"""Assemble the recorded shots, the narration and burned-in subtitles into one MP4 (plus an .srt file).

Usage: python -I assemble.py <out.mp4>
Reads capture/<shot>/shot.json + frames, voice/<id>.wav + durations.json and lines.json from this folder.
Timeline rules:
  - each shot runs from its "start" to its "end" marker;
  - AI waits ("cut:start" .. "cut:end") are removed, keeping the moment just before the answer appears;
  - after a page shot's last narration line, the scroll-up and menu click play 1.8 times faster;
  - shots join with a short crossfade; the video fades in and out.
"""
import bisect
import json
import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]).resolve()
OUT.parent.mkdir(parents=True, exist_ok=True)
FPS, W, H, RATE = 30, 1920, 1080, 44100
SHOTS = ["title", "try", "evaluation", "backtests", "debate", "replay", "ask", "outro"]
NAV_SHOTS = {"try", "evaluation", "backtests", "debate", "replay"}
TAIL_SPEED, XFADE, FADE_IN, FADE_OUT = 2.2, 0.25, 0.4, 0.7
LINES = {x["id"]: x["show"] for x in json.loads((HERE / "lines.json").read_text(encoding="utf-8"))}
DUR = json.loads((HERE / "voice" / "durations.json").read_text())

# ---------------------------------------------------------------- timeline
timeline = []   # (out_start, out_end, shot, src_a, speed)
says = []       # (out_time, line id)
t_out = 0.0
shot_frames = {}
for shot in SHOTS:
    d = json.loads((HERE / "capture" / shot / "shot.json").read_text())
    frames = sorted(d["frames"], key=lambda f: f["ts"])
    shot_frames[shot] = ([f["ts"] for f in frames], [HERE / "capture" / shot / "frames" / f["f"] for f in frames])
    m = d["markers"]
    start = next(x["t"] for x in m if x["name"] == "start")
    end = next(x["t"] for x in m if x["name"] == "end")
    if shot in NAV_SHOTS:
        # End the clip right after the menu click, before the next page starts to draw: record.mjs marks "nav" at the
        # click (older recordings: the click is 0.25 s before "end", and the new page appears about 0.1 s before "end").
        nav = next((x["t"] for x in m if x["name"] == "nav"), None)
        end = min(end, nav + 0.2 if nav is not None else end - 0.22)
    cuts, open_cut = [], None
    for x in m:
        if x["name"] == "cut:start":
            open_cut = x["t"]
        elif x["name"] == "cut:end" and open_cut is not None:
            if x["t"] - 0.25 > open_cut:
                cuts.append((open_cut, x["t"] - 0.25))
            open_cut = None
    shot_says = [(x["t"], x["name"][4:]) for x in m if x["name"].startswith("say:")]
    pieces, a = [], start
    for c0, c1 in cuts:
        pieces.append([a, c0, 1.0])
        a = c1
    pieces.append([a, end, 1.0])
    if shot in NAV_SHOTS and shot_says:
        t_last, lid = shot_says[-1]
        tail = t_last + DUR[lid] + 0.3
        last = pieces[-1]
        if last[0] < tail < last[1]:
            pieces[-1] = [last[0], tail, 1.0]
            pieces.append([tail, last[1], TAIL_SPEED])
    # A long silent stretch of clicking between two narration lines plays 1.6 times faster.
    for (t1, l1), (t2, _) in zip(shot_says, shot_says[1:]):
        g0, g1 = t1 + DUR[l1] + 0.5, t2 - 0.4
        cut_inside = sum(max(0.0, min(c1, g1) - max(c0, g0)) for c0, c1 in cuts)
        if (g1 - g0) - cut_inside > 4.5:
            split = []
            for a, b, sp in pieces:
                if sp == 1.0 and a < g1 and b > g0:
                    for p in ([a, max(a, g0), 1.0], [max(a, g0), min(b, g1), 1.6], [min(b, g1), b, 1.0]):
                        if p[1] - p[0] > 1e-3:
                            split.append(p)
                else:
                    split.append([a, b, sp])
            pieces = split
    for a, b, sp in pieces:
        dur = (b - a) / sp
        timeline.append((t_out, t_out + dur, shot, a, sp))
        for ts, lid in shot_says:
            if a <= ts < b:
                says.append((t_out + (ts - a) / sp, lid))
        t_out += dur
TOTAL = t_out
says.sort()
print(f"total {TOTAL:.2f} s, {len(says)} narration lines")

# ---------------------------------------------------------------- narration track (lines never overlap)
audio = np.zeros(int(TOTAL * RATE) + RATE, dtype=np.float32)
placed, prev_end = [], 0.0
for t, lid in says:
    t = max(t, prev_end + 0.12)
    with wave.open(str(HERE / "voice" / f"{lid}.wav")) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    i = int(t * RATE)
    audio[i:i + len(x)] += x[: len(audio) - i]
    placed.append((t, t + len(x) / RATE, lid))
    prev_end = t + len(x) / RATE
audio = audio[: int(TOTAL * RATE)]
fade = np.ones_like(audio)
n_in, n_out = int(0.05 * RATE), int(FADE_OUT * RATE)
fade[:n_in] = np.linspace(0, 1, n_in)
fade[-n_out:] = np.linspace(1, 0, n_out)
audio *= fade
wav_path = OUT.with_suffix(".narration.wav")
with wave.open(str(wav_path), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
    w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())

