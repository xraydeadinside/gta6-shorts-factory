"""Topic long videos ("15 Beginner Mistakes in GTA 6", "Secrets you missed"...), not tied to streamers.

1. research(): YouTube Data API finds the most watched recent videos on the topic; Gemini watches them
   (by link, nothing downloaded) and writes down every fact/tip WITH the seconds where it is shown.
2. A fact goes into the script only if 2+ videos show it (or one shows it very clearly), so we don't
   repeat rumours. The script gets 2-3 short shots (<= 8 s) per point from those videos.
3. The laptop helper downloads only those shots (YouTube blocks GitHub servers); then longform edits it.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import requests

from . import brain, fetch

FACTS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "relevant": {"type": "BOOLEAN", "description": "is this video really about the topic"},
        "facts": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "fact": {"type": "STRING", "description": "one concrete fact / tip / mistake, English, 1 sentence"},
            "start": {"type": "NUMBER", "description": "second where it is SHOWN on screen (gameplay)"},
            "end": {"type": "NUMBER", "description": "end second, 3-10 s after start"},
            "visual": {"type": "STRING", "description": "what exactly is visible in that shot"},
            "sure": {"type": "INTEGER", "description": "1-5 how certain it is a real in-game fact (5 = shown clearly)"}},
            "required": ["fact", "start", "end", "visual", "sure"]}},
    },
    "required": ["relevant", "facts"],
}

TOPIC_SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"}, "alt_titles": {"type": "ARRAY", "items": {"type": "STRING"}},
        "thumbnail_text": {"type": "STRING"}, "thumbnail_idea": {"type": "STRING"},
        "hook_vo": {"type": "STRING"}, "promise_vo": {"type": "STRING"}, "stakes_vo": {"type": "STRING"},
        "intro_vo": {"type": "STRING"}, "mid_cta_vo": {"type": "STRING"}, "rehook_vo": {"type": "STRING"},
        "outro_cta_vo": {"type": "STRING"}, "endscreen_vo": {"type": "STRING"},
        "items": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "label_en": {"type": "STRING", "description": "2-5 words, caps"},
            "what_ru": {"type": "STRING", "description": "Russian, 1 sentence: what this point says"},
            "vo_lines": {"type": "ARRAY", "items": {"type": "STRING"},
                         "description": "2-4 narrator sentences, each max 18 words, spoken English"},
            "vo_after": {"type": "STRING", "description": "max 10 words, punchy"},
            "shots": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "video": {"type": "STRING", "description": "video id from SOURCES"},
                "start": {"type": "NUMBER"}, "end": {"type": "NUMBER"},
                "line": {"type": "INTEGER", "description": "index of the vo line this shot illustrates"}},
                "required": ["video", "start", "end", "line"]}},
            "sources": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "video ids confirming it"}},
            "required": ["label_en", "what_ru", "vo_lines", "vo_after", "shots", "sources"]},
            "description": "ordered from the LAST place to #1 (best / most important last)"},
        "description": {"type": "STRING"}, "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "explanation_ru": {"type": "STRING"},
    },
    "required": ["title", "alt_titles", "thumbnail_text", "thumbnail_idea", "hook_vo", "promise_vo", "stakes_vo",
                 "intro_vo", "mid_cta_vo", "rehook_vo", "outro_cta_vo", "endscreen_vo", "items", "description",
                 "tags", "explanation_ru"],
}


def _iso_seconds(d: str) -> int:
    import re
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    dd, h, mi, s = (int(x or 0) for x in m.groups())
    return dd * 86400 + h * 3600 + mi * 60 + s


def search(topic: str, n: int = 6) -> list[dict]:
    """Most watched recent videos about the topic (YouTube Data API, the owner's free key)."""
    key = os.environ.get("YOUTUBE_API_KEY", "")
    if not key:
        raise fetch.UserError("Нет ключа YOUTUBE_API_KEY для поиска роликов.")
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = topic if "gta" in topic.lower() else f"GTA 6 {topic}"
    ids: list[str] = []
    for order in ("viewCount", "relevance"):
        r = requests.get("https://www.googleapis.com/youtube/v3/search", timeout=30, params={
            "part": "snippet", "type": "video", "maxResults": 15, "order": order, "q": q, "key": key,
            "relevanceLanguage": "en", "publishedAfter": since, "videoDuration": "medium"}).json()
        ids += [i["id"]["videoId"] for i in r.get("items", []) if i["id"]["videoId"] not in ids]
    v = requests.get("https://www.googleapis.com/youtube/v3/videos", timeout=30, params={
        "part": "snippet,statistics,contentDetails", "id": ",".join(ids[:40]), "key": key}).json()
    vids = [{"id": x["id"], "title": x["snippet"]["title"], "channel": x["snippet"]["channelTitle"],
             "views": int(x["statistics"].get("viewCount", 0)),
             "duration": _iso_seconds(x.get("contentDetails", {}).get("duration", ""))} for x in v.get("items", [])]
    vids = [x for x in vids if 60 <= x["duration"] <= 40 * 60]
    vids.sort(key=lambda x: -x["views"])
    return vids[:n]


def watch(video: dict, topic: str) -> dict:
    part = {"file_data": {"file_uri": f"https://www.youtube.com/watch?v={video['id']}"}}
    dur = int(video.get("duration") or 0)
    prompt = f"""Topic of our video: "{topic}". You see a YouTube video "{video['title']}" by {video['channel']}.
The video is {dur} seconds long ({dur // 60}:{dur % 60:02d}): every timestamp MUST be between 0 and {dur}.
Give real playback seconds of the video (mm:ss converted to seconds), double-check each one.
Watch AND listen. List every concrete fact / tip / mistake about the topic that is SHOWN in gameplay, with the
seconds where it is visible (start-end, 3-10 s, the clearest moment, no talking head, no text-only slides).
Only real in-game facts; mark rumours / concept art / speculation with sure=1. Max 25 facts."""
    return brain.generate([part, {"text": prompt}], FACTS_SCHEMA, temperature=0.2, media_low=True, patience=True)


def research(topic: str, minutes: float, wish: str | None, previous: dict | None, progress) -> dict:
    from .longform import n_items
    vids = search(topic)
    if not vids:
        raise fetch.UserError("На YouTube не нашлось свежих роликов на эту тему.")
    sources = []
    for k, v in enumerate(vids, 1):
        progress(f"👀 ИИ смотрит ролик {k}/{len(vids)}: {v['title'][:50]}")
        try:
            out = watch(v, topic)
        except Exception as e:  # noqa: BLE001 - one video less is fine
            print(f"watch {v['id']} failed: {e}")
            continue
        if out.get("relevant") is False:
            continue
        dur = float(v.get("duration") or 0) or 1e9
        facts = [f for f in out.get("facts") or [] if int(f.get("sure") or 0) >= 3 and f["end"] > f["start"]
                 and f["end"] <= dur - 0.5]
        if facts:
            sources.append({**v, "facts": facts})
    if len(sources) < 2:
        raise fetch.UserError("ИИ не смог разобрать достаточно роликов по теме (Gemini перегружен?). Попробуй позже.")
    n = n_items(minutes)
    progress("✍️ ИИ пишет сценарий по фактам")
    prompt = f"""You write a {minutes:.0f}-minute YouTube video for a GTA 6 channel. Topic: "{topic}".
Make a countdown of exactly {n} points (e.g. mistakes / tips / secrets) using ONLY facts from SOURCES below.
- A point must be confirmed by at least 2 different videos, or by one video with sure=5. List them in "sources".
- shots: 2-3 per point, each 3-8 s, taken from the fact ranges of SOURCES (use those seconds), each shot
  illustrates one vo line ("line" = index). Different shots for different lines; prefer different videos.
- Narration: spoken English, short sentences, "you", helpful and fun, no "hey guys", no "thanks for watching".
  Never state anything that is not in SOURCES.
- Strongest points at #1 and #2 and at the start; hook teases #1.
- Title 60-90 chars, keyword first, a number; description: first 150 chars = keyword + promise, then 2 short
  paragraphs and a line "Footage: videos credited below".
{('OWNER WISH: ' + wish) if wish else ''}
{('PREVIOUS SCRIPT (change it according to the wish): ' + json.dumps(previous, ensure_ascii=False)[:5000]) if previous else ''}
SOURCES:
{json.dumps([{"video": s["id"], "channel": s["channel"], "title": s["title"], "facts": s["facts"]} for s in sources], ensure_ascii=False)[:70000]}"""
    sc = brain.generate([{"text": prompt}], TOPIC_SCRIPT_SCHEMA, temperature=0.6, patience=True)
    known = {s["id"]: s for s in sources}
    items = []
    for it in sc.get("items") or []:
        shots = [s for s in it.get("shots") or [] if s.get("video") in known and 1 <= s["end"] - s["start"] <= 12
                 and s["end"] <= float(known[s["video"]].get("duration") or 1e9) - 0.5]
        if shots and it.get("vo_lines"):
            it["shots"] = shots[:3]
            items.append(it)
    if len(items) < 3:
        raise fetch.UserError("ИИ не собрал достаточно подтверждённых пунктов по этой теме.")
    sc["items"] = items[:n]
    sc["minutes"] = minutes
    sc["topic"] = topic
    footage = []
    for k, it in enumerate(sc["items"]):
        for j, s in enumerate(it["shots"]):
            s["part"] = len(footage)
            footage.append({"part": s["part"], "url": f"https://www.youtube.com/watch?v={s['video']}",
                            "start": max(0.0, float(s["start"]) - 0.5), "end": float(s["end"]) + 0.5,
                            "item": k, "channel": known[s["video"]]["channel"]})
    return {"script": sc, "sources": [{k: s.get(k) for k in ("id", "title", "channel", "views", "duration")} for s in sources],
            "footage": footage}
