"""Enforce YouTube Shorts metadata rules on whatever the AI wrote."""
from __future__ import annotations

import re

KEY = "GTA 6"


def _shorten(t: str, limit: int) -> str:
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,:;-–—")


def fix_title(t: str) -> str:
    t = re.sub(r"#\w+", "", t or "").strip()
    t = re.sub(r"\s+", " ", t).strip(" \"'")
    t = re.sub(r"\bGTA\s*(VI|6)\b", KEY, t, flags=re.I)
    if KEY.lower() not in t.lower():
        t = f"{KEY}: {t}" if t else f"{KEY} moment"
    elif t.lower().find(KEY.lower()) > 40 - len(KEY):
        t = f"{KEY}: " + re.sub(re.escape(KEY), "", t, flags=re.I).strip(" :-")
    return _shorten(t, 60)


def fix_hashtags(tags: list[str], streamer: str | None) -> list[str]:
    out: list[str] = []
    for h in tags or []:
        h = "#" + re.sub(r"[^\w]", "", h.lstrip("#"))
        h = {"#gta6": "#GTA6", "#shorts": "#Shorts", "#gtavi": "#GTAVI"}.get(h.lower(), h)
        if len(h) > 2 and h.lower() not in (x.lower() for x in out):
            out.append(h)
    for must in ("#GTA6", "#Shorts"):
        if must.lower() not in (x.lower() for x in out):
            out.append(must)
    # most specific first, broadest (#GTA6, #Shorts) last; 3-5 total
    broad = [h for h in out if h.lower() in ("#gta6", "#shorts")]
    spec = [h for h in out if h not in broad]
    if streamer and len(spec) < 1:
        spec.append("#" + re.sub(r"[^\w]", "", streamer))
    spec = spec[: 5 - len(broad)]
    res = spec + sorted(broad, key=lambda h: h.lower() == "#shorts")
    return res[:5]


def fix_tags(tags: list[str], streamer: str | None) -> list[str]:
    base = [t.strip().lower() for t in tags or [] if t and t.strip()]
    must = ["gta 6", "gta vi", "gta 6 funny moments", "gta 6 clips"]
    if streamer:
        must.insert(1, streamer.lower())
    out: list[str] = []
    for t in must[:1] + base + must[1:] + ["grand theft auto 6", "gaming shorts"]:
        t = t.replace("#", "")[:60]
        if t and t not in out:
            out.append(t)
    while len(out) > 15 or len(", ".join(out)) > 480:
        out.pop()
    return out


def fix_description(desc: str, streamer: str | None, source_url: str | None, hashtags: list[str]) -> str:
    d = re.sub(r"#\w+", "", desc or "").strip()
    d = re.sub(r"\n{3,}", "\n\n", d)
    first = d.split("\n", 1)[0]
    if KEY.lower() not in first[:160].lower():
        d = f"{KEY} — " + d
    lines = [d]
    if streamer:
        lines.append(f"\n🎥 Streamer: twitch.tv/{streamer} — go follow!")
    lines.append("\n" + " ".join(hashtags))
    return _shorten("\n".join(lines).strip(), 4900)


def normalise(ai: dict, streamer: str | None, source_url: str | None) -> dict:
    titles = [fix_title(t) for t in (ai.get("titles") or []) if t]
    while len(titles) < 3:
        titles.append(fix_title(["GTA 6 streamer can't believe this", "GTA 6 moment nobody expected",
                                 "GTA 6 chaos gone wrong"][len(titles)]))
    hashtags = fix_hashtags(ai.get("hashtags") or [], streamer)
    return {
        "titles": titles[:3],
        "hashtags": hashtags,
        "tags": fix_tags(ai.get("tags") or [], streamer),
        "description": fix_description(ai.get("description") or "", streamer, source_url, hashtags),
    }
