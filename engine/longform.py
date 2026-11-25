"""Long videos (16:9, 9-15 min): "Streamers React to GTA 6's Most INSANE Moments".

1. script(job)  - the AI picks moments from the library (found by /vod and /collect) and writes a countdown
                  script with a hook, mid CTA, re-hook at ~60% and outro (rules of the /youtube skill).
2. render(job)  - after the owner approves: downloads each moment, voices the script (Piper, free placeholder
                  voice; ElevenLabs later), builds 1920x1080 segments with ffmpeg and joins them.
"""
from __future__ import annotations

import json
import math
import os
import re
import textwrap
import wave
from pathlib import Path

import requests

from . import audio, brain, fetch, sfx
from .util import FONTS_DIR, probe, run

W, H, FPS = 1920, 1080, 30
FONT = str(FONTS_DIR / "Anton-Regular.ttf")
VOICE = os.environ.get("PIPER_VOICE", str(Path.home() / ".cache" / "piper" / "en_US-ryan-high.onnx"))
NEON, YELLOW = "0xFF2E9A", "0xFFE600"

SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING", "description": "60-90 chars, starts with the main keyword, names 2-3 streamers"},
        "alt_titles": {"type": "ARRAY", "items": {"type": "STRING"}},
        "thumbnail_text": {"type": "STRING", "description": "2-4 words, caps"},
        "thumbnail_idea": {"type": "STRING"},
        "hook_vo": {"type": "STRING", "description": "0:00-0:05 grab, max 12 words, said over the #1 moment"},
        "promise_vo": {"type": "STRING", "description": "max 35 words: what the viewer gets"},
        "stakes_vo": {"type": "STRING", "description": "max 25 words: tease #1 without revealing it"},
        "intro_vo": {"type": "STRING", "description": "max 30 words: how the ranking works"},
        "mid_cta_vo": {"type": "STRING", "description": "max 25 words, soft subscribe tied to the content"},
        "rehook_vo": {"type": "STRING", "description": "max 25 words, said before the top 3"},
        "outro_cta_vo": {"type": "STRING", "description": "max 30 words: ask a specific comment"},
        "endscreen_vo": {"type": "STRING", "description": "max 20 words: point to the next video"},
        "items": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "cand": {"type": "STRING", "description": "id of the chosen moment"},
            "label_en": {"type": "STRING", "description": "2-5 words, caps, shown on the number card"},
            "vo_before": {"type": "STRING", "description": "max 35 words: context so a stranger understands the clip"},
            "vo_after": {"type": "STRING", "description": "max 14 words: punchy reaction / joke / bridge to the next"},
            "what_ru": {"type": "STRING"}},
            "required": ["cand", "label_en", "vo_before", "vo_after", "what_ru"]},
            "description": "ordered from the LAST place (e.g. #10) to #1"},
        "description": {"type": "STRING", "description": "YouTube description without chapters (they are added)"},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "explanation_ru": {"type": "STRING", "description": "Russian, 4-6 lines: why this order and this hook"},
    },
    "required": ["title", "alt_titles", "thumbnail_text", "thumbnail_idea", "hook_vo", "promise_vo", "stakes_vo",
                 "intro_vo", "mid_cta_vo", "rehook_vo", "outro_cta_vo", "endscreen_vo", "items", "description",
                 "tags", "explanation_ru"],
}


def n_items(minutes: float) -> int:
    return max(6, min(15, round(minutes * 60 / 58)))


