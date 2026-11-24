"""Natural narration for the Idea2Alpha intro video, made with Gemini text-to-speech.

Run it in your own PowerShell window after setting GEMINI_API_KEY there, the same way as for the
pipeline runs. Never paste the key into a chat, a screenshot or a file.

    python -I gemini_voice.py lines.json voice_gemini
    python -I gemini_voice.py lines.json voice_gemini --voice Charon --force    (another voice, redo all)

It writes one WAV per narration line into the output folder and prints how long each one is.
Lines that already have a WAV are skipped, so a run that stops halfway can simply be started again.
Voices to try (Google's descriptions): Kore firm, Charon informative, Achird friendly, Sulafat warm,
Iapetus clear, Puck upbeat, Sadaltager knowledgeable.
"""
import argparse
import base64
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

API = "https://generativelanguage.googleapis.com/v1beta"
MODELS = ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-2.5-flash-preview-tts"]
STYLE = "warm, clear and confident, like a friendly presenter narrating a short product demo, at a brisk, natural pace"


def post(url, body, key):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"x-goog-api-key": key, "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return e.code, {}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": {"message": str(e)}}


def find_audio(j):
    """Interactions API: steps[].content[] holds an audio block; generateContent: candidates[].content.parts[].inlineData."""
    for step in j.get("steps") or []:
        for c in step.get("content") or []:
            if isinstance(c, dict) and c.get("data"):
                kind = f"{c.get('type', '')} {c.get('mime_type', '')} {c.get('mimeType', '')}"
                if "audio" in kind:
                    return base64.b64decode(c["data"]), c.get("mime_type") or c.get("mimeType") or ""
    for cand in j.get("candidates") or []:
        for p in (cand.get("content") or {}).get("parts") or []:
            d = p.get("inlineData") or p.get("inline_data")
            if d and d.get("data"):
                return base64.b64decode(d["data"]), d.get("mimeType") or d.get("mime_type") or ""
    return None, ""


def as_wav(data, mime):
    if data[:4] == b"RIFF":
        return data
    m = re.search(r"rate=(\d+)", mime or "")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(int(m.group(1)) if m else 24000)
        w.writeframes(data)
    return buf.getvalue()


def speak(text, voice, model, key):
    """Current Interactions API first; the older generateContent form if that is refused."""
    body = {"model": model,
            "input": [{"type": "user_input", "content": [{"type": "text", "text": text,
                                                          "annotations": [{"type": "speech_metadata", "style": STYLE}]}]}],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": voice}]}}
    status, j = post(f"{API}/interactions", body, key)
    if status == 200:
        audio, mime = find_audio(j)
        if audio:
            return as_wav(audio, mime), status, j
    if status in (400, 404):
        legacy = {"contents": [{"parts": [{"text": f"Say this in a {STYLE} voice: {text}"}]}],
                  "generationConfig": {"responseModalities": ["AUDIO"],
                                       "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
        s2, j2 = post(f"{API}/models/{model}:generateContent", legacy, key)
        if s2 == 200:
            audio, mime = find_audio(j2)
            if audio:
                return as_wav(audio, mime), s2, j2
        if s2 not in (400, 404):
            status, j = s2, j2
    return None, status, j


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lines"); ap.add_argument("out")
    ap.add_argument("--voice", default="Kore"); ap.add_argument("--model", default=MODELS[0])
    ap.add_argument("--gap", type=float, default=4.0, help="seconds to wait between requests")
    ap.add_argument("--force", action="store_true", help="redo lines that already have a WAV")
    a = ap.parse_args()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        sys.exit("GEMINI_API_KEY is not set in this window. Set it the same way as for the pipeline runs, then run this again.")
    lines = json.loads(Path(a.lines).read_text(encoding="utf-8"))
    out = Path(a.out); out.mkdir(exist_ok=True)
    model, total = a.model, 0.0
    print(f"voice {a.voice}, model {model}, {len(lines)} lines -> {out.resolve()}")
    for ln in lines:
        dest = out / f"{ln['id']}.wav"
        if dest.exists() and not a.force:
            with wave.open(str(dest)) as w:
                total += w.getnframes() / w.getframerate()
            print(f"  {ln['id']}  already done"); continue
        for attempt in range(6):
            wav, status, j = speak(ln["say"], a.voice, model, key)
            if wav:
                break
            msg = str(((j or {}).get("error") or {}).get("message", ""))[:300]
            if status == 429 or (status == 0 and attempt < 2):
                wait = 30 * (attempt + 1)
                print(f"  {ln['id']}  busy or rate-limited, waiting {wait} s"); time.sleep(wait); continue
            if status == 404 and model in MODELS and MODELS.index(model) + 1 < len(MODELS):
                model = MODELS[MODELS.index(model) + 1]
                print(f"  model not available, trying {model}"); continue
            sys.exit(f"Gemini did not return audio for {ln['id']} (HTTP {status}): {msg}")
        else:
            sys.exit(f"Gave up on {ln['id']} after several tries; run the same command again later to continue.")
        dest.write_bytes(wav)
        with wave.open(str(dest)) as w:
            secs = w.getnframes() / w.getframerate()
        total += secs
        print(f"  {ln['id']}  {secs:4.1f} s")
        time.sleep(a.gap)
    print(f"done: {len(lines)} lines, {total:.1f} s of speech, model {model}, voice {a.voice}")


if __name__ == "__main__":
    main()
