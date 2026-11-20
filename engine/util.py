"""Small shared helpers: config, ffmpeg runner, probing."""
from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = ROOT / "assets" / "fonts"
FONT_NAME = "Anton"


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def style(overrides: dict | None = None) -> dict:
    """Default style from config.yaml, overridden by panel settings."""
    s = dict(config()["style"])
    for k, v in (overrides or {}).items():
        if k in s and v is not None and v != "":
            d = s[k]
            try:
                if isinstance(d, bool):
                    s[k] = _bool(v)
                elif isinstance(d, int):
                    s[k] = int(float(v))
                elif isinstance(d, float):
                    s[k] = float(v)
                else:
                    s[k] = str(v)
            except (TypeError, ValueError):
                pass
    s["caption_words"] = 1 if int(s["caption_words"]) <= 1 else 2
    s["zoom_strength"] = min(1.6, max(0.3, float(s["zoom_strength"])))
    s["cam_height"] = min(0.5, max(0.25, float(s["cam_height"])))
    s["max_len"] = min(170.0, max(10.0, float(s["max_len"])))
    return s


def _bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "да")
    return bool(v)


def run(cmd: list[str], *, input: bytes | None = None) -> str:
    r = subprocess.run(cmd, capture_output=True, input=input)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace")[-3000:]
        raise RuntimeError(f"command failed: {' '.join(map(str, cmd[:8]))} ...\n{err}")
    return r.stderr.decode("utf-8", "replace")


def probe(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True)
    info = json.loads(r.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    dur = float(info["format"].get("duration") or (v or {}).get("duration") or 0)
    fps = 30.0
    if v and v.get("avg_frame_rate") and v["avg_frame_rate"] != "0/0":
        n, d = v["avg_frame_rate"].split("/")
        fps = float(n) / float(d or 1)
    return {
        "duration": dur,
        "width": int(v["width"]) if v else 0,
        "height": int(v["height"]) if v else 0,
        "fps": fps,
        "has_audio": a is not None,
        "size": int(info["format"].get("size") or 0),
    }


def hex_to_bgr(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)


def hex_to_ass(h: str) -> str:
    """#RRGGBB -> &H00BBGGRR (ASS colour)."""
    b, g, r = hex_to_bgr(h)
    return f"&H00{b:02X}{g:02X}{r:02X}"
