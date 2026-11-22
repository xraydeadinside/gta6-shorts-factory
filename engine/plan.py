"""Make the AI's edit plan safe and concrete: clamp times, enforce Shorts rules, build the timeline.

A *part* is a piece of the source: {role, a, b, shot, camera, x, y, zoom, note}
  role   : hook | setup | punchline | reaction | payoff
  shot   : game (webcam on top + game below) | react (big webcam close-up)
  camera : push | pan | whip | hold | cut
Times a/b are seconds in the downloaded source file.
"""
from __future__ import annotations

ROLES = ("hook", "setup", "punchline", "reaction", "payoff")
SHOTS = ("game", "react")
CAMS = ("push", "pan", "whip", "hold", "cut")
MIN_PART = 0.5


def _f(v, d=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def clean_parts(raw: list[dict], duration: float, style: dict, has_cam: bool) -> list[dict]:
    """Validate parts from the AI. Hook may be a teaser copied from later; the rest go forward in time."""
    parts = []
    for p in raw or []:
        a = min(max(0.0, _f(p.get("start"), _f(p.get("a")))), duration)
        b = min(max(0.0, _f(p.get("end"), _f(p.get("b")))), duration)
        if b - a < MIN_PART:
            continue
        role = p.get("role") if p.get("role") in ROLES else "setup"
        shot = p.get("shot") if p.get("shot") in SHOTS else "game"
        if shot == "react" and not has_cam:
            shot = "game"
        cam = p.get("camera") if p.get("camera") in CAMS else "hold"
        if shot == "react":
            b = min(b, a + 2.2)  # reaction close-ups are SHORT
        parts.append({
            "role": role, "a": round(a, 3), "b": round(b, 3), "shot": shot, "camera": cam,
            "x": min(0.95, max(0.05, _f(p.get("x"), 0.5))),
            "y": min(0.95, max(0.05, _f(p.get("y"), 0.5))),
            "zoom": min(2.0, max(1.0, _f(p.get("zoom"), 1.15))),
            "note": str(p.get("note") or "")[:200],
        })
    if not parts:
        return []
    # hook may be out of order (teaser); others: drop overlaps going back in time
    head, rest = (parts[0], parts[1:]) if parts[0]["role"] == "hook" else (None, parts)
    fixed, last_b = [], -1.0
    for p in rest:
        if p["a"] < last_b - 0.05:
            p["a"] = last_b
            if p["b"] - p["a"] < MIN_PART:
                continue
        fixed.append(p)
        last_b = p["b"]
    if head:
        head["b"] = min(head["b"], head["a"] + 3.0)  # teaser hook is max 3 s
        fixed.insert(0, head)
    return _fit_length(_reach_min(fixed, style, duration), style)


def _reach_min(parts: list[dict], style: dict, duration: float) -> list[dict]:
    """A story shorter than min_len is usually unclear: give back context from the source.

    1) start the first story part earlier (more setup), 2) let the last part run longer (more payoff),
    3) put back the shortest pieces the AI skipped between parts."""
    min_len = float(style.get("min_len", 20))
    total = sum(p["b"] - p["a"] for p in parts)
    need = min(min_len, duration) - total
    if need <= 0.05 or not parts:
        return parts
    body = [p for p in parts if not (p is parts[0] and p["role"] == "hook" and len(parts) > 1)]
    first = body[0]
    if first["shot"] == "react":  # never stretch a close-up: add a game part before it
        if first["a"] > MIN_PART:
            add = min(need, first["a"])
            new = dict(first, role="setup", shot="game", camera="hold", a=round(first["a"] - add, 3),
                       b=first["a"], note="контекст")
            parts.insert(parts.index(first), new)
            need -= add
    else:
        add = min(need, first["a"])
        first["a"] = round(first["a"] - add, 3)
        need -= add
    if need > 0.05:
        last = parts[-1]
        add = min(need, duration - last["b"])
        if add > 0:
            if last["shot"] == "react":
                parts.append(dict(last, role="payoff", shot="game", camera="hold", a=last["b"],
                                  b=round(last["b"] + add, 3), note="продолжение"))
            else:
                last["b"] = round(last["b"] + add, 3)
            need -= add
    if need > 0.05:  # gaps between consecutive forward parts, shortest first
        idx = [i for i in range(1, len(parts)) if parts[i]["a"] - parts[i - 1]["b"] > 0.05
               and not (i - 1 == 0 and parts[0]["role"] == "hook")]
        for i in sorted(idx, key=lambda i: parts[i]["a"] - parts[i - 1]["b"]):
            if need <= 0.05:
                break
            gap = parts[i]["a"] - parts[i - 1]["b"]
            add = min(gap, need)
            if parts[i]["shot"] != "react":
                parts[i]["a"] = round(parts[i]["a"] - add, 3)
            elif parts[i - 1]["shot"] != "react":
                parts[i - 1]["b"] = round(parts[i - 1]["b"] + add, 3)
            else:
                continue
            need -= add
    return parts


def _fit_length(parts: list[dict], style: dict) -> list[dict]:
    max_len = float(style.get("max_len", 58))
    total = sum(p["b"] - p["a"] for p in parts)
    if total <= max_len:
        return parts
    # trim setup first, then payoff, never the punchline/reaction
    order = sorted(range(len(parts)), key=lambda i: {"setup": 0, "payoff": 1, "hook": 2}.get(parts[i]["role"], 9))
    for i in order:
        over = total - max_len
        if over <= 0:
            break
        p = parts[i]
        can = (p["b"] - p["a"]) - MIN_PART * 2
        if can <= 0:
            continue
        cut = min(can, over)
        if p["role"] == "setup":
            p["a"] += cut  # keep the end of the setup (closest to the punch)
        else:
            p["b"] -= cut
        total -= cut
    while total > max_len and len(parts) > 1:
        drop = parts.pop()  # last resort
        total -= drop["b"] - drop["a"]
    return parts


def split_for_pace(parts: list[dict], words: list[dict], max_piece: float = 3.0) -> list[dict]:
    """Visual change at least every ~3 s: split long game parts at word gaps, alternate punch-in."""
    out = []
    for p in parts:
        dur = p["b"] - p["a"]
        if p["shot"] != "game" or dur <= max_piece + 0.8 or p["camera"] == "pan":
            out.append(p)
            continue
        n = int(dur // max_piece) + 1
        step = dur / n
        cuts = []
        for k in range(1, n):
            t = p["a"] + k * step
            # snap to the nearest gap between words (cleaner cut)
            gaps = [(w1["e"] + w2["s"]) / 2 for w1, w2 in zip(words, words[1:])
                    if p["a"] + 0.6 < (w1["e"] + w2["s"]) / 2 < p["b"] - 0.6]
            if gaps:
                g = min(gaps, key=lambda x: abs(x - t))
                if abs(g - t) < step * 0.4:
                    t = g
            cuts.append(round(t, 3))
        bounds = [p["a"], *cuts, p["b"]]
        for k in range(len(bounds) - 1):
            q = dict(p, a=bounds[k], b=bounds[k + 1])
            if k > 0:
                q["camera"] = "cut"  # jump-cut: instant re-frame with a different zoom
                q["zoom"] = min(2.0, p["zoom"] + (0.12 if k % 2 else 0.0))
                q["sub"] = True
            out.append(q)
    return out


def timeline(parts: list[dict], intro: float = 0.0) -> list[dict]:
    """Add output times (oa, ob) to each part."""
    t = intro
    out = []
    for p in parts:
        q = dict(p, oa=round(t, 3))
        t += p["b"] - p["a"]
        q["ob"] = round(t, 3)
        out.append(q)
    return out


def cut_points(tl: list[dict]) -> list[float]:
    """Output times of hard cuts between main parts (for the whoosh). Jump-cut sub-pieces excluded."""
    return [p["oa"] for p in tl[1:] if not p.get("sub")]


def map_words(words: list[dict], tl: list[dict]) -> list[dict]:
    """Words in output time. A word that falls in several parts (teaser hook) appears each time."""
    out = []
    for p in tl:
        for i, w in enumerate(words):
            mid = (w["s"] + w["e"]) / 2
            if p["a"] <= mid < p["b"]:
                s = p["oa"] + max(0.0, w["s"] - p["a"])
                e = p["oa"] + min(p["b"], w["e"]) - p["a"]
                out.append({"w": w["w"], "s": round(s, 3), "e": round(max(e, s + 0.08), 3), "i": i})
    out.sort(key=lambda x: x["s"])
    return out


def fallback_plan(duration: float, loud_peak: float | None, has_cam: bool, style: dict) -> list[dict]:
    """Used when the AI is unavailable: setup -> punch at the loudest moment -> reaction."""
    peak = loud_peak if loud_peak is not None else duration * 0.6
    max_len = min(float(style.get("max_len", 58)), 40.0)
    a = max(0.0, peak - max_len * 0.65)
    b = min(duration, a + max_len)
    raw = [
        {"role": "hook", "start": max(0, peak - 1.0), "end": min(duration, peak + 1.2), "camera": "push", "zoom": 1.35},
        {"role": "setup", "start": a, "end": max(a + 1, peak - 1.2), "camera": "hold", "zoom": 1.05},
        {"role": "punchline", "start": max(a + 1, peak - 1.2), "end": min(b, peak + 1.8), "camera": "push", "zoom": 1.4},
    ]
    if has_cam:
        raw.append({"role": "reaction", "start": min(b, peak + 1.8), "end": min(duration, peak + 3.4), "shot": "react"})
        raw.append({"role": "payoff", "start": min(duration, peak + 3.4), "end": b, "camera": "pan", "zoom": 1.1})
    else:
        raw.append({"role": "payoff", "start": min(b, peak + 1.8), "end": b, "camera": "pan", "zoom": 1.1})
    return clean_parts(raw, duration, style, has_cam)
