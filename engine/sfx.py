"""Sound design: short synthesized effects (no copyright, no downloads) placed on what happens on screen.

  pop     - text appears (title, pop-up shouts)
  impact  - the punchline hits / reaction close-up / crash or explosion in the game
  riser   - tension builds up right before the punchline (ends exactly on it)
  swish   - fast camera move (whip / push)
  whoosh  - hard cut between parts (the old effect, in audio.whoosh)
  glitch  - something absurd / broken happens (WTF physics)

Volumes are moderate on purpose: the streamer's voice must stay on top.
"""
from __future__ import annotations

import numpy as np

SR = 48000
KINDS = ["impact", "riser", "pop", "swish", "glitch"]

# base loudness of each effect before the user's sfx_volume (speech is ~0.3-0.6 peak before normalising)
GAIN = {"pop": 0.32, "impact": 0.5, "riser": 0.22, "swish": 0.26, "glitch": 0.24, "whoosh": 0.35}
# effects closer than this to an already placed effect are dropped (no sound salad)
MIN_GAP = 0.35
PRIORITY = {"impact": 5, "riser": 4, "glitch": 3, "pop": 2, "swish": 1}


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(SR * seconds), dtype=np.float32) / SR


def _stereo(y: np.ndarray, width: float = 0.0) -> np.ndarray:
    y = y / (np.abs(y).max() + 1e-9)
    if width:
        d = int(SR * 0.004 * width)  # tiny delay on one side = wider
        r = np.concatenate([np.zeros(d, np.float32), y[:len(y) - d]]) if d else y
        return np.stack([y, r], axis=1).astype(np.float32)
    return np.stack([y, y], axis=1).astype(np.float32)


def pop(seed: int = 0) -> np.ndarray:
    """Bubbly 'pop': fast falling sine + a tiny click."""
    t = _t(0.11)
    f = 1100 * np.exp(-t * 28) + 380 + 40 * (seed % 3)
    ph = 2 * np.pi * np.cumsum(f) / SR
    body = np.sin(ph) * np.exp(-t * 38)
    click = np.random.default_rng(seed).standard_normal(len(t)).astype(np.float32) * np.exp(-t * 900) * 0.4
    return _stereo((body + click).astype(np.float32), 0.5)


def impact(seed: int = 0) -> np.ndarray:
    """Deep cinematic hit: sub-bass drop + noisy transient + short tail."""
    t = _t(0.85)
    f = 95 * np.exp(-t * 6) + 42
    ph = 2 * np.pi * np.cumsum(f) / SR
    sub = np.sin(ph) * np.exp(-t * 4.2)
    rng = np.random.default_rng(seed + 11)
    noise = rng.standard_normal(len(t)).astype(np.float32)
    k = np.exp(-t * 60)
    spec = np.fft.rfft(noise * k)
    freqs = np.fft.rfftfreq(len(t), 1 / SR)
    crack = np.fft.irfft(spec * (freqs < 2500), len(t)).astype(np.float32)
    crack /= np.abs(crack).max() + 1e-9
    y = sub * 1.0 + crack * 0.55
    y = np.tanh(y * 1.6)  # a bit of saturation = punchier
    return _stereo(y.astype(np.float32), 1.0)


def riser(seconds: float = 1.1, seed: int = 0) -> np.ndarray:
    """Tension build: rising noise + rising tone, cut off sharply at the end."""
    t = _t(seconds)
    u = t / seconds
    rng = np.random.default_rng(seed + 5)
    noise = rng.standard_normal(len(t)).astype(np.float32)
    spec = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(len(t), 1 / SR)
    air = np.fft.irfft(spec * ((freqs > 1500) & (freqs < 9000)), len(t)).astype(np.float32)
    air /= np.abs(air).max() + 1e-9
    f = 180 * (2 ** (u * 2.3))
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR)
    env = u ** 2.2
    y = (air * 0.6 + tone * 0.4) * env
    y[-int(SR * 0.01):] *= np.linspace(1, 0, int(SR * 0.01), dtype=np.float32)
    return _stereo(y.astype(np.float32), 0.8)


