"""Burned-in text as an ASS subtitle file: 1-2 word captions, screaming title, pop-up shouts, credit."""
from __future__ import annotations

import random
import re
from pathlib import Path

from .util import FONT_NAME, hex_to_ass

W, H = 1080, 1920


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def clean_word(w: str) -> str:
    w = re.sub(r"[^\w'’!?\-$%#&]", "", w, flags=re.UNICODE)
    return w.upper()


def group_words(words: list[dict], max_words: int = 2, max_chars: int = 13) -> list[dict]:
    """[{w,s,e,i}] (output time) -> [{text, s, e, idx:[...]}] of 1-2 words."""
    groups: list[dict] = []
    i = 0
    while i < len(words):
        w = words[i]
        text = clean_word(w["w"])
        if not text:
            i += 1
            continue
        g = {"words": [text], "s": w["s"], "e": w["e"], "idx": [w["i"]]}
        ends_sentence = w["w"][-1:] in ".?!,"
        if max_words >= 2 and i + 1 < len(words) and not ends_sentence and len(text) < 8:
            n = words[i + 1]
            nt = clean_word(n["w"])
            if nt and n["s"] - w["e"] < 0.25 and len(text) + 1 + len(nt) <= max_chars:
                g["words"].append(nt)
                g["e"] = n["e"]
                g["idx"].append(n["i"])
                i += 1
        groups.append(g)
        i += 1
    # each caption stays until the next starts (no flicker), but not longer than 1.2 s
    for k, g in enumerate(groups):
        nxt = groups[k + 1]["s"] if k + 1 < len(groups) else g["e"] + 0.4
        g["e"] = round(min(max(g["e"] + 0.05, min(nxt, g["s"] + 1.2)), nxt if nxt > g["s"] else g["e"] + 0.4), 3)
        if g["e"] <= g["s"]:
            g["e"] = g["s"] + 0.12
    return groups


def wrap_title(text: str, max_chars: int = 14) -> list[str]:
    words = text.upper().split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines[:3]


def build_ass(path: Path, *, words_out: list[dict], punch_idx: set[int], title: str, title_accent_word: str | None,
              shouts: list[dict], streamer: str | None, style: dict, cam_h: int, layouts: list[tuple[float, float, str]],
              total: float) -> Path:
    """Write the .ass file. `layouts` = [(oa, ob, 'game'|'react'|'nocam'|'intro')] for caption placement."""
    cap_size = int(style.get("caption_size", 100))
    white = hex_to_ass(style.get("caption_color", "#FFFFFF"))
    punch = hex_to_ass(style.get("punch_color", "#FFE600"))
    tcol = hex_to_ass(style.get("title_color", "#FFFFFF"))
    tacc = hex_to_ass(style.get("title_accent", "#FFE600"))
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT_NAME},{cap_size},{white},{white},&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,8,3,5,40,40,0,1
Style: Title,{FONT_NAME},128,{tcol},{tcol},&H00000000,&H90000000,0,0,0,0,100,100,2,0,1,14,6,5,30,30,0,1
Style: Shout,{FONT_NAME},92,{punch},{punch},&H00000000,&H90000000,0,0,0,0,100,100,1,0,1,9,4,5,30,30,0,1
Style: Credit,{FONT_NAME},38,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,3,1,1,24,24,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev: list[str] = []

    def layout_at(t: float) -> str:
        for a, b, kind in layouts:
            if a <= t < b:
                return kind
        return layouts[-1][2] if layouts else "game"

    # --- captions: lower third of the game area (or lower on a reaction close-up)
    tsec = float(style.get("title_seconds", 3.0))
    for g in group_words(words_out, int(style.get("caption_words", 2))):
        kind = layout_at(g["s"])
        y = 1560 if kind in ("react", "intro") else (cam_h + int((H - cam_h) * 0.64))
        is_punch = any(i in punch_idx for i in g["idx"])
        col = punch if is_punch else white
        size = int(cap_size * (1.25 if is_punch else 1.0))
        pop = r"\fscx70\fscy70\t(0,90,\fscx112\fscy112)\t(90,160,\fscx100\fscy100)"
        txt = esc(" ".join(g["words"]))
        ev.append(f"Dialogue: 2,{ass_time(g['s'])},{ass_time(g['e'])},Cap,,0,0,0,,"
                  f"{{\\an5\\pos(540,{y})\\fs{size}\\1c{col}{pop}}}{txt}")

    # --- title: first seconds, under the webcam (never over the face)
    if title:
        lines = wrap_title(title)
        acc = (title_accent_word or "").upper()
        out_lines = []
        for ln in lines:
            ws = []
            for w in ln.split():
                ws.append(f"{{\\1c{tacc}}}{esc(w)}{{\\1c{tcol}}}" if acc and acc in w else esc(w))
            out_lines.append(" ".join(ws))
        ty = cam_h + 40 + 70 * len(lines)
        anim = r"\fscx40\fscy40\frz-6\t(0,140,\fscx115\fscy115\frz2)\t(140,260,\fscx100\fscy100\frz-2)"
        fade = r"\fad(0,180)"
        ev.append(f"Dialogue: 5,{ass_time(0)},{ass_time(min(tsec, total))},Title,,0,0,0,,"
                  f"{{\\an5\\pos(540,{ty}){anim}{fade}}}" + r"\N".join(out_lines))

    # --- pop-up shouts: random spots in the game area, away from captions & title
    if style.get("shouts", True):
        rnd = random.Random(7)
        for s in shouts:
            t = float(s["t"])
            if t < tsec or t >= total - 0.3:
                continue
            x = rnd.choice([260, 540, 820])
            y = rnd.randint(cam_h + 230, cam_h + int((H - cam_h) * 0.45))
            rot = rnd.choice([-9, -6, 6, 9])
            anim = (rf"\frz{rot}\fscx20\fscy20\t(0,110,\fscx125\fscy125)\t(110,200,\fscx100\fscy100)"
                    r"\fad(0,150)")
            ev.append(f"Dialogue: 4,{ass_time(t)},{ass_time(min(total, t + 0.9))},Shout,,0,0,0,,"
                      f"{{\\an5\\pos({x},{y}){anim}}}{esc(s['text'].upper())[:22]}")

    # --- credit
    if style.get("watermark", True) and streamer:
        ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(total)},Credit,,0,0,0,,"
                  f"{{\\an1\\pos(26,{cam_h - 18})\\alpha&H30&}}twitch.tv/{esc(streamer)}")

    path.write_text(head + "\n".join(ev) + "\n", encoding="utf-8")
    return path