def write_script(library: list[dict], minutes: float, wish: str | None = None, previous: dict | None = None) -> dict:
    cands = [{"id": m["id"], "streamer": m.get("streamer"), "kind": m.get("kind"), "score": m.get("score"),
              "len_s": round(float(m["end"]) - float(m["start"])), "situation": m.get("context_en"),
              "what": m.get("what_ru"), "why": m.get("why_ru")} for m in library]
    n = n_items(minutes)
    prompt = f"""You write a {minutes:.0f}-minute YouTube video for a GTA 6 channel: a countdown of the most insane
moments of famous streamers playing GTA 6 ("Streamers React to GTA 6's Most INSANE Moments").
The streamers' clips carry the video; a voice-over (AI voice) only gives context and jokes between them,
so keep every line SHORT, spoken, punchy, funny, English, direct address ("you"). Never "hey guys", never
"thanks for watching". Do not invent events: describe only what the moment data says.

Pick exactly {n} moments from CANDIDATES (use their ids). Rules:
- #1 and #2 are the strongest; #1 is also teased in the hook. Strong moments also at the start (#{n}, #{n - 1}).
- Variety: at most 3 moments of the same streamer, alternate moods (funny / chaos / wholesome / epic / scary).
- items are ordered from #{n} down to #1.
- vo_before gives a stranger the context (who, what they try) without spoiling the punchline.
- Title: keyword first ("Streamers React to GTA 6…" or "GTA 6 …"), a number helps, 60-90 chars, honest.
- description: first 150 chars = keyword + promise; then 2 short paragraphs; credit the streamers' Twitch channels.
- tags: 12-15, most specific first.
{('OWNER WISH (follow it): ' + wish) if wish else ''}
{('PREVIOUS SCRIPT (change it according to the wish): ' + json.dumps(previous, ensure_ascii=False)[:6000]) if previous else ''}

CANDIDATES:
{json.dumps(cands, ensure_ascii=False)[:60000]}"""
    out = brain.generate([{"text": prompt}], SCRIPT_SCHEMA, temperature=0.7, patience=True)
    ids = {m["id"] for m in library}
    out["items"] = [it for it in out.get("items") or [] if it.get("cand") in ids][:n]
    if len(out["items"]) < 3:
        raise fetch.UserError("ИИ не смог собрать рейтинг: в библиотеке мало моментов. Пришли /collect по записям стримеров.")
    out["minutes"] = minutes
    return out


# ------------------------------------------------------------------ voice
TTS_CACHE = Path.home() / ".cache" / "tts"


def _elevenlabs(text: str, raw: Path) -> bool:
    """ElevenLabs voice (paid plan of the owner). Cached by text, so a re-edit does not spend characters again."""
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        return False
    from .util import config
    v = (config().get("voice") or {})
    voice_id, model = v.get("voice_id", "TX3LPaxmHKxFdv7VOQHJ"), v.get("model", "eleven_multilingual_v2")
    import hashlib
    h = hashlib.sha1(f"{voice_id}|{model}|{text}".encode()).hexdigest()
    TTS_CACHE.mkdir(parents=True, exist_ok=True)
    cached = TTS_CACHE / f"{h}.mp3"
    if not cached.exists():
        r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128",
                          headers={"xi-api-key": key, "content-type": "application/json"}, timeout=120,
                          json={"text": text, "model_id": model, "voice_settings": {
                              "stability": float(v.get("stability", 0.38)), "similarity_boost": 0.8,
                              "style": float(v.get("style", 0.35)), "use_speaker_boost": True}})
        if r.status_code != 200:
            print(f"ElevenLabs failed ({r.status_code}): {r.text[:200]} — using the free voice")
            return False
        cached.write_bytes(r.content)
    raw.write_bytes(cached.read_bytes())
    return True


def tts(text: str, out: Path) -> float:
    """ElevenLabs when the key is set, otherwise Piper (free, offline). Returns seconds."""
    raw = out.with_suffix(".raw.mp3")
    speed = "atempo=1.0,"
    if not _elevenlabs(text, raw):
        from piper import PiperVoice  # type: ignore
        global _voice
        try:
            _voice
        except NameError:
            _voice = PiperVoice.load(VOICE)
        raw = out.with_suffix(".raw.wav")
        with wave.open(str(raw), "wb") as wf:
            if hasattr(_voice, "synthesize_wav"):
                _voice.synthesize_wav(text, wf)
            else:
                _voice.synthesize(text, wf)
        speed = "atempo=1.08,"   # the free voice is slow: a bit faster sounds more energetic
    # loud and clear, 48 kHz stereo like the rest
    run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af",
         speed + "highpass=f=80,acompressor=threshold=-18dB:ratio=3:attack=5:release=80,"
         "loudnorm=I=-16:TP=-1.5:LRA=7", "-ar", "48000", "-ac", "2", str(out)])
    raw.unlink(missing_ok=True)
    return probe(out)["duration"]


# ------------------------------------------------------------------ drawing helpers
def _tf(wd: Path, text: str) -> str:
    """drawtext reads the text from a file: no escaping problems with quotes and colons."""
    wd.mkdir(parents=True, exist_ok=True)
    p = wd / f"t{abs(hash(text)) % 10**10}.txt"
    p.write_text(text, encoding="utf-8")
    return str(p).replace("\\", "/").replace(":", r"\:")


def _font() -> str:
    return FONT.replace("\\", "/").replace(":", r"\:")


