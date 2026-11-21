"""Best moments of a finished Twitch VOD by chat spikes (free, no API key).

Chat reacts 2-10 s AFTER the moment: count messages in 10 s buckets, weight laughs/hype higher,
find peaks well above the LOCAL normal level of the chat and move the window back in time.
"""
from __future__ import annotations

import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import requests

LAUGH = re.compile(r"(lol+|lmao+|lmfao|kekw|omegalul|\blul\b|lulw|icant|💀|😂|🤣|haha+|\bxd+\b|\bdead\b|crying)", re.I)
HYPE = re.compile(r"(pog+|poggers|clip (it|that)|\bomg\b|\bwtf\b|no way|holy|insane|bro what|\bw\b|monkas|sheesh)", re.I)

GQL = "https://gql.twitch.tv/gql"
PUBLIC_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # Twitch website's public client id (no account needed)
COMMENTS_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"


def _page(s: requests.Session, video_id: str, cursor: str | None, offset: float | None) -> dict:
    variables: dict = {"videoID": video_id}
    variables.update({"cursor": cursor} if cursor else {"contentOffsetSeconds": int(offset or 0)})
    body = [{"operationName": "VideoCommentsByOffsetOrCursor", "variables": variables,
             "extensions": {"persistedQuery": {"version": 1, "sha256Hash": COMMENTS_HASH}}}]
    for attempt in range(4):
        try:
            r = s.post(GQL, json=body, timeout=30)
            r.raise_for_status()
            data = r.json()[0].get("data") or {}
            return ((data.get("video") or {}).get("comments")) or {}
        except Exception:  # noqa: BLE001 - transient network error / rate limit
            time.sleep(1.5 * (attempt + 1))
    return {}


def _fetch_range(video_id: str, a: float, b: float, max_pages: int = 4000) -> list[tuple[float, str]]:
    """All comments with a <= t < b, following cursors from offset a."""
    s = requests.Session()
    s.headers.update({"Client-Id": PUBLIC_CLIENT_ID, "Content-Type": "application/json"})
    out: list[tuple[float, str]] = []
    cursor = None
    for _ in range(max_pages):
        comments = _page(s, video_id, cursor, a if cursor is None else None)
        edges = comments.get("edges") or []
        last_t = a
        for e in edges:
            n = e.get("node") or {}
            t = float(n.get("contentOffsetSeconds") or 0)
            last_t = max(last_t, t)
            if a <= t < b:
                text = "".join(f.get("text", "") for f in ((n.get("message") or {}).get("fragments") or []))
                out.append((t, text))
        if not edges or last_t >= b or not comments.get("pageInfo", {}).get("hasNextPage"):
            break
        cursor = edges[-1].get("cursor")
        if not cursor:
            break
    return out


def _sample(s: requests.Session, video_id: str, o: float, step: float) -> list[tuple[float, str, float]]:
    """One page of chat starting at offset o, limited to [o, o+step).

    Busy chats fill a page in under a second, so each kept message gets a weight = step / covered time:
    the bucket then counts as many messages as the chat really had (an estimate of chat speed)."""
    comments = _page(s, video_id, None, o)
    rows = []
    for e in comments.get("edges") or []:
        n = e.get("node") or {}
        t = float(n.get("contentOffsetSeconds") or 0)
        text = "".join(f.get("text", "") for f in ((n.get("message") or {}).get("fragments") or []))
        rows.append((t, text))
    rows.sort(key=lambda r: r[0])
    kept = [r for r in rows if o <= r[0] < o + step]
    if not kept:
        return []
    full = len(kept) == len(rows) and rows and rows[-1][0] < o + step and comments.get("pageInfo", {}).get("hasNextPage")
    covered = (kept[-1][0] - o) if full else step
    w = step / max(covered, 0.5)
    return [(t, text, w) for t, text in kept]