# ---------------------------------------------------------------- subtitles
cues = []
for k, (t0, t1, lid) in enumerate(placed):
    nxt = placed[k + 1][0] if k + 1 < len(placed) else TOTAL
    cues.append((max(0, t0 - 0.05), min(t1 + 0.35, nxt - 0.06), LINES[lid]))

def srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

OUT.with_suffix(".srt").write_text("".join(f"{i + 1}\n{srt_time(a)} --> {srt_time(b)}\n{s}\n\n" for i, (a, b, s) in enumerate(cues)), encoding="utf-8")

FONT = ImageFont.truetype("C:/Windows/Fonts/seguisb.ttf", 40)

def caption_image(text):
    """White text on a dark ink panel, wrapped to at most two lines of 1500 px."""
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        trial = (cur + " " + w_).strip()
        if FONT.getlength(trial) > 1500 and cur:
            lines.append(cur); cur = w_
        else:
            cur = trial
    lines.append(cur)
    if len(lines) > 2:   # rebalance into two lines
        best = min(range(1, len(words)), key=lambda i: abs(FONT.getlength(" ".join(words[:i])) - FONT.getlength(" ".join(words[i:]))))
        lines = [" ".join(words[:best]), " ".join(words[best:])]
    # Centre on the visible letters, not on the font's line box: equal space from the panel's top edge to the
    # top of the capitals and from the last baseline to the bottom edge (descenders hang into that space),
    # and equal space left and right of each line's ink.
    lh, pad_x, pad_v = 54, 30, 28
    cap_h = -FONT.getbbox("H", anchor="ls")[1]
    inks = [ink_columns(s) for s in lines]                        # drawn pixels, relative to the drawing point
    tw = max(r - l for l, r in inks)
    img = Image.new("RGBA", (tw + 2 * pad_x, cap_h + (len(lines) - 1) * lh + 2 * pad_v), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    dr.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=14, fill=(20, 33, 61, 222))
    for i, (s, (l, r)) in enumerate(zip(lines, inks)):
        x = round((img.width - (r - l)) / 2) - l
        dr.text((x, pad_v + cap_h + i * lh), s, font=FONT, fill=(255, 255, 255, 255), anchor="ls")
    return img


def ink_columns(s):
    """First and one-past-last pixel column the line actually paints, relative to its drawing point."""
    l, t, r, b = FONT.getbbox(s, anchor="ls")
    pad = 8
    tmp = Image.new("L", (r - l + 2 * pad, b - t + 2 * pad), 0)
    ImageDraw.Draw(tmp).text((pad - l, pad - t), s, font=FONT, fill=255, anchor="ls")
    cols = np.nonzero((np.asarray(tmp) > 60).any(axis=0))[0]
    return int(cols.min()) - pad + l, int(cols.max()) + 1 - pad + l

caps = [(a, b, caption_image(s)) for a, b, s in cues]

# ---------------------------------------------------------------- video frames
cache = {}
def load(path):
    if path not in cache:
        if len(cache) > 6:
            cache.pop(next(iter(cache)))
        im = Image.open(path).convert("RGB")
        if im.size != (W, H):
            im = im.resize((W, H), Image.LANCZOS)
        cache[path] = im
    return cache[path]

def frame_at(T):
    i = max(0, bisect.bisect_right([s[0] for s in timeline], T) - 1)
    o0, o1, shot, a, sp = timeline[i]
    src = a + (min(T, o1) - o0) * sp
    tss, paths = shot_frames[shot]
    j = max(0, bisect.bisect_right(tss, src) - 1)
    return shot, load(paths[j])

shot_start = {}
for o0, o1, shot, a, sp in timeline:
    shot_start.setdefault(shot, o0)

ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
proc = subprocess.Popen([ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                         "-i", str(wav_path), "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                         "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-movflags", "+faststart", str(OUT)], stdin=subprocess.PIPE)
n_frames = int(round(TOTAL * FPS))
black = Image.new("RGB", (W, H), (0, 0, 0))
prev_shot, prev_clean, xfade_from = None, None, None
for k in range(n_frames):
    T = k / FPS
    shot, clean = frame_at(T)
    if prev_shot is not None and shot != prev_shot:
        xfade_from = prev_clean
    prev_shot, prev_clean = shot, clean
    im = clean.copy()
    since = T - shot_start[shot]
    if xfade_from is not None and since < XFADE:
        im = Image.blend(xfade_from, im, since / XFADE)
    for a, b, cap in caps:
        if a <= T < b:
            im.paste(cap, ((W - cap.width) // 2, H - 64 - cap.height), cap)
            break
    if T < FADE_IN:
        im = Image.blend(black, im, T / FADE_IN)
    elif T > TOTAL - FADE_OUT:
        im = Image.blend(black, im, max(0.0, (TOTAL - T) / FADE_OUT))
    proc.stdin.write(im.tobytes())
proc.stdin.close()
proc.wait()
print("wrote", OUT, f"{OUT.stat().st_size / 1e6:.1f} MB,", n_frames, "frames")
print("subtitles", OUT.with_suffix(".srt"))
