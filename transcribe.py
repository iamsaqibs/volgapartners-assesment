"""Transcribe a WAV/MP3 file to JSON: python transcribe.py audio.mp3 [--model base] [-o out.json]"""
import argparse
import json
import sys
from pathlib import Path

from faster_whisper import WhisperModel  # decodes wav/mp3/etc. itself via bundled PyAV

ALLOWED = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}


def transcribe(path: Path, model_size: str = "base") -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() not in ALLOWED:
        raise ValueError(f"unsupported format {path.suffix!r}; allowed: {sorted(ALLOWED)}")
    # shortcut: model reloaded per call, fine for a script; cache it if run as a service
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segs, info = model.transcribe(str(path), vad_filter=True)  # VAD skips silence
    segments = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segs]
    return {
        "file": path.name,
        "language": info.language,
        "duration_s": round(info.duration, 2),
        "text": " ".join(s["text"] for s in segments),
        "segments": segments,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("audio", type=Path)
    p.add_argument("--model", default="base")
    p.add_argument("-o", "--output", type=Path)
    a = p.parse_args()
    try:
        out = json.dumps(transcribe(a.audio, a.model), indent=2, ensure_ascii=False)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(f"error: {e}")
    a.output.write_text(out) if a.output else print(out)
