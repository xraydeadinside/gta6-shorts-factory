"""Find the best moments of a long stream by letting the AI WATCH it (not by chat spikes).

1. Download the VOD in the lowest quality (picture + sound are enough to understand what happens).
2. Cut it into ~30 minute pieces at 1 frame per second, tiny size.
3. Gemini watches and listens to each piece and lists complete mini-stories:
   setup (what the streamer tries) -> turn (fail / WTF / clutch / funny line) -> reaction.
4. Stories from the whole stream are ranked; chat activity is only a small bonus, never the reason.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from . import brain
from .util import config, probe, run

KINDS = ["funny", "fail", "wtf", "clutch", "rage", "wholesome", "scary", "other"]

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "moments": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "start": {"type": "NUMBER", "description": "seconds from the start of THIS video piece"},
            "end": {"type": "NUMBER", "description": "seconds from the start of THIS video piece"},
            "score": {"type": "INTEGER", "description": "1-10: how good as a standalone viral Short"},
            "kind": {"type": "STRING", "enum": KINDS},
            "context_en": {"type": "STRING",
                           "description": "3-6 words, CAPS, the situation a stranger needs, e.g. 'SPEED TRIES TO STEAL A JET'"},
            "title_en": {"type": "STRING", "description": "Shorts title idea, 4-6 words, starts with 'GTA 6'"},
            "what_ru": {"type": "STRING", "description": "Russian, 1 sentence: what happens (setup -> turn -> reaction)"},
            "why_ru": {"type": "STRING", "description": "Russian, short: why people will watch it to the end"}},
            "required": ["start", "end", "score", "kind", "context_en", "title_en", "what_ru", "why_ru"]}},
    },
    "required": ["moments"],
}


def hms(t: float) -> str:
    t = int(max(0, t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def download_low(url: str, out: Path) -> Path:
    """Whole VOD in the smallest format that still has picture and sound."""
    import yt_dlp
    opts = {"format": "worst[vcodec!=none][acodec!=none][height>=144]/worst[vcodec!=none]/worst",
            "outtmpl": str(out.with_suffix(".%(ext)s")), "quiet": True, "noprogress": True,
            "concurrent_fragment_downloads": 16, "retries": 10, "fragment_retries": 10}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
    if not path.exists():
        cands = sorted(out.parent.glob(out.stem + ".*"))
        if not cands:
            raise RuntimeError("запись не скачалась")
        path = cands[0]
    return path


def cut_piece(src: Path, a: float, length: float, out: Path) -> Path:
    """1 fps, small picture, mono sound: what the AI needs to follow the story, nothing more."""
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.2f}", "-t", f"{length:.2f}", "-i", str(src),
         "-vf", "fps=1,scale=-2:240", "-c:v", "libx264", "-preset", "veryfast", "-crf", "34",
         "-c:a", "aac", "-b:a", "32k", "-ac", "1", str(out)])
    return out


def prompt_for(a: float, b: float, streamer: str | None, title: str | None, chat_hint: str) -> str:
    return f"""You are the clip scout of a GTA 6 YouTube Shorts channel (English-speaking audience).
This video is the piece {hms(a)}-{hms(b)} of a Twitch stream by {streamer or 'the streamer'} ("{title or ''}").
The webcam overlay shows the streamer; the rest is GTA 6 gameplay. Watch AND listen.

Find the moments that would make a great STANDALONE Short for people who never saw this stream.
A good moment is a complete mini-story:
  setup (what the streamer is trying to do) -> turn (fail, surprise, WTF physics, crazy NPC, insane clutch,
  rage, a genuinely funny line) -> the streamer's reaction.
It must make sense on its own, without the rest of the stream.
SKIP: menus, loading, pause screens, talking to chat about unrelated things, donation/sub alerts, sponsor reads,
AFK, cutscenes nobody reacts to, anything that only makes sense with earlier context, generic driving around.