def _caption_filters(wd: Path, text: str, start: float, dur: float) -> list[str]:
    """VO subtitles: chunks of ~6 words timed by word count, bottom centre, white with outline."""
    words = text.split()
    if not words or dur <= 0:
        return []
    chunks = [" ".join(words[i:i + 6]) for i in range(0, len(words), 6)]
    per = dur / len(words)
    t, out = start, []
    for c in chunks:
        d = per * len(c.split())
        out.append(f"drawtext=fontfile='{_font()}':textfile='{_tf(wd, c.upper())}':fontsize=64:fontcolor=white:"
                   f"borderw=6:bordercolor=black:x=(w-text_w)/2:y=h-170:enable='between(t,{t:.2f},{t + d:.2f})'")
        t += d
    return out


def _enc(out: Path) -> list[str]:
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(out)]


def frame_at(src: Path, t: float, out: Path) -> Path:
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0, t):.2f}", "-i", str(src), "-frames:v", "1", str(out)])
    return out


def card(out: Path, bg: Path, title: str, sub: str, vo: Path | None, wd: Path, *, min_len: float = 1.6,
         hit: str | None = "impact", vo_text: str = "", big: int = 300) -> float:
    """Number card: blurred, darkened still with a slow zoom, big neon number + label, VO on top."""
    vo_len = probe(vo)["duration"] if vo else 0.0
    dur = max(min_len, vo_len + 0.5)
    fx = (f"[0:v]scale={W * 2}:{H * 2},zoompan=z='min(1.0+0.0009*on,1.12)':d={int(dur * FPS) + 1}:s={W}x{H}:fps={FPS},"
          f"boxblur=18:2,eq=brightness=-0.22:saturation=1.2")
    fx += (f",drawtext=fontfile='{_font()}':textfile='{_tf(wd, title)}':fontsize={big}:fontcolor={NEON}:borderw=14:"
           f"bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-110")
    if sub:
        fx += (f",drawtext=fontfile='{_font()}':textfile='{_tf(wd, sub)}':fontsize=96:fontcolor={YELLOW}:borderw=9:"
               f"bordercolor=black:x=(w-text_w)/2:y=(h/2)+120")
    for f in _caption_filters(wd, vo_text, 0.35, vo_len):
        fx += "," + f
    fx += f",trim=duration={dur:.2f}[v]"
    ins = ["-i", str(bg)]   # one still; zoompan makes the frames
    a_parts, idx = [], 1
    sfx_wav = None
    if hit:
        sfx_wav = wd / f"sfx_{hit}.wav"
        if not sfx_wav.exists():
            audio.write_wav(sfx_wav, sfx.render(hit) * 0.55)
        ins += ["-i", str(sfx_wav)]
        a_parts.append(f"[{idx}:a]apad[a{idx}]")
        idx += 1
    if vo:
        ins += ["-i", str(vo)]
        a_parts.append(f"[{idx}:a]adelay=350|350,apad[a{idx}]")
        idx += 1
    if a_parts:
        mix = "".join(f"[a{i}]" for i in range(1, idx))
        af = ";".join(a_parts) + f";{mix}amix=inputs={idx - 1}:normalize=0,atrim=duration={dur:.2f}[a]"
    else:
        ins += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        af = f"[1:a]atrim=duration={dur:.2f}[a]"
    run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", fx + ";" + af, "-map", "[v]", "-map", "[a]",
         "-t", f"{dur:.2f}", *_enc(out)])
    return dur


