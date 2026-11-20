"""Virtual camera: fast-but-smooth eased moves of zoom and centre over the game picture.

A move goes from (z0, c0) to (z1, c1) in T seconds using an easing curve, then keeps a tiny drift
so the frame is never perfectly still. Centres are normalised (0..1) in the source frame.
"""
from __future__ import annotations

from dataclasses import dataclass


def ease_out_cubic(p: float) -> float:
    return 1 - (1 - p) ** 3


def ease_in_out_cubic(p: float) -> float:
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def ease_in_out_quint(p: float) -> float:
    return 16 * p ** 5 if p < 0.5 else 1 - (-2 * p + 2) ** 5 / 2


def linear(p: float) -> float:
    return p


EASES = {"push": ease_out_cubic, "pan": ease_in_out_cubic, "whip": ease_in_out_quint,
         "hold": linear, "react": ease_out_cubic, "cut": linear}


@dataclass
class Move:
    kind: str
    z0: float
    z1: float
    c0: tuple[float, float]
    c1: tuple[float, float]
    T: float
    dur: float               # whole part duration
    drift: float = 0.02      # extra zoom added over the rest of the part after the move

    def at(self, u: float) -> tuple[float, tuple[float, float]]:
        """Zoom and centre at local time u (seconds since part start)."""
        T = max(1e-3, self.T)
        p = EASES.get(self.kind, linear)(min(1.0, max(0.0, u / T)))
        z = self.z0 + (self.z1 - self.z0) * p
        cx = self.c0[0] + (self.c1[0] - self.c0[0]) * p
        cy = self.c0[1] + (self.c1[1] - self.c0[1]) * p
        if u > T and self.dur > T:
            z += self.drift * (u - T) / (self.dur - T)
        return z, (cx, cy)


def build_moves(parts: list[dict], style: dict) -> list[Move]:
    """Turn the AI plan into concrete camera moves (same logic for every renderer)."""
    k = float(style.get("zoom_strength", 1.0))
    moves: list[Move] = []
    cur_z, cur_c = 1.0, (0.5, 0.5)
    for p in parts:
        dur = p["b"] - p["a"]
        target = (float(p.get("x", 0.5)), float(p.get("y", 0.5)))
        zoom = 1.0 + (float(p.get("zoom", 1.2)) - 1.0) * k
        zoom = min(2.0, max(1.0, zoom))
        mt = min(0.45, max(0.2, dur * 0.35))
        cam = p.get("camera", "hold")
        if p["shot"] == "react":
            rz = 1.0 + (float(style.get("react_zoom", 1.18)) - 1.0) * k
            moves.append(Move("react", 1.0, rz, (0.5, 0.5), (0.5, 0.5), min(0.28, dur), dur, 0.03))
            continue  # webcam close-up does not change where the game camera is
        if cam == "push":
            m = Move("push", max(1.0, zoom - 0.2 * k), zoom, cur_c, target, mt, dur)
        elif cam == "pan":
            m = Move("pan", cur_z, zoom, cur_c, target, min(dur, mt * 1.4), dur)
        elif cam == "whip":
            m = Move("whip", cur_z, zoom, cur_c, target, min(dur, 0.18), dur)
        elif cam == "cut":
            m = Move("cut", zoom, zoom, target, target, 1e-3, dur)
        else:  # hold: slow drift so the picture never freezes
            m = Move("hold", zoom, zoom + 0.05 * k, target, target, dur, dur, 0.0)
        moves.append(m)
        cur_z, cur_c = m.z1, m.c1
    return moves


def crop_rect(z: float, c: tuple[float, float], src_w: int, src_h: int, out_aspect: float,
              avoid: tuple[int, int, int, int] | None = None) -> tuple[int, int, int, int]:
    """Pixel crop (x, y, w, h) of the source for zoom z centred near c, kept inside the frame.

    zoom 1.0 = the tallest crop that fits the output aspect. If `avoid` (the webcam box) would be
    mostly inside the crop, the centre is pushed away from it.
    """
    h = src_h / max(1.0, z)
    w = h * out_aspect
    if w > src_w:
        w = src_w
        h = w / out_aspect
    cx, cy = c[0] * src_w, c[1] * src_h
    if avoid:
        ax, ay, aw, ah = avoid
        acx = ax + aw / 2
        # if the target is inside the webcam, slide horizontally to the side with more room
        if ax <= cx <= ax + aw and ay <= cy <= ay + ah:
            cx = ax - w / 2 if acx > src_w / 2 else ax + aw + w / 2
    x = min(max(0.0, cx - w / 2), src_w - w)
    y = min(max(0.0, cy - h / 2), src_h - h)
    return int(round(x)), int(round(y)), int(round(w)), int(round(h))
