"""Prepare narration clips for the video: trim silence, even out loudness, convert to 44.1 kHz mono.

Usage: python -I trim_voice.py <raw folder> <out folder> [--tempo 1.05]
Writes <out>/<id>.wav and <out>/durations.json (seconds per line), which record.mjs and assemble.py read.
--tempo speeds speech up (or slows it down) without changing the pitch, to fit the video under two minutes.
"""
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np

args = [a for a in sys.argv[1:] if not a.startswith("--")]
tempo = float(sys.argv[sys.argv.index("--tempo") + 1]) if "--tempo" in sys.argv else 1.0
src, dst = Path(args[0]), Path(args[1])
dst.mkdir(exist_ok=True)
ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
durs = {}
for f in sorted(src.glob("*.wav")):
    with wave.open(str(f)) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    if width != 2:
        sys.exit(f"{f.name}: expected 16-bit audio")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    thr = 0.02 * np.abs(x).max()
    loud = np.nonzero(np.abs(x) > thr)[0]
    pad = int(0.06 * rate)
    x = x[max(0, loud[0] - pad): min(len(x), loud[-1] + pad)]
    x = x / np.abs(x).max() * 0.85 * 32767          # peak at about -1.4 dBFS
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "in.wav"
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(x.astype(np.int16).tobytes())
        filt = ["-af", f"atempo={tempo}"] if abs(tempo - 1.0) > 1e-6 else []
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(tmp), *filt, "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(dst / f.name)], check=True)
    # Set the level after resampling, which can push peaks a little higher than they were.
    with wave.open(str(dst / f.name)) as w:
        params, y = w.getparams(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    y = y / np.abs(y).max() * 0.85 * 32767
    with wave.open(str(dst / f.name), "wb") as w:
        w.setparams(params)
        w.writeframes(y.astype(np.int16).tobytes())
    with wave.open(str(dst / f.name)) as w:
        durs[f.stem] = round(w.getnframes() / w.getframerate(), 3)
(dst / "durations.json").write_text(json.dumps(durs, indent=1))
print(f"total speech {sum(durs.values()):.1f} s (tempo {tempo});", " ".join(f"{k}={v:.1f}" for k, v in durs.items()))