def clip_segment(out: Path, src: Path, a: float, b: float, wd: Path, *, rank: int | None, streamer: str | None,
                 vo: Path | None = None, vo_text: str = "", duck: float = 1.0,
                 lines: list[tuple[float, Path, str]] | None = None, credit: str | None = None) -> float:
    """A piece of the stream, full frame, corner badge with the place and the streamer credit.

    `lines` = narrator lines [(t in this segment, wav, text)] placed ON the action they describe: the stream
    sound is lowered to ~30% while the narrator talks (ducking) and comes back right after."""
    dur = b - a
    lines = list(lines or [])
    if vo:
        lines.insert(0, (0.2, vo, vo_text))
    spans = []
    for t0, wav, _ in lines:
        spans.append((t0, min(dur, t0 + probe(wav)["duration"] + 0.15)))
    fx = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    if rank:
        fx += (f",drawtext=fontfile='{_font()}':textfile='{_tf(wd, f'#{rank}')}':fontsize=110:fontcolor={NEON}:"
               f"borderw=8:bordercolor=black:x=40:y=30")
    if streamer or credit:
        fx += (f",drawtext=fontfile='{_font()}':textfile='{_tf(wd, credit or f'twitch.tv/{streamer}')}':fontsize=44:"
               f"fontcolor=white:borderw=4:bordercolor=black:x=40:y=h-80:alpha=0.85")
    for (t0, wav, text), (s0, s1) in zip(lines, spans):
        for f in _caption_filters(wd, text, t0, s1 - t0 - 0.15):
            fx += "," + f
    fx += "[v]"
    ins = ["-ss", f"{a:.2f}", "-t", f"{dur:.2f}", "-i", str(src)]
    vol = f"volume={duck}"
    if spans and not vo:
        cond = "+".join(f"between(t,{s0 - 0.1:.2f},{s1:.2f})" for s0, s1 in spans)
        vol = f"volume='if(gt({cond},0),0.3,1)':eval=frame"
    af = f"[0:a]loudnorm=I=-16:TP=-1.5:LRA=11,{vol}[c]"
    mixes = ["[c]"]
    for k, (t0, wav, _) in enumerate(lines, 1):
        ins += ["-i", str(wav)]
        d = int(max(0.0, t0) * 1000)
        af += f";[{k}:a]adelay={d}|{d},apad[l{k}]"
        mixes.append(f"[l{k}]")
    if len(mixes) > 1:
        af += f";{''.join(mixes)}amix=inputs={len(mixes)}:normalize=0,atrim=duration={dur:.2f}[a]"
    else:
        af += ";[c]anull[a]"
    run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", fx + ";" + af, "-map", "[v]", "-map", "[a]",
         "-t", f"{dur:.2f}", *_enc(out)])
    return dur


def montage(out: Path, pieces: list[tuple[Path, float]], each: float, wd: Path, vo: Path | None, vo_text: str,
            total: float | None = None) -> float:
    """Fast cuts (each `each` s) around given moments, whoosh on every cut, VO on top, clips ducked."""
    parts = []
    for k, (src, t) in enumerate(pieces):
        p = wd / f"mt_{out.stem}_{k}.mp4"
        clip_segment(p, src, max(0.0, t - each / 2), max(0.0, t - each / 2) + each, wd, rank=None, streamer=None,
                     duck=0.35)
        parts.append(p)
    joined = wd / f"mt_{out.stem}_joined.mp4"
    concat(parts, joined)
    dur = probe(joined)["duration"]
    if total:
        dur = min(dur, total)
    fx = "[0:v]null"
    for f in _caption_filters(wd, vo_text, 0.2, probe(vo)["duration"] if vo else 0):
        fx += "," + f
    fx += f",trim=duration={dur:.2f}[v]"
    w_wav = wd / "whoosh.wav"
    if not w_wav.exists():
        audio.write_wav(w_wav, audio.whoosh() * 0.5)
    ins = ["-i", str(joined)]
    af_parts, mixes, idx = [], ["[0:a]"], 1
    for k in range(1, len(pieces)):
        ins += ["-i", str(w_wav)]
        d = int((k * each - 0.2) * 1000)
        af_parts.append(f"[{idx}:a]adelay={d}|{d}[w{idx}]")
        mixes.append(f"[w{idx}]")
        idx += 1
    if vo:
        ins += ["-i", str(vo)]
        af_parts.append(f"[{idx}:a]adelay=150|150[vo]")
        mixes.append("[vo]")
        idx += 1
    af = ";".join(af_parts + [f"{''.join(mixes)}amix=inputs={len(mixes)}:normalize=0,atrim=duration={dur:.2f}[a]"])
    run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", fx + ";" + af, "-map", "[v]", "-map", "[a]",
         "-t", f"{dur:.2f}", *_enc(out)])
    return dur


def concat(parts: list[Path], out: Path) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    try:
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
             "-movflags", "+faststart", str(out)])
    except RuntimeError:  # pieces differ slightly: re-encode while joining (slower, always works)
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), *_enc(out)])
    return out


def thumbnail(src: Path, t: float, text: str, out: Path, wd: Path) -> Path:
    lines = textwrap.wrap(text.upper(), 12)[:3]
    fx = "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,eq=saturation=1.45:contrast=1.12"
    for i, ln in enumerate(lines):
        fx += (f",drawtext=fontfile='{_font()}':textfile='{_tf(wd, ln)}':fontsize=150:fontcolor={YELLOW}:"
               f"borderw=12:bordercolor=black:x=50:y={40 + i * 160}")
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0, t):.2f}", "-i", str(src), "-frames:v", "1", "-vf", fx,
         "-q:v", "2", str(out)])
    return out


def fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


