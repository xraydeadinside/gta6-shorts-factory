"""Reference videos the owner likes (/ref link): the AI watches them and writes down the editing style,
so our edits can copy what works. YouTube links are watched by Gemini directly (no download needed);
other links (Twitch clip, TikTok...) are downloaded first (first 3 minutes)."""
from __future__ import annotations

from pathlib import Path

from . import brain, fetch

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "format": {"type": "STRING", "enum": ["short", "long"], "description": "vertical Short or horizontal long video"},
        "title": {"type": "STRING", "description": "the video's title or a 4-6 word name for it"},
        "duration": {"type": "NUMBER", "description": "seconds"},
        "hook": {"type": "STRING", "description": "what exactly happens in the first 1-3 s (visual + audio + text)"},
        "pacing": {"type": "STRING", "description": "how often the picture changes: cuts, zooms, jump cuts; average seconds per shot"},
        "layout": {"type": "STRING", "description": "where the webcam / face is, where the gameplay is, crop, split-screen proportions"},
        "camera": {"type": "STRING", "description": "zooms, pans, shakes, speed ramps, freeze frames, slow motion"},
        "captions": {"type": "STRING", "description": "subtitle style: words per caption, font look, size, colours, outline, position, animation, highlighted words"},
        "text_overlays": {"type": "STRING", "description": "titles, labels, emojis, arrows, counters: when and how"},
        "sound": {"type": "STRING", "description": "sound effects (which and where), music (genre, volume), voice-over, how the original audio is mixed"},
        "story": {"type": "STRING", "description": "structure: setup, escalation, punchline, reaction, payoff; is context explained"},
        "ending": {"type": "STRING", "description": "how it ends: loop, CTA, cliffhanger, cut"},
        "why_it_works": {"type": "STRING", "description": "the 2-3 main reasons people watch it to the end"},
        "rules_en": {"type": "ARRAY", "items": {"type": "STRING"},
                     "description": "6-12 concrete, copyable editing rules for our GTA 6 stream-clip channel"},
        "rules_ru": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "the same rules in simple Russian"},
        "summary_ru": {"type": "STRING", "description": "Russian, 3-5 short sentences: what this video does well"},
    },
    "required": ["format", "title", "duration", "hook", "pacing", "layout", "camera", "captions", "text_overlays",
                 "sound", "story", "ending", "why_it_works", "rules_en", "rules_ru", "summary_ru"],
}

PROMPT = """You are a senior short-form video editor. The owner of a GTA 6 stream-clips channel (YouTube Shorts and
long videos, English-speaking audience) likes this video and wants our edits to learn from it.
Watch AND listen carefully, second by second. Describe the EDITING, not the topic: be concrete and measurable
(seconds, sizes as share of the screen, colours, how many words per caption, how many cuts in the first 10 s).
Then turn it into rules we can copy when we edit Twitch stream moments of GTA 6 (webcam + gameplay).
{note}"""


def analyse(job: dict, wd: Path) -> dict:
    link = fetch.parse_url(job["url"])
    note = f"The owner's comment about it: «{job['note']}»" if job.get("note") else ""
    prompt = PROMPT.format(note=note)
    if link["kind"] == "youtube":
        part = {"file_data": {"file_uri": link["url"].split("&")[0]}}
        try:
            return brain.generate([part, {"text": prompt}], SCHEMA, temperature=0.3, media_low=True, patience=True)
        except Exception as e:  # noqa: BLE001 - e.g. very long video: fall through to an explicit error
            raise fetch.UserError(f"Gemini не смог посмотреть это видео: {str(e)[:200]}") from e
    dl = fetch.download(job["url"], wd, 0.0 if link.get("t") is None else None, 180.0 if link.get("t") is None else None)
    proxy = brain.make_proxy(dl["path"], wd / "ref_proxy.mp4")
    out = brain.generate([brain.video_part(proxy), {"text": prompt}], SCHEMA, temperature=0.3, patience=True)
    out.setdefault("title", dl["meta"].get("title") or "")
    return out