def swish(seed: int = 0) -> np.ndarray:
    """Short airy swish for fast camera moves."""
    t = _t(0.22)
    rng = np.random.default_rng(seed + 3)
    noise = rng.standard_normal(len(t)).astype(np.float32)
    spec = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(len(t), 1 / SR)
    band = np.exp(-((np.log(freqs + 1) - np.log(3500)) ** 2) / 0.5)
    y = np.fft.irfft(spec * band, len(t)).astype(np.float32)
    env = np.sin(np.pi * (t / t[-1]) ** 0.5) ** 2
    return _stereo(y * env, 0.7)


def glitch(seed: int = 0) -> np.ndarray:
    """Digital stutter: square-wave bursts at random pitches."""
    rng = np.random.default_rng(seed + 7)
    parts = []
    for _ in range(6):
        n = int(SR * rng.uniform(0.025, 0.05))
        t = np.arange(n, dtype=np.float32) / SR
        f = rng.uniform(300, 1600)
        sq = np.sign(np.sin(2 * np.pi * f * t)).astype(np.float32) * rng.uniform(0.5, 1.0)
        parts.append(sq * np.linspace(1, 0.6, n, dtype=np.float32))
    y = np.concatenate(parts)
    return _stereo(y * 0.8, 0.0)


MAKERS = {"pop": pop, "impact": impact, "riser": riser, "swish": swish, "glitch": glitch}


def render(kind: str, seed: int = 0) -> np.ndarray:
    return MAKERS[kind](seed=seed)


def plan_events(*, tl: list[dict], total: float, intro_len: float, title_on: bool, shouts: list[dict],
                punch_times: list[float], ai_sfx: list[dict]) -> list[dict]:
    """[{t, kind}] in output seconds, cleaned so effects never pile up.

    t is when the effect HITS (for a riser: where it ends)."""
    ev: list[dict] = []
    if title_on:
        ev.append({"t": intro_len + 0.02, "kind": "pop"})
    for s in shouts:
        ev.append({"t": float(s["t"]), "kind": "pop"})
    for t in punch_times[:2]:
        ev.append({"t": t, "kind": "impact"})
        if t > intro_len + 1.6:
            ev.append({"t": t, "kind": "riser"})
    for p in tl:
        if p.get("sub"):
            continue
        if p.get("shot") == "react":
            ev.append({"t": p["oa"], "kind": "impact"})
        elif p.get("camera") in ("whip", "push") and p["oa"] > intro_len + 0.5:
            ev.append({"t": p["oa"] + 0.05, "kind": "swish"})
    # effects the AI asked for, at moments of the game action (source seconds -> output seconds)
    for s in ai_sfx:
        kind = s.get("kind")
        if kind not in KINDS:
            continue
        for p in tl:
            if p["a"] <= float(s.get("t", -1)) < p["b"]:
                ev.append({"t": p["oa"] + float(s["t"]) - p["a"], "kind": kind})
                break
    # keep the most important effect when two are too close; risers only need their end free
    ev = [e for e in ev if 0 <= e["t"] < total - 0.05]
    ev.sort(key=lambda e: -PRIORITY[e["kind"]])
    kept: list[dict] = []
    for e in ev:
        if e["kind"] == "riser":
            if any(k["kind"] == "riser" for k in kept):
                continue
            kept.append(e)
            continue
        if any(k["kind"] != "riser" and abs(k["t"] - e["t"]) < MIN_GAP for k in kept):
            continue
        kept.append(e)
    kept.sort(key=lambda e: e["t"])
    return kept


def mix_into(mix: np.ndarray, events: list[dict], volume: float = 1.0) -> np.ndarray:
    """Add the effects to the soundtrack; speech is ducked a little under impacts so the hit is felt."""
    for i, e in enumerate(events):
        y = render(e["kind"], seed=i) * GAIN[e["kind"]] * volume
        if e["kind"] == "riser":
            start = int((e["t"] * SR)) - len(y)
        elif e["kind"] in ("swish",):
            start = int((e["t"] - 0.1) * SR)
        else:
            start = int(e["t"] * SR)
        a, b = max(0, start), min(len(mix), start + len(y))
        if b <= a:
            continue
        seg = y[a - start: b - start]
        if e["kind"] == "impact":  # duck the original sound by ~4 dB for 0.25 s
            d = min(b, a + int(0.25 * SR))
            mix[a:d] *= 0.63
        mix[a:b] += seg
    return mix