# ------------------------------------------------------------------ topic footage
def prepare_topic_clips(job: dict, wd: Path, progress) -> list[dict]:
    """Build one clip per point from the downloaded shots: every narrator sentence plays over the shot that
    shows it; a shot shorter than its sentence is held on its last frame. Footage sound stays low."""
    sc, footage = job["result"]["script"], job["result"]["footage"]
    n = len(sc["items"])
    files: dict[int, Path] = {}
    (wd / "shots").mkdir(parents=True, exist_ok=True)
    for f in footage:
        if not f.get("asset"):
            continue
        progress(f"⬇️ Беру кадры из хранилища {f['part'] + 1}/{len(footage)}")
        files[f["part"]] = fetch.material_file(f["asset"], wd / "shots" / f"raw{f['part']}")
    clips = []
    for k, it in enumerate(sc["items"]):
        shots = sorted(it["shots"], key=lambda s: int(s.get("line") or 0))
        by_line = {int(s.get("line") or 0): s for s in shots}
        pieces, lines, t, last = [], [], 0.0, shots[0]
        for j, text in enumerate(it["vo_lines"]):
            text = text if isinstance(text, str) else text.get("text", "")
            s = by_line.get(j, last)
            last = s
            src = files.get(s.get("part"))
            if src is None or not text.strip():
                continue
            d = tts(text, wd / "shots" / f"tl{k}_{j}.wav") + 0.35
            out = wd / "shots" / f"p{k}_{j}.mp4"
            run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-t", f"{d:.2f}", "-vf",
                 f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},"
                 f"tpad=stop_mode=clone:stop_duration={d:.2f},trim=duration={d:.2f},setsar=1",
                 "-af", f"volume=0.5,apad,atrim=duration={d:.2f}", *_enc(out)])
            pieces.append(out)
            lines.append({"t": round(t + 0.15, 2), "text": text})
            t += d
        if not pieces:
            continue
        clip = concat(pieces, wd / "shots" / f"item{k}.mp4")
        it["vo_lines"] = lines
        ch = next((f["channel"] for f in footage if f["item"] == k), "")
        clips.append({"src": clip, "a": 0.0, "b": probe(clip)["duration"], "peak": max(1.5, t / 2),
                      "streamer": None, "credit": f"Footage: {ch}" if ch else None, "rank": n - k, "it": it})
    if len(clips) < 3:
        raise fetch.UserError("Не хватает скачанных кадров для лонга — запусти помощника на ноутбуке ещё раз.")
    for k, c in enumerate(clips):  # places renumbered if a point had no footage
        c["rank"] = len(clips) - k
    return clips