Times are seconds from the START OF THIS VIDEO PIECE (0 .. {b - a:.0f}).
Each moment 20-60 s: start a few seconds BEFORE the setup, end right after the reaction.
Score honestly: 9-10 only for moments that would clearly go viral; most good ones are 6-8.
Return up to 6 moments, the best first. If this piece has nothing good, return an empty list.
{chat_hint}"""


def chat_hint_for(spikes: list[dict], a: float, b: float) -> str:
    inside = [s for s in spikes if a <= s["peak"] < b]
    if not inside:
        return ""
    pts = ", ".join(f"{s['peak'] - a:.0f}s (x{s['score']})" for s in inside[:8])
    return ("Hint, not a rule: viewer chat got unusually active at these piece seconds (chat reacts 3-10 s late): "
            f"{pts}. Chat also spikes for alerts and giveaways, so judge by what you SEE and HEAR.")


def watch(src: Path, duration: float, wd: Path, *, streamer: str | None, title: str | None,
          spikes: list[dict], progress=None) -> tuple[list[dict], list[str]]:
    """All candidate moments in absolute stream seconds, plus problems per piece (for the report)."""
    cfg = config().get("scout") or {}
    piece = float(cfg.get("piece_minutes", 30)) * 60
    pieces = [(a, min(duration, a + piece)) for a in range(0, int(duration), int(piece))]
    pieces = [(float(a), b) for a, b in pieces if b - a >= 30]
    found: list[dict] = []
    errors: dict[int, str] = {}

    def one(i: int, a: float, b: float) -> None:
        clip = wd / f"piece_{i}.mp4"
        if not clip.exists():
            cut_piece(src, a, b - a, clip)
        part = brain.video_part(clip, force_upload=True)
        out = brain.generate([part, {"text": prompt_for(a, b, streamer, title, chat_hint_for(spikes, a, b))}],
                             SCHEMA, temperature=0.4, media_low=True, patience=True)
        for m in out.get("moments") or []:
            s, e = float(m.get("start", 0)), float(m.get("end", 0))
            if e - s < 8 or s < 0 or s > b - a:
                continue
            e = min(e, b - a, s + 75)
            found.append({**m, "start": a + s, "end": a + e})
        clip.unlink(missing_ok=True)

    todo = list(enumerate(pieces, 1))
    rounds = int(cfg.get("retry_rounds", 2)) + 1
    for rnd in range(rounds):
        if rnd:  # Gemini was overloaded / out of quota: wait, then try only the failed pieces again
            if progress:
                progress(f"⏳ Gemini был перегружен — жду и повторяю {len(todo)} част(ь/и) (попытка {rnd + 1})")
            time.sleep(float(cfg.get("retry_wait", 180)))
        failed = []
        for i, (a, b) in todo:
            if progress:
                progress(f"🧠 ИИ смотрит стрим: часть {i}/{len(pieces)} ({hms(a)}–{hms(b)})")
            try:
                one(i, a, b)
                errors.pop(i, None)
            except Exception as e:  # noqa: BLE001 - one bad piece must not kill the whole search
                errors[i] = f"{hms(a)}–{hms(b)}: {str(e)[:160]}"
                failed.append((i, (a, b)))
                print(f"piece {i} failed (round {rnd + 1}): {e}")
        todo = failed
        if not todo:
            break
    return found, list(errors.values())


def rank(cands: list[dict], spikes: list[dict], top: int, min_gap: float = 20.0) -> list[dict]:
    """Best non-overlapping moments. A chat spike inside the moment adds a small bonus."""
    def value(m: dict) -> float:
        bonus = 0.5 if any(m["start"] <= s["peak"] <= m["end"] + 10 for s in spikes) else 0.0
        return float(m.get("score") or 0) + bonus

    out: list[dict] = []
    for m in sorted(cands, key=value, reverse=True):
        if len(out) >= top:
            break
        if int(m.get("score") or 0) < 5:
            continue
        if any(m["start"] < o["end"] + min_gap and o["start"] < m["end"] + min_gap for o in out):
            continue
        out.append({**m, "value": value(m)})
    return out


def chat_in_background(url: str, video_id: str | None, duration: float) -> dict:
    """Read chat in a thread while the video downloads. {'spikes': [...]} when done (empty on failure)."""
    box: dict = {"spikes": []}

    def go():
        try:
            from .vod_chat import fetch_chat, find_spikes
            box["spikes"] = find_spikes(fetch_chat(url, video_id, duration), top=40, min_gap=45)
        except Exception as e:  # noqa: BLE001 - chat is only a hint
            print(f"chat hint failed: {e}")

    t = threading.Thread(target=go, daemon=True)
    t.start()
    box["thread"] = t
    return box


def vod_length(src: Path) -> float:
    return float(probe(src)["duration"])
