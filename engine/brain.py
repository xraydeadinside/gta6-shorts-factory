"""AI editor (Gemini, free tier): watches the clip, scores it, cuts it into story beats, picks camera
moves, writes YouTube Shorts metadata and explains the edit in plain Russian."""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import requests

from .util import config, run

API = "https://generativelanguage.googleapis.com/v1beta"

RULES = """
YOUTUBE SHORTS RULES (from the channel's YouTube playbook — follow them):
- Hook lands in the first 1-3 seconds. The very first frame must stop the scroll. A great trick: open with a
  1-2.5 s TEASER of the most intense instant (role "hook", may be copied from later in the clip), then go back.
- Retention is king (70%+ completion gets pushed). Cut EVERYTHING boring: dead air, menus, walking around,
  repeated sentences, loading. Visual change at least every 3 seconds.
- STORY FIRST: a stranger who never saw the stream must understand what is going on and why it is funny or crazy.
  Keep the setup that explains the situation (what the streamer is trying to do). Cut dead air, NOT context.
- Sweet spots: ~20-45 s for a complete mini-story (setup -> turn -> reaction), up to ~55 s if the story needs it.
  Never cut a story down to a meaningless 10-second fragment.
- End right after the payoff; ideally the last beat loops back naturally into the first frame (rewatch = views).
- Title: 4-6 words, 20-40 characters, keyword first ("GTA 6 ..."), curiosity or emotion, no hashtags, no clickbait lies.
  Give 3 genuinely different titles: [0] search-optimised (keyword-forward), [1] browse/curiosity-driven, [2] hybrid.
- Description: first 125 characters carry everything (keyword "GTA 6" in the first 25 words, what happens + streamer).
  Then 1-2 short lines of context. Credit the streamer. No links except the streamer's channel.
- 3-5 hashtags, most specific first, broadest last (#GTA6 ... #Shorts). 10-15 tags, priority order, total < 500 chars.
- No background music is added (Shorts revenue share drops with music).
"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "score": {"type": "INTEGER", "description": "1-10, how good this moment is for a viral Short"},
        "verdict_ru": {"type": "STRING", "description": "one short sentence in Russian: worth it or not and why"},
        "streamer": {"type": "STRING", "description": "twitch login of the streamer if visible/said, else empty"},
        "webcam": {"type": "OBJECT", "properties": {
            "present": {"type": "BOOLEAN"},
            "box": {"type": "ARRAY", "items": {"type": "NUMBER"},
                    "description": "[x1,y1,x2,y2] normalised 0..1 of the streamer webcam overlay"}},
            "required": ["present"]},
        "parts": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "role": {"type": "STRING", "enum": ["hook", "setup", "punchline", "reaction", "payoff"]},
            "start": {"type": "NUMBER"}, "end": {"type": "NUMBER"},
            "shot": {"type": "STRING", "enum": ["game", "react"]},
            "camera": {"type": "STRING", "enum": ["push", "pan", "whip", "hold"]},
            "x": {"type": "NUMBER", "description": "0..1 where the action is in the GAME picture (full frame coords)"},
            "y": {"type": "NUMBER"},
            "zoom": {"type": "NUMBER", "description": "1.0 .. 1.8"},
            "note": {"type": "STRING", "description": "Russian, 3-8 words: what happens here"}},
            "required": ["role", "start", "end", "shot", "camera", "x", "y", "zoom", "note"]}},
        "punch_words": {"type": "ARRAY", "items": {"type": "INTEGER"},
                        "description": "transcript word indices of the punchline words to highlight"},
        "hook_text": {"type": "STRING", "description": "SCREAMING on-screen title, 2-5 words, English, caps"},
        "sfx": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "t": {"type": "NUMBER", "description": "clip seconds of the game action"},
            "kind": {"type": "STRING", "enum": ["impact", "riser", "glitch", "swish", "pop"]}},
            "required": ["t", "kind"]},
            "description": "0-4 sound effects on GAME ACTIONS (crash, explosion, hit, fall, absurd physics)"},
        "hook_accent": {"type": "STRING", "description": "one word of hook_text to colour"},
        "shouts": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "t": {"type": "NUMBER", "description": "source seconds"},
            "text": {"type": "STRING", "description": "1-3 word pop-up like 'NO WAY', 'BRO 💀'"}},
            "required": ["t", "text"]}},
        "titles": {"type": "ARRAY", "items": {"type": "STRING"}},
        "description": {"type": "STRING"},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "flow_prompt": {"type": "STRING", "description": "English prompt for Google Flow (Veo): 6-8 s vertical 9:16 "
                        "cinematic intro teasing this moment, GTA 6 Vice City vibe, no real people, no logos, no text"},
        "summary_en": {"type": "STRING", "description": "2 sentences: what happens in the clip"},
        "explanation_ru": {"type": "STRING", "description": "Russian, simple words, 4-8 short lines: how you cut it, "
                           "what you removed and why, where the camera moves, why this hook"},
        "viewed_pct": {"type": "INTEGER", "description": "predicted 'viewed vs swiped away' %"},
        "risk_ru": {"type": "STRING", "description": "Russian, main retention risk and a fix, 1 line"},
    },
    "required": ["score", "verdict_ru", "webcam", "parts", "punch_words", "hook_text", "shouts", "titles",
                 "description", "tags", "hashtags", "flow_prompt", "summary_en", "explanation_ru", "viewed_pct",
                 "risk_ru"],
}


def make_proxy(src: Path, out: Path) -> Path:
    """Small low-fps copy for the AI (fast upload, same timings)."""
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", "fps=2,scale=640:-2",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "33", "-c:a", "aac", "-b:a", "48k", "-ac", "1", str(out)])
    return out


def _key() -> str:
    k = os.environ.get("GEMINI_API_KEY", "").strip()
    if not k:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return k


def generate(parts: list[dict], schema: dict | None = None, temperature: float = 0.6, *,
             media_low: bool = False, patience: bool = False) -> dict:
    """Call Gemini with model fallbacks and retries. Returns parsed JSON.

    media_low: fewer tokens per video frame (long videos). patience: on rate limits wait up to a few
    minutes instead of giving up (the free tier limits tokens per minute)."""
    cfg = config()["gemini"]
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"}}
    if schema:
        body["generationConfig"]["responseSchema"] = schema
    if media_low:
        body["generationConfig"]["mediaResolution"] = "MEDIA_RESOLUTION_LOW"
    last = None
    attempts = 6 if patience else 3
    for attempt in range(attempts):
        if attempt and patience:
            time.sleep(min(150, 40 * attempt))
        for model in cfg["models"]:
            try:
                r = requests.post(f"{API}/models/{model}:generateContent", json=body,
                                  headers={"x-goog-api-key": _key()}, timeout=cfg.get("timeout", 240))
                if r.status_code in (429, 500, 503, 504):
                    last = f"{model}: HTTP {r.status_code}"
                    continue
                r.raise_for_status()
                data = r.json()
                text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
                out = json.loads(text)
                out["_model"] = model
                return out
            except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError) as e:
                last = f"{model}: {e}"
        if not patience:
            time.sleep(8 * (attempt + 1))
    raise RuntimeError(f"Gemini недоступен: {last}")


def upload_file(path: Path, mime: str = "video/mp4") -> dict:
    """Gemini Files API (for videos too big to send inline). Waits until ACTIVE."""
    size = path.stat().st_size
    start = requests.post(
        f"{API.replace('/v1beta', '')}/upload/v1beta/files",
        headers={"x-goog-api-key": _key(), "X-Goog-Upload-Protocol": "resumable",
                 "X-Goog-Upload-Command": "start", "X-Goog-Upload-Header-Content-Length": str(size),
                 "X-Goog-Upload-Header-Content-Type": mime, "Content-Type": "application/json"},
        json={"file": {"display_name": path.name}}, timeout=60)
    start.raise_for_status()
    url = start.headers["X-Goog-Upload-URL"]
    up = requests.post(url, headers={"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"},
                       data=path.read_bytes(), timeout=300)
    up.raise_for_status()
    f = up.json()["file"]
    for _ in range(60):
        if f.get("state") == "ACTIVE":
            return f
        time.sleep(3)
        f = requests.get(f"{API}/{f['name']}", headers={"x-goog-api-key": _key()}, timeout=30).json()
    raise RuntimeError("Gemini не обработал видео вовремя")


def video_part(proxy: Path, force_upload: bool = False) -> dict:
    if not force_upload and proxy.stat().st_size < 15 * 1024 * 1024:
        return {"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(proxy.read_bytes()).decode()}}
    f = upload_file(proxy)
    return {"file_data": {"mime_type": "video/mp4", "file_uri": f["uri"]}}


def analyse(proxy: Path, transcript: str, duration: float, *, streamer: str | None, cam_hint: dict | None,
            source_title: str | None, wish: str | None = None, previous: dict | None = None,
            brief: dict | None = None, min_len: float = 20, max_len: float = 58) -> dict:
    hint = "none found by the detector"
    if cam_hint and cam_hint.get("present"):
        hint = f"detector thinks the webcam is at normalised box {cam_hint.get('norm')} (confirm or correct)"
    prompt = f"""You are a top YouTube Shorts editor for a GTA 6 clips channel (English-speaking streamers,
the audience is English-speaking gamers). You get a stream clip ({duration:.1f} s) and its transcript with word
indices and times. Make a short-attention-span edit.