# ------------------------------------------------------------------ render
def render(job: dict, wd: Path, progress) -> dict:
    sc = job["result"]["script"]
    lib = {m["id"]: m for m in job["result"].get("library") or []}
    items = sc["items"]
    n = len(items)
    wd.mkdir(parents=True, exist_ok=True)

    # 1. download every moment (1080p piece of the VOD). The range is the SAME one the anchoring AI watched,
    #    so the narrator's timestamps match the picture exactly. Topic videos use the shots the laptop downloaded.
    clips = prepare_topic_clips(job, wd, progress) if job["result"].get("footage") else []
    for k, it in enumerate(items if not clips else []):
        m = lib[it["cand"]]
        progress(f"⬇️ Скачиваю момент {k + 1}/{n} ({m.get('streamer')})")
        a, b = clip_range(m)
        dl = fetch.download(m["vod"], wd / f"clip{k}", a, b)
        src = dl["path"]
        length = probe(src)["duration"]
        peak = it.get("punch_t") or audio.loudest_moment(src) or length * 0.7
        clips.append({"src": src, "a": 0.0, "b": length, "peak": max(1.5, min(float(peak), length - 0.6)),
                      "streamer": m.get("streamer"), "rank": n - k, "it": it})

    # 2. voice
    progress("🎙 Озвучиваю сценарий")
    vo = {}
    for key in ("hook_vo", "promise_vo", "stakes_vo", "intro_vo", "mid_cta_vo", "rehook_vo", "outro_cta_vo",
                "endscreen_vo"):
        vo[key] = wd / f"{key}.wav"
        tts(sc[key], vo[key])
    for c in clips:
        r, it = c["rank"], c["it"]
        anchored = it.get("vo_lines") or [{"t": 0.3, "text": it.get("vo_before") or ""}]
        c["lines"] = []
        for j, ln in enumerate(anchored):
            if not (ln.get("text") or "").strip():
                continue
            wav = wd / f"vl{r}_{j}.wav"
            d = tts(ln["text"], wav)
            t0 = min(max(0.2, float(ln.get("t") or 0)), max(0.2, c["b"] - d - 0.3))
            if c["lines"] and t0 < c["lines"][-1][3]:      # never two lines at once
                t0 = c["lines"][-1][3] + 0.2
            c["lines"].append((t0, wav, ln["text"], t0 + d))
        c["va"] = wd / f"va{r}.wav"
        tts(it.get("vo_after") or "", c["va"])

    # 3. segments
    progress("🎬 Монтирую лонг")
    segs: list[Path] = []
    chapters: list[tuple[float, str]] = [(0.0, "Intro")]
    narration: list[tuple[float, str]] = []      # (time in the final video, line) for the AI check
    t = 0.0

    def add(p: Path, d: float) -> None:
        nonlocal t
        segs.append(p)
        t += d

    top = clips[-1]
    narration.append((0.2, sc["hook_vo"]))
    # hook: 3 s of the #1 moment, then the grab line on a freeze, fast montage with the promise, grid with "#1 ?"
    add(wd / "s_hook.mp4", clip_segment(wd / "s_hook.mp4", top["src"], max(0, top["peak"] - 2.6), top["peak"] + 0.4,
                                        wd, rank=None, streamer=top["streamer"], vo=vo["hook_vo"],
                                        vo_text=sc["hook_vo"], duck=0.5))
    others = [(c["src"], c["peak"]) for c in clips[:-1]][-6:]
    pdur = probe(vo["promise_vo"])["duration"] + 0.4
    narration.append((t + 0.15, sc["promise_vo"]))
    add(wd / "s_promise.mp4", montage(wd / "s_promise.mp4", others, max(0.8, pdur / max(1, len(others))), wd,
                                      vo["promise_vo"], sc["promise_vo"], total=pdur))
    grid = " ".join(f"#{c['rank']}" for c in clips[:-1]) + " #1?"
    bg0 = frame_at(top["src"], top["peak"], wd / "bg_top.jpg")
    narration.append((t + 0.35, sc["stakes_vo"]))
    add(wd / "s_stakes.mp4", card(wd / "s_stakes.mp4", bg0, "#1 = ?", grid if len(grid) < 40 else "", vo["stakes_vo"],
                                  wd, vo_text=sc["stakes_vo"], hit="impact", big=260))
    bg1 = frame_at(clips[0]["src"], clips[0]["a"] + 1, wd / "bg_intro.jpg")
    narration.append((t + 0.35, sc["intro_vo"]))
    add(wd / "s_intro.mp4", card(wd / "s_intro.mp4", bg1, "TOP " + str(n), "STREAMERS VS GTA 6", vo["intro_vo"], wd,
                                 vo_text=sc["intro_vo"], hit="pop", big=220))

    mid_after = max(1, round(n * 0.25))          # soft CTA at ~25%
    rehook_before = 3                            # before the top 3 (~60-70%)
    for k, c in enumerate(clips):
        r = c["rank"]
        if r == rehook_before:
            bgr = frame_at(clips[-1]["src"], clips[-1]["peak"], wd / "bg_re.jpg")
            pieces = [(x["src"], x["peak"]) for x in clips[-3:]]
            rdur = probe(vo["rehook_vo"])["duration"] + 0.4
            narration.append((t + 0.15, sc["rehook_vo"]))
            add(wd / "s_rehook.mp4", montage(wd / "s_rehook.mp4", pieces, max(0.8, rdur / 3), wd, vo["rehook_vo"],
                                             sc["rehook_vo"], total=rdur))
        chapters.append((t, f"#{r} {c['it']['label_en'].title()}"))
        bg = frame_at(c["src"], c["a"] + 0.5, wd / f"bg{r}.jpg")
        # short number card (no talking over a still), then the clip with the narrator ON the action
        add(wd / f"s{r}_card.mp4", card(wd / f"s{r}_card.mp4", bg, f"#{r}", c["it"]["label_en"].upper()[:28],
                                        None, wd, min_len=1.4))
        for t0, _, text, _ in c["lines"]:
            narration.append((t + t0, text))
        add(wd / f"s{r}_clip.mp4", clip_segment(wd / f"s{r}_clip.mp4", c["src"], c["a"], c["b"], wd, rank=r,
                                                streamer=c["streamer"], lines=[x[:3] for x in c["lines"]],
                                                credit=c.get("credit")))
        if (c["it"].get("vo_after") or "").strip():
            narration.append((t, c["it"]["vo_after"]))
            bga = frame_at(c["src"], c["b"] - 0.2, wd / f"bga{r}.jpg")
            add(wd / f"s{r}_after.mp4", card(wd / f"s{r}_after.mp4", bga, "", "", c["va"], wd, min_len=1.0, hit=None,
                                             vo_text=c["it"]["vo_after"]))
        if k + 1 == mid_after:
            narration.append((t + 0.35, sc["mid_cta_vo"]))
            add(wd / "s_cta.mp4", card(wd / "s_cta.mp4", bg, "SUBSCRIBE", "NEW GTA 6 MOMENTS DAILY", vo["mid_cta_vo"],
                                       wd, vo_text=sc["mid_cta_vo"], hit="pop", big=200))

    chapters.append((t, "Your Turn"))
    best = [(x["src"], x["peak"]) for x in clips[-4:]]
    odur = probe(vo["outro_cta_vo"])["duration"] + 0.4
    narration.append((t + 0.15, sc["outro_cta_vo"]))
    add(wd / "s_outro.mp4", montage(wd / "s_outro.mp4", best, max(1.0, odur / 4), wd, vo["outro_cta_vo"],
                                    sc["outro_cta_vo"], total=odur))
    add(wd / "s_end.mp4", card(wd / "s_end.mp4", bg0, "WATCH NEXT", "MORE GTA 6 CHAOS", vo["endscreen_vo"], wd,
                               vo_text=sc["endscreen_vo"], hit="pop", min_len=15.0, big=200))

    progress("🧩 Склеиваю всё вместе")
    full = concat(segs, wd / f"long_{job['id']}.mp4")
    total = probe(full)["duration"]
    thumb = thumbnail(top["src"], top["peak"], sc.get("thumbnail_text") or "THEY LOST IT", wd / "thumb.jpg", wd)
    chap_txt = "\n".join(f"{fmt(a)} {name}" for a, name in chapters)
    streamers = sorted({c["streamer"] for c in clips if c["streamer"]})
    desc = (sc.get("description") or "").strip() + "\n\nChapters:\n" + chap_txt
    if streamers:
        desc += "\n\nStreamers:\n" + "\n".join(f"https://twitch.tv/{s}" for s in streamers)
    if job["result"].get("sources"):
        desc += "\n\nFootage from:\n" + "\n".join(f"{s['channel']} — https://youtu.be/{s['id']}"
                                                 for s in job["result"]["sources"])
    return {"path": full, "thumb": thumb, "duration": total, "chapters": chapters, "description": desc,
            "narration": narration}


