"""Best moments of a finished Twitch VOD by chat spikes (free, no API key).

Chat reacts 2-10 s AFTER the moment: count messages in 10 s buckets, weight laughs/hype higher,
find peaks well above the baseline and move the window back in time.
"""
from __future__ import annotations

import re
import statistics

import requests

LAUGH = re.compile(r"(lol+|lmao+|lmfao|kekw|omegalul|\blul\b|icant|💀|😂|🤣|haha+|\bxd+\b|\bdead\b|crying)", re.I)
HYPE = re.compile(r"(pog+|poggers|clip (it|that)|\bomg\b|\bwtf\b|no way|holy|insane|bro what|\bw\b|monkas)", re.I)

GQL = "https://gql.twitch.tv/gql"
PUBLIC_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # Twitch website's public client id (no account needed)
COMMENTS_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"


def fetch_chat_gql(video_id: str, max_pages: int = 3000) -> list[tuple[float, str]]:
    msgs: list[tuple[float, str]] = []
    cursor = None
    s = requests.Session()
    s.headers.update({"Client-Id": PUBLIC_CLIENT_ID, "Content-Type": "application/json"})
    for _ in range(max_pages):
        variables = {"videoID": video_id}
        variables.update({"cursor": cursor} if cursor else {"contentOffsetSeconds": 0})
        body = [{"operationName": "VideoCommentsByOffsetOrCursor", "variables": variables,
                 "extensions": {"persistedQuery": {"version": 1, "sha256Hash": COMMENTS_HASH}}}]
        r = s.post(GQL, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()[0].get("data") or {}
        comments = ((data.get("video") or {}).get("comments")) or {}
        edges = comments.get("edges") or []
        for e in edges:
            n = e.get("node") or {}
            text = "".join(f.get("text", "") for f in ((n.get("message") or {}).get("fragments") or []))
            msgs.append((float(n.get("contentOffsetSeconds") or 0), text))
        if not comments.get("pageInfo", {}).get("hasNextPage") or not edges:
            break
        cursor = edges[-1].get("cursor")
        if not cursor:
            break
    return msgs


def fetch_chat(vod_url: str, video_id: str | None) -> list[tuple[float, str]]:
    if video_id:
        try:
            msgs = fetch_chat_gql(video_id)
            if msgs:
                return msgs
        except Exception:  # noqa: BLE001 - fall back to the library
            pass
    from chat_downloader import ChatDownloader
    out = []
    for m in ChatDownloader().get_chat(vod_url):
        t = m.get("time_in_seconds")
        if t is not None:
            out.append((float(t), m.get("message") or ""))
    return out


def find_spikes(msgs: list[tuple[float, str]], bucket: float = 10.0, top: int = 3,
                min_gap: float = 90.0, factor: float = 2.2) -> list[dict]:
    """[{start, end, peak, score, kind}] best first."""
    if not msgs:
        return []
    n = int(max(t for t, _ in msgs) // bucket) + 1
    score = [0.0] * n
    laughs = [0] * n
    for t, text in msgs:
        i = int(t // bucket)
        w = 1.0
        if LAUGH.search(text):
            w += 1.5
            laughs[i] += 1
        if HYPE.search(text):
            w += 1.0
        score[i] += w
    sm = [sum(score[max(0, i - 1):i + 2]) / len(score[max(0, i - 1):i + 2]) for i in range(n)]
    base = statistics.median([s for s in sm if s > 0] or [1.0])
    picked: list[dict] = []
    for i in sorted(range(n), key=lambda i: sm[i], reverse=True):
        if sm[i] < base * factor or len(picked) >= top:
            break
        peak = i * bucket + bucket / 2
        if any(abs(peak - p["peak"]) < min_gap for p in picked):
            continue
        lw = sum(laughs[max(0, i - 1):i + 2])
        picked.append({"peak": peak, "start": max(0.0, peak - 40.0), "end": peak + 15.0,
                       "score": round(sm[i] / base, 2), "kind": "funny" if lw >= 3 else "hype"})
    return picked