Streamer: {streamer or 'unknown'}. Source title: {source_title or '-'}.
Webcam: {hint}.
{RULES}
EDIT PLAN:
- Split the clip into beats: hook -> setup -> punchline -> reaction -> payoff (skip roles that do not exist,
  repeat roles if needed). Times are seconds in THIS clip (0..{duration:.1f}), parts in playback order.
- Remove boring stretches by simply not including them. Each part >= 0.6 s.
- LENGTH: the parts together must last at least {min(min_len, duration):.0f} s and at most {max_len:.0f} s.
  If the story is short, keep more setup before it and more reaction after it — never deliver a fragment.
- shot "game": webcam on top, gameplay below with a virtual camera. shot "react": SHORT (0.6-2 s) big close-up
  of the streamer's face right after a punchline or a big reaction — only if a webcam exists.
- camera: "push" fast zoom into the action, "pan" glide to where the action moves, "whip" very fast jump to a
  new spot, "hold" calm framing. x,y = where the action is in the full frame (0..1), zoom 1.0-1.8.
  Never aim at the webcam overlay area.
- punch_words: transcript indices of the punchline words (they get coloured).
- shouts: 2-5 short pop-up words at peak moments (times in clip seconds).
- sfx: 0-4 sound effects on what happens IN THE GAME: "impact" on a crash / explosion / hit / hard landing,
  "glitch" on absurd broken physics, "riser" where tension peaks (t = the peak), "swish" on something flying past.
  Text pop-ups and the punchline already get sounds automatically — do not duplicate them. Fewer is better.