# ------------------------------------------------------------------ narrator <-> picture
ANCHOR_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "beats": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "t": {"type": "NUMBER", "description": "seconds from the start of THIS clip"},
            "what": {"type": "STRING", "description": "what is visible / said at that second"}},
            "required": ["t", "what"]}},
        "punch_t": {"type": "NUMBER", "description": "second of the punchline / biggest reaction"},
        "vo_lines": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "t": {"type": "NUMBER", "description": "when the narrator starts this line (clip seconds)"},
            "text": {"type": "STRING", "description": "max 16 words, describes what is ON SCREEN at t"}},
            "required": ["t", "text"]}},
        "vo_after": {"type": "STRING", "description": "max 12 words, said right after the clip"},
    },
    "required": ["beats", "punch_t", "vo_lines", "vo_after"],
}


def clip_range(m: dict) -> tuple[float, float]:
    """The exact piece of the VOD used for both anchoring and the final edit."""
    return max(0.0, float(m["start"]) - 2), float(m["end"]) + 2


def anchor_items(script: dict, library: list[dict], wd: Path, progress=lambda s: None) -> dict:
    """The AI watches every chosen clip and ties each narrator line to the second it describes:
    lines go on the setup / quiet moments, never over the streamer's punchline."""
    from . import scout
    lib = {m["id"]: m for m in library}
    n = len(script["items"])
    for k, it in enumerate(script["items"]):
        m = lib.get(it["cand"])
        if not m:
            continue
        progress(f"🎯 ИИ привязывает текст к кадрам: момент {k + 1}/{n}")
        a, b = clip_range(m)
        try:
            low = scout.download_low(m["vod"], wd / f"anchor{k}", a, b)
            proxy = brain.make_proxy(low, wd / f"anchor{k}_p.mp4")
            prompt = f"""This clip ({b - a:.0f} s) is place #{n - k} of a GTA 6 streamers countdown video.
Streamer: {m.get('streamer')}. What happens: {m.get('what_ru')} ({m.get('context_en')}).
Draft narration (keep the meaning and tone, adapt to the picture): before: "{it.get('vo_before')}" after: "{it.get('vo_after')}"
Watch AND listen. Then:
1. beats: 4-8 key seconds with what is on screen.
2. punch_t: the second of the punchline / biggest reaction.
3. vo_lines: 1-3 narrator lines, English, short, funny, spoken. Each line starts at t and must describe what the
   viewer SEES at that time. The first line starts at 0.3-1.5 s and sets up the situation. Put lines only on the
   setup or on quiet moments: NEVER from punch_t-1.5 to punch_t+4 (the streamer's reaction must be heard), never
   over the streamer saying something important. Total narration at most 35% of the clip.
4. vo_after: one short punchy line after the clip."""
            out = brain.generate([brain.video_part(proxy), {"text": prompt}], ANCHOR_SCHEMA, temperature=0.5,
                                 patience=True)
            pt = float(out.get("punch_t") or 0)
            lines = []
            for ln in sorted(out.get("vo_lines") or [], key=lambda x: float(x.get("t") or 0)):
                t0 = float(ln.get("t") or 0)
                if pt and pt - 1.5 <= t0 <= pt + 4:     # the rule above, enforced
                    continue
                if 0 <= t0 < (b - a) - 1 and (ln.get("text") or "").strip():
                    lines.append({"t": round(t0, 2), "text": ln["text"].strip()})
            if lines:
                it["vo_lines"] = lines[:3]
            if (out.get("vo_after") or "").strip():
                it["vo_after"] = out["vo_after"].strip()
            it["punch_t"] = pt or None
            it["beats"] = out.get("beats") or []
        except Exception as e:  # noqa: BLE001 - the draft line still works, just not anchored
            print(f"anchor {k} failed: {e}")
    return script


