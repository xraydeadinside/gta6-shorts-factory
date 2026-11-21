"""Entry point on GitHub Actions:  python -m engine.run <kind> <job_id>

kind = render | vod | upload
The job (link, wish, style, previous plan, intro video) is read from the Cloudflare Worker.
"""
from __future__ import annotations

import html
import json
import sys
import time
import traceback
from pathlib import Path

from . import audio, brain, camera, compose, fetch, plan, seo, subs, webcam
from .notify import Worker, get_file, send_text, send_video
from .util import config, probe, style as make_style

WORK = Path("work")


def _brief(job: dict) -> dict | None:
    """What the scout saw in the stream (set for moments found by /vod)."""
    raw = job.get("brief")
    if not raw:
        return None
    try:
        return raw if isinstance(raw, dict) else json.loads(raw)
    except (TypeError, ValueError):
        return None


def render_job(job: dict, w: Worker) -> dict:
    jid = job["id"]
    st = make_style(job.get("style") or {})
    wd = WORK / str(jid)
    wd.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    w.stage(jid, "⬇️ Скачиваю видео")
    dl = fetch.download(job["url"], wd, job.get("start"), job.get("end"))
    src, meta = dl["path"], dl["meta"]
    dur = meta["duration"]
    prev = job.get("result") or {}
    reuse = bool(job.get("reuse_plan")) and prev.get("ai")

    w.stage(jid, "👀 Ищу вебку")
    cam = webcam.detect(src, dur, meta["width"], meta["height"])
    if cam["present"]:
        x, y, bw, bh = cam["box"]
        cam["norm"] = [round(x / meta["width"], 3), round(y / meta["height"], 3),
                       round((x + bw) / meta["width"], 3), round((y + bh) / meta["height"], 3)]

    if reuse:
        ai, words = prev["ai"], prev.get("words") or []
    else:
        w.stage(jid, "👂 Расшифровываю речь")
        from .transcribe import numbered, transcribe
        wav = wd / "speech.wav"
        from .util import run as sh
        sh(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", "16000", str(wav)])
        words = transcribe(wav)
        w.stage(jid, "🧠 ИИ смотрит клип и монтирует")
        proxy = brain.make_proxy(src, wd / "proxy.mp4")
        try:
            ai = brain.analyse(proxy, numbered(words), dur, streamer=meta.get("streamer"), cam_hint=cam,
                               source_title=meta.get("title"), wish=job.get("wish"),
                               previous=prev.get("ai") if job.get("wish") else None, brief=_brief(job))
        except Exception as e:  # noqa: BLE001 - AI down: still make a decent short
            ai = {"_error": str(e), "parts": [], "score": 0, "titles": [], "punch_words": [], "shouts": [],
                  "hook_text": "WAIT FOR IT", "explanation_ru": "ИИ был недоступен — смонтировал по самому громкому моменту.",
                  "verdict_ru": "Без ИИ", "description": "", "tags": [], "hashtags": [], "flow_prompt": "",
                  "summary_en": meta.get("title") or "", "viewed_pct": 0, "risk_ru": ""}

    # the AI may correct the webcam box
    wc = ai.get("webcam") or {}
    if not cam["present"] and wc.get("present"):
        hint = webcam.from_box_norm(wc.get("box"))
        if hint:
            cam = webcam.detect(src, dur, meta["width"], meta["height"], hint=hint)
    elif cam["present"] and wc.get("present") is False and not cam.get("how", "").startswith("лицо"):
        cam = {"present": False, "box": None, "face": None, "how": "ИИ: вебки нет"}

    streamer = (meta.get("streamer") or (ai.get("streamer") or "").strip().lstrip("@").lower() or None)
    parts = plan.clean_parts(ai.get("parts") or [], dur, st, cam["present"])
    if not parts or sum(p["b"] - p["a"] for p in parts) < 3:
        parts = plan.fallback_plan(dur, audio.loudest_moment(src), cam["present"], st)
    parts = plan.split_for_pace(parts, words)

    intro_path, intro_len = None, 0.0
    if job.get("intro_file_id"):
        w.stage(jid, "🎞 Беру интро из Flow")
        intro_path = get_file(job["intro_file_id"], wd / "intro.mp4")
        intro_len = min(8.0, probe(intro_path)["duration"])

    tl = plan.timeline(parts, intro_len)
    total = tl[-1]["ob"]
    moves = camera.build_moves(tl, st)
    cuts = plan.cut_points(tl)

    w.stage(jid, "🎬 Собираю шортс")
    cam_h, _ = compose.layout_sizes(st)
    words_out = plan.map_words(words, tl)
    layouts = ([(0.0, intro_len, "intro")] if intro_len else []) + [
        (p["oa"], p["ob"], ("react" if p["shot"] == "react" else ("game" if cam["present"] else "nocam"))) for p in tl]
    shouts = []
    for s in ai.get("shouts") or []:
        for p in tl:
            if p["a"] <= float(s.get("t", -1)) < p["b"]:
                shouts.append({"t": p["oa"] + float(s["t"]) - p["a"], "text": str(s.get("text", ""))})
                break
    ass = subs.build_ass(wd / "subs.ass", words_out=words_out, punch_idx=set(ai.get("punch_words") or []),
                         title=ai.get("hook_text") or "", title_accent_word=ai.get("hook_accent"),
                         shouts=shouts, streamer=streamer, style=st,
                         cam_h=cam_h if cam["present"] else 300, layouts=layouts, total=total)
    snd = audio.build(src, tl, cuts, wd / "audio.wav", style=st, intro=intro_path, intro_len=intro_len, total=total)
    out = wd / f"short_{jid}.mp4"
    compose.encode(compose.render_frames(src, tl, moves, cam, st, total, intro_path, intro_len), snd, ass, out, total)
    thumb = compose.thumbnail(out, wd / "thumb.jpg", min(1.2, total / 2))
    lufs = audio.measure_lufs(out)

    meta_seo = seo.normalise(ai, streamer, meta.get("source_url"))
    w.stage(jid, "📤 Отправляю")
    sent = send_video(out, f"🎬 #{jid} · {html.escape(meta_seo['titles'][0])}", thumb, duration=total)

    readable_plan = [{"role": p["role"], "from": round(p["a"], 1), "to": round(p["b"], 1),
                      "at": round(p["oa"], 1), "shot": p["shot"], "camera": p["camera"], "zoom": p["zoom"],
                      "note": p.get("note", "")} for p in tl if not p.get("sub")]
    return {
        "ai": {k: v for k, v in ai.items() if not k.startswith("_")},
        "model": ai.get("_model"), "ai_error": ai.get("_error"),
        "words": words,
        "plan": readable_plan,
        "titles": meta_seo["titles"], "description": meta_seo["description"],
        "tags": meta_seo["tags"], "hashtags": meta_seo["hashtags"],
        "flow_prompt": ai.get("flow_prompt") or "",
        "explanation": ai.get("explanation_ru") or "",
        "verdict": ai.get("verdict_ru") or "", "score": ai.get("score") or 0,
        "viewed_pct": ai.get("viewed_pct") or 0, "risk": ai.get("risk_ru") or "",
        "summary": ai.get("summary_en") or "",
        "streamer": streamer, "source_title": meta.get("title"),
        "webcam": {"present": cam["present"], "how": cam.get("how")},
        "duration": round(total, 2), "lufs": round(lufs, 1), "size_mb": round(out.stat().st_size / 1048576, 1),
        "video_file_id": sent["file_id"], "video_msg_id": sent["message_id"],
        "render_seconds": int(time.time() - t0),
    }


def vod_job(job: dict, w: Worker) -> dict:
    """The AI watches the whole stream and picks complete stories; each becomes its own render job."""
    from . import scout
    jid = job["id"]
    link = fetch.parse_url(job["url"])
    if link["kind"] != "twitch_vod":
        raise fetch.UserError("Для /vod нужна ссылка на запись Twitch: twitch.tv/videos/…")
    base = link["url"].split("?")[0]
    wd = WORK / str(jid)
    wd.mkdir(parents=True, exist_ok=True)

    import yt_dlp
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(base, download=False)
    except Exception as e:  # noqa: BLE001
        raise fetch.UserError(f"Не открыл запись (удалена или только для подписчиков?): {str(e)[:200]}") from e
    duration = float(info.get("duration") or 0)
    streamer = (info.get("uploader_id") or info.get("uploader") or "").lower() or None
    chat = scout.chat_in_background(base, link["id"], duration)

    w.stage(jid, f"⬇️ Скачиваю запись ({scout.hms(duration)}) в низком качестве")
    src = scout.download_low(base, wd / "vod_low")
    duration = scout.vod_length(src) or duration
    chat["thread"].join(timeout=600)
    spikes = chat["spikes"]

    cands, problems = scout.watch(src, duration, wd, streamer=streamer, title=info.get("title"), spikes=spikes,
                                  progress=lambda s: w.stage(jid, s))
    top = int((config().get("vod") or {}).get("top_moments", 4))
    best = scout.rank(cands, spikes, top)
    how = "ИИ посмотрел стрим"
    if not best and spikes:  # the AI could not watch (limits) -> old chat method, so the owner still gets something
        how = "ИИ не смог посмотреть стрим — взял всплески чата"
        best = [{"start": s["start"], "end": s["end"], "score": 0, "kind": s["kind"], "context_en": "",
                 "title_en": "", "what_ru": f"всплеск чата x{s['score']}", "why_ru": ""} for s in spikes[:top]]
    if not best:
        extra = f" Проблемы: {problems[0]}" if problems else ""
        raise fetch.UserError("Не нашёл в этой записи законченных смешных или безумных историй." + extra)

    moments = []
    for m in best:
        a, b = max(0.0, m["start"] - 3), min(duration, m["end"] + 3)
        brief = {k: m.get(k) for k in ("what_ru", "why_ru", "context_en", "title_en", "kind", "score")}
        note = f"{m.get('what_ru', '')} · оценка {m.get('score', 0)}/10 · {scout.hms(a)}"
        moments.append({"url": f"{base}?t={int(a)}s", "start": a, "end": b, "note": note[:300],
                        "brief": json.dumps(brief, ensure_ascii=False)})
    w.post("/hook/spawn", {"job_id": jid, "moments": moments})
    return {"moments": moments, "candidates": len(cands), "how": how, "problems": problems,
            "chat_spikes": len(spikes), "duration": duration}


def upload_job(job: dict, w: Worker) -> dict:
    from .youtube_upload import upload
    res = job.get("result") or {}
    if not res.get("video_file_id"):
        raise fetch.UserError("Нет готового видео для загрузки.")
    wd = WORK / str(job["id"])
    wd.mkdir(parents=True, exist_ok=True)
    path = get_file(res["video_file_id"], wd / "final.mp4")
    title = job.get("title") or res["titles"][0]
    desc = job.get("description") or res["description"]
    vid = upload(path, title, desc, res.get("tags") or [])
    return {"youtube_id": vid, "youtube_url": f"https://youtube.com/shorts/{vid}"}


def main() -> int:
    kind, jid = sys.argv[1], int(sys.argv[2])
    w = Worker()
    job = w.job(jid)
    try:
        if kind == "render":
            res = render_job(job, w)
            w.post("/hook/done", {"job_id": jid, "kind": kind, "result": res})
        elif kind == "vod":
            res = vod_job(job, w)
            w.post("/hook/done", {"job_id": jid, "kind": kind, "result": res})
        elif kind == "upload":
            res = upload_job(job, w)
            w.post("/hook/done", {"job_id": jid, "kind": kind, "result": res})
        else:
            raise ValueError(kind)
    except fetch.UserError as e:
        w.post("/hook/fail", {"job_id": jid, "kind": kind, "error": str(e), "user": True})
        return 0
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        r = w.post("/hook/fail", {"job_id": jid, "kind": kind, "error": f"{type(e).__name__}: {e}"[:600],
                                  "trace": tb[-2500:]})
        if not r:
            send_text(f"❌ #{jid}: ошибка монтажа: {e}"[:500])
            return 1
        return 0  # already reported to the bot; don't send a second message from the workflow
    print(json.dumps({"ok": True, "job": jid}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