- hook_text: the big title on screen for the first 3 s. It must tell the SITUATION in plain words
  (e.g. "SPEED TRIES TO STEAL A JET") so the viewer instantly knows what to watch for, and create curiosity
  without spoiling the outcome.
- explanation_ru: the owner is not a video editor — explain your edit in simple Russian.
"""
    if brief:
        prompt += ("\nSCOUT BRIEF (another AI watched the whole stream and picked this moment):\n"
                   f"- what happens: {brief.get('what_ru', '')}\n- situation: {brief.get('context_en', '')}\n"
                   f"- why it works: {brief.get('why_ru', '')}\nKeep this whole story readable.\n")
    if wish:
        prompt += f"\nTHE OWNER ASKED FOR A RE-EDIT. Their wish (Russian, follow it): «{wish}»\n"
    if previous:
        prompt += "\nPrevious plan (change it according to the wish):\n" + json.dumps(
            {k: previous.get(k) for k in ("parts", "hook_text", "titles")}, ensure_ascii=False)[:6000] + "\n"
    prompt += "\nTRANSCRIPT:\n" + transcript[:30000]
    return generate([video_part(proxy), {"text": prompt}], SCHEMA)


def new_titles(summary: str, streamer: str | None, old: list[str], wish: str | None = None) -> list[str]:
    prompt = (f"GTA 6 Shorts clip: {summary}\nStreamer: {streamer}\nOld titles: {old}\n{RULES}\n"
              f"Write 3 NEW different titles (search / curiosity / hybrid). {('Wish: ' + wish) if wish else ''}"
              '\nReturn JSON {"titles": [..3 strings..]}')
    return generate([{"text": prompt}], temperature=0.95).get("titles", [])[:3]