CHECK_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "score": {"type": "INTEGER", "description": "1-10: how well narration matches the picture"},
        "problems": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "t": {"type": "STRING", "description": "mm:ss"},
            "issue_ru": {"type": "STRING"}}, "required": ["t", "issue_ru"]}},
        "summary_ru": {"type": "STRING"},
    },
    "required": ["score", "problems", "summary_ru"],
}


def check_alignment(full: Path, narration: list[tuple[float, str]], wd: Path) -> dict:
    """The AI watches the finished long video and lists places where the narrator talks about something else
    than what is on screen (or covers the streamer's punchline)."""
    proxy = wd / "check.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(full), "-vf", "fps=1,scale=-2:240", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "34", "-c:a", "aac", "-b:a", "32k", "-ac", "1", str(proxy)])
    lines = "\n".join(f"{fmt(t)} {text}" for t, text in narration)
    prompt = f"""This is a finished GTA 6 streamers countdown video. Below is the narrator's script with the time each
line starts. Watch AND listen. Find every place where the narration does not match what is on screen, is said
too early or too late, or talks over the streamer's punchline. Be strict but fair; give mm:ss of each problem.
NARRATION:
{lines}"""
    return brain.generate([brain.video_part(proxy, force_upload=True), {"text": prompt}], CHECK_SCHEMA,
                          temperature=0.2, media_low=True, patience=True)


def preview(full: Path, out: Path, max_mb: float = 45.0) -> Path:
    """Small copy that fits into Telegram (bots can send up to 50 MB)."""
    dur = probe(full)["duration"]
    kbps = int(max_mb * 8 * 1024 / max(1.0, dur)) - 70
    run(["ffmpeg", "-y", "-v", "error", "-i", str(full), "-vf", "scale=854:-2", "-c:v", "libx264", "-preset", "veryfast",
         "-b:v", f"{max(150, kbps)}k", "-maxrate", f"{max(150, kbps)}k", "-bufsize", f"{max(300, 2 * kbps)}k",
         "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-movflags", "+faststart", str(out)])
    return out


def store_full(path: Path) -> str | None:
    """Full-quality file goes to the private repo release 'longs'. Returns the asset page URL."""
    repo, token = os.environ.get("MATERIALS_REPO"), os.environ.get("MATERIALS_TOKEN")
    if not repo or not token:
        return None
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    r = requests.get(f"https://api.github.com/repos/{repo}/releases/tags/longs", headers=h, timeout=30)
    if r.status_code == 404:
        r = requests.post(f"https://api.github.com/repos/{repo}/releases", headers=h, timeout=30,
                          json={"tag_name": "longs", "name": "longs", "body": "Finished long videos"})
    r.raise_for_status()
    rel = r.json()
    up = re.sub(r"\{.*\}$", "", rel["upload_url"])
    with path.open("rb") as f:
        u = requests.post(f"{up}?name={path.name}", headers={**h, "Content-Type": "video/mp4"}, data=f, timeout=1800)
    u.raise_for_status()
    return rel.get("html_url")
