"""Understand a link the user sent and download the needed piece of video (yt-dlp, free)."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .util import config, probe, run


class UserError(Exception):
    """A problem the user can fix (shown to them in Russian)."""


def parse_timecode(v: str | None) -> float | None:
    """'1h2m3s' | '3723' | '3723s' | '1:02:03' | '62:03' -> seconds."""
    if not v:
        return None
    v = v.strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?s?", v):
        return float(v.rstrip("s"))
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}", v):
        sec = 0.0
        for p in v.split(":"):
            sec = sec * 60 + int(p)
        return sec
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", v)
    if m and any(m.groups()):
        h, mi, s = (int(x or 0) for x in m.groups())
        return float(h * 3600 + mi * 60 + s)
    return None


def parse_url(url: str) -> dict:
    url = url.strip().strip("<>")
    if not re.match(r"https?://", url):
        url = "https://" + url
    u = urlparse(url)
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    q = parse_qs(u.query)
    t = parse_timecode((q.get("t") or q.get("start") or [None])[0])
    if t is None and u.fragment.startswith("t="):
        t = parse_timecode(u.fragment[2:])
    parts = [p for p in u.path.split("/") if p]
    out = {"url": url, "kind": "other", "t": t, "streamer": None, "id": None}

    if host == "clips.twitch.tv" and parts:
        out.update(kind="twitch_clip", id=parts[0])
    elif host.endswith("twitch.tv"):
        if len(parts) >= 3 and parts[1] == "clip":
            out.update(kind="twitch_clip", id=parts[2], streamer=parts[0].lower())
        elif len(parts) >= 2 and parts[0] == "videos":
            out.update(kind="twitch_vod", id=parts[1])
        elif len(parts) >= 3 and parts[1] in ("v", "video"):
            out.update(kind="twitch_vod", id=parts[2], streamer=parts[0].lower())
        elif len(parts) == 1:
            out.update(kind="twitch_live", streamer=parts[0].lower())
    elif host in ("youtube.com", "music.youtube.com", "youtu.be"):
        vid = None
        if host == "youtu.be" and parts:
            vid = parts[0]
        elif parts and parts[0] in ("shorts", "live", "embed") and len(parts) > 1:
            vid = parts[1]
        else:
            vid = (q.get("v") or [None])[0]
        out.update(kind="youtube", id=vid)
    return out


def plan_window(link: dict, duration: float | None, start: float | None = None,
                end: float | None = None) -> tuple[float | None, float | None]:
    """Which piece of the source to download. None/None = whole thing."""
    cfg = config()["download"]
    if start is not None and end is not None:
        return max(0.0, start), end
    if link["kind"] == "twitch_clip":
        return None, None
    if link["kind"] == "twitch_live":
        raise UserError("Это ссылка на канал, а не на запись. Пришли клип или VOD с таймкодом (?t=1h2m3s).")
    t = link.get("t")
    if t is not None:
        return max(0.0, t - cfg["window_before"]), t + cfg["window_after"]
    if duration and duration > cfg["max_full"]:
        raise UserError("Видео длинное, а таймкода нет. Добавь в ссылку ?t=1h2m3s (место момента) "
                        "или пришли /vod — тогда я сам найду лучшие моменты по чату.")
    return None, None


def _ydl_opts(out_tmpl: str) -> dict:
    return {
        "outtmpl": out_tmpl,
        "format": "bv*[height<=1080][vcodec^=avc1]+ba/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
    }


def download(url: str, workdir: Path, start: float | None = None, end: float | None = None) -> dict:
    """Download the clip/piece. Returns {path, meta, offset, link}."""
    import yt_dlp
    from yt_dlp.utils import download_range_func

    link = parse_url(url)
    workdir.mkdir(parents=True, exist_ok=True)
    info = None
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as y:
            info = y.extract_info(link["url"], download=False)
    except Exception as e:  # noqa: BLE001
        raise UserError(_explain_download_error(link, e)) from e

    a, b = plan_window(link, info.get("duration"), start, end)
    opts = _ydl_opts(str(workdir / "src.%(ext)s"))
    if a is not None:
        opts["download_ranges"] = download_range_func(None, [(a, b)])
        opts["force_keyframes_at_cuts"] = True
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(link["url"], download=True)
    except Exception as e:  # noqa: BLE001
        raise UserError(_explain_download_error(link, e)) from e

    cands = [p for p in workdir.glob("src.*") if p.suffix in (".mp4", ".mkv", ".webm", ".mov", ".ts")]
    if not cands:
        raise UserError("Не получилось скачать видео по ссылке.")
    raw = cands[0]
    path = workdir / "source.mp4"
    # normalise: constant 30 fps, <=1080p, mp4/h264 + aac. Makes seeking exact and maths simple.
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
         "-vf", "fps=30,scale='min(1920,iw)':-2:flags=lanczos",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(path)])
    pr = probe(path)
    if not pr["has_audio"]:
        # add silent track so the rest of the pipeline stays simple
        tmp = workdir / "source_a.mp4"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-f", "lavfi", "-i",
             "anullsrc=r=48000:cl=stereo", "-shortest", "-c:v", "copy", "-c:a", "aac", str(tmp)])
        tmp.replace(path)
        pr = probe(path)

    streamer = link.get("streamer") or _streamer_from_info(info)
    meta = {
        "streamer": streamer,
        "title": info.get("title"),
        "source_url": link["url"],
        "kind": link["kind"],
        "view_count": info.get("view_count"),
        "duration": pr["duration"],
        "width": pr["width"],
        "height": pr["height"],
    }
    return {"path": path, "meta": meta, "offset": a or 0.0, "link": link}


def _streamer_from_info(info: dict) -> str | None:
    for k in ("channel", "uploader_id", "uploader", "creator"):
        v = info.get(k)
        if v and isinstance(v, str):
            v = v.strip().lstrip("@")
            if re.fullmatch(r"[A-Za-z0-9_]{2,30}", v):
                return v.lower()
    return None


def _explain_download_error(link: dict, e: Exception) -> str:
    msg = str(e)
    if link["kind"] == "youtube" and ("Sign in" in msg or "bot" in msg.lower() or "429" in msg):
        return ("YouTube не даёт скачать видео с серверов GitHub (защита от ботов). "
                "Пришли этот же момент ссылкой на Twitch (клип или VOD с таймкодом).")
    if "Private" in msg or "private" in msg:
        return "Видео приватное, скачать нельзя."
    if "subscriber" in msg.lower() or "sub-only" in msg.lower():
        return "Запись доступна только подписчикам стримера — скачать нельзя."
    if "404" in msg or "does not exist" in msg or "Unable to find" in msg:
        return "По ссылке ничего нет (удалено или опечатка)."
    return "Не получилось скачать видео: " + msg.splitlines()[-1][:200]