def fetch_chat_gql(video_id: str, duration: float | None = None, workers: int = 16,
                   step: float = 5.0) -> list[tuple[float, str, float]]:
    """Chat of the whole VOD, sampled every `step` seconds in parallel (Twitch no longer pages by cursor)."""
    if not duration:
        return [(t, x, 1.0) for t, x in _fetch_range(video_id, 0, 10 ** 9, max_pages=20000)]
    step = max(step, duration / 4000)  # at most ~4000 requests
    offsets = [i * step for i in range(int(duration // step) + 1)]
    local = __import__("threading").local()

    def one(o: float):
        if not hasattr(local, "s"):
            local.s = requests.Session()
            local.s.headers.update({"Client-Id": PUBLIC_CLIENT_ID, "Content-Type": "application/json"})
        return _sample(local.s, video_id, o, step)

    msgs: list[tuple[float, str, float]] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for part in ex.map(one, offsets):
            msgs.extend(part)
    msgs.sort(key=lambda m: m[0])
    return msgs


def fetch_chat(vod_url: str, video_id: str | None, duration: float | None = None) -> list[tuple]:
    if video_id:
        try:
            msgs = fetch_chat_gql(video_id, duration)
            est = sum(m[2] for m in msgs)
            print(f"chat (gql): {len(msgs)} messages read (~{est:.0f} estimated), "
                  f"up to {msgs[-1][0] if msgs else 0:.0f}s of {duration}s")
            if msgs:
                return msgs
        except Exception as e:  # noqa: BLE001 - fall back to the library
            print(f"chat gql failed: {e}")
    from chat_downloader import ChatDownloader
    out = []
    for m in ChatDownloader().get_chat(vod_url):
        t = m.get("time_in_seconds")
        if t is not None:
            out.append((float(t), m.get("message") or ""))
    print(f"chat (chat_downloader): {len(out)} messages")
    return out


def find_spikes(msgs: list[tuple], bucket: float = 10.0, top: int = 3,
                min_gap: float = 90.0, factor: float = 1.6, window: int = 30) -> list[dict]:
    """[{start, end, peak, score, kind}] best first.

    Each 10 s bucket gets a weight (laughs and hype count more). A bucket is compared with the
    LOCAL normal level of the chat (median of the surrounding ~10 minutes), so a huge chat that is
    always busy still shows its spikes. If nothing clears the bar, the strongest moments are returned anyway.
    """
    if not msgs:
        return []
    n = int(max(m[0] for m in msgs) // bucket) + 1
    score = [0.0] * n
    laughs = [0.0] * n
    for m in msgs:
        t, text = m[0], m[1]
        k = m[2] if len(m) > 2 else 1.0  # sampled chat: one read message stands for k real ones
        i = int(t // bucket)
        w = 1.0
        if LAUGH.search(text):
            w += 1.5
            laughs[i] += k
        if HYPE.search(text):
            w += 1.0
        score[i] += w * k
    sm = [sum(score[max(0, i - 1):i + 2]) / len(score[max(0, i - 1):i + 2]) for i in range(n)]
    glob = statistics.median([s for s in sm if s > 0] or [1.0])
    ratio = []
    for i in range(n):
        around = [s for s in sm[max(0, i - window):i + window + 1] if s > 0]
        local = statistics.median(around) if around else glob
        ratio.append(sm[i] / max(local, glob * 0.5, 1e-6))

    def pick(min_ratio: float, have: list[dict]) -> list[dict]:
        out = list(have)
        for i in sorted(range(n), key=lambda i: ratio[i], reverse=True):
            if len(out) >= top or ratio[i] < min_ratio:
                break
            if sm[i] <= 0:
                continue
            peak = i * bucket + bucket / 2
            if any(abs(peak - p["peak"]) < min_gap for p in out):
                continue
            lw = sum(laughs[max(0, i - 1):i + 2])
            tw = sum(score[max(0, i - 1):i + 2]) or 1.0
            out.append({"peak": peak, "start": max(0.0, peak - 40.0), "end": peak + 15.0,
                        "score": round(ratio[i], 2),
                        "kind": "funny" if lw >= 3 and lw * 2.5 / tw >= 0.15 else "hype"})
        return out

    picked = pick(factor, [])
    if len(picked) < top:  # always give the best we have
        picked = pick(0.0, picked)
    return picked
