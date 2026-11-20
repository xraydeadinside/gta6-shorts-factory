"""Speech -> words with timings (faster-whisper on CPU, free, runs inside GitHub Actions)."""
from __future__ import annotations

from pathlib import Path

from .util import config

_models: dict = {}


def transcribe(audio_path: Path) -> list[dict]:
    """Return [{"w": word, "s": start, "e": end}] in seconds of the downloaded source."""
    from faster_whisper import WhisperModel

    cfg = config()["transcribe"]
    key = (cfg["model"], cfg["compute_type"])
    if key not in _models:
        _models[key] = WhisperModel(cfg["model"], device="cpu", compute_type=cfg["compute_type"])
    segments, _ = _models[key].transcribe(
        str(audio_path), language="en", word_timestamps=True, vad_filter=True,
        beam_size=5, condition_on_previous_text=False)
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"w": text, "s": round(float(w.start), 3), "e": round(float(w.end), 3)})
    return words


def numbered(words: list[dict]) -> str:
    """Compact transcript for the AI: '[12.3s] 40:word 41:word ...'."""
    if not words:
        return "(no speech)"
    lines, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        if len(cur) >= 12 or w["w"][-1:] in ".?!":
            lines.append(f"[{words[cur[0]]['s']:.1f}s] " + " ".join(f"{j}:{words[j]['w']}" for j in cur))
            cur = []
    if cur:
        lines.append(f"[{words[cur[0]]['s']:.1f}s] " + " ".join(f"{j}:{words[j]['w']}" for j in cur))
    return "\n".join(lines)
