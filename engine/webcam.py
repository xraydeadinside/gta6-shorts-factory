"""Find the streamer's webcam overlay automatically.

Two independent signals, combined:
  1. Faces (OpenCV Haar cascade) that stay in the SAME place across the clip. Game characters move,
     the streamer's face in an overlay does not.
  2. Overlay borders: the webcam frame is a rectangle whose edges stay put while the game moves,
     so edges that persist across many frames outline it.
Gemini also looks at the clip and may give its own box; it is used as a hint / tie-breaker.
All boxes are (x, y, w, h) in source pixels.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

Box = tuple[int, int, int, int]


def sample_frames(video: Path, duration: float, n: int = 24, width: int = 960) -> list[np.ndarray]:
    """n evenly spaced frames, resized to `width` (BGR)."""
    import cv2
    cap = cv2.VideoCapture(str(video))
    frames = []
    for i in range(n):
        t = (i + 0.5) * duration / n
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, f = cap.read()
        if not ok:
            continue
        h, w = f.shape[:2]
        frames.append(cv2.resize(f, (width, int(h * width / w))))
    cap.release()
    return frames


def _faces(frames: list[np.ndarray]) -> list[list[tuple[float, float, float, float]]]:
    import cv2
    if not hasattr(cv2, "CascadeClassifier"):  # OpenCV build without Haar cascades: rely on borders + AI
        return [[] for _ in frames]
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    out = []
    for f in frames:
        g = cv2.equalizeHist(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))
        h, w = g.shape
        found = casc.detectMultiScale(g, scaleFactor=1.1, minNeighbors=6, minSize=(int(w * 0.025),) * 2,
                                      maxSize=(int(w * 0.3),) * 2)
        out.append([(x / w, y / h, fw / w, fh / h) for (x, y, fw, fh) in found])
    return out


def stable_face(frames: list[np.ndarray]) -> tuple[float, float, float, float] | None:
    """Normalised (x, y, w, h) of a face that sits still in >=30% of frames."""
    dets = _faces(frames)
    allf = [(i, d) for i, ds in enumerate(dets) for d in ds]
    if not allf:
        return None
    best, best_n = None, 0
    for _, d in allf:
        cx, cy = d[0] + d[2] / 2, d[1] + d[3] / 2
        members = {}
        for i, e in allf:
            ex, ey = e[0] + e[2] / 2, e[1] + e[3] / 2
            if abs(ex - cx) < 0.045 and abs(ey - cy) < 0.06 and 0.6 < e[2] / d[2] < 1.6:
                members.setdefault(i, e)
        if len(members) > best_n:
            best, best_n = list(members.values()), len(members)
    if best_n < max(3, int(0.3 * len(frames))):
        return None
    arr = np.array(best)
    return tuple(float(v) for v in np.median(arr, axis=0))  # type: ignore[return-value]


def persistent_edges(frames: list[np.ndarray]) -> np.ndarray:
    """Fraction of frames in which each pixel is an edge (0..1)."""
    import cv2
    acc = None
    for f in frames:
        e = cv2.Canny(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), 60, 160) > 0
        acc = e.astype(np.float32) if acc is None else acc + e
    return acc / max(1, len(frames))


def _snap(edges: np.ndarray, box: tuple[float, float, float, float], search: float = 0.08,
          thr: float = 0.45) -> tuple[float, float, float, float]:
    """Move each side of a normalised box to the strongest persistent straight line nearby."""
    h, w = edges.shape
    x0, y0, bw, bh = box
    x1, y1 = x0 + bw, y0 + bh
    res = [x0, y0, x1, y1]

    def best_line(axis: str, pos: float, lo: float, hi: float) -> float:
        span = slice(int(lo * (h if axis == "v" else w)), max(int(hi * (h if axis == "v" else w)), 1))
        n = w if axis == "v" else h
        a, b = int(max(0, (pos - search) * n)), int(min(n - 1, (pos + search) * n))
        best_p, best_s = pos, 0.0
        for p in range(a, b + 1):
            col = edges[span, p] if axis == "v" else edges[p, span]
            if col.size == 0:
                continue
            s = float((col > 0.6).mean())  # share of the side covered by a persistent edge
            if s > best_s:
                best_p, best_s = p / n, s
        return best_p if best_s >= thr else pos

    res[0] = best_line("v", x0, y0, y1)
    res[2] = best_line("v", x1, y0, y1)
    res[1] = best_line("h", y0, x0, x1)
    res[3] = best_line("h", y1, x0, x1)
    nx0, ny0, nx1, ny1 = res
    if nx1 - nx0 < 0.05 or ny1 - ny0 < 0.05:
        return box
    return (nx0, ny0, nx1 - nx0, ny1 - ny0)


def _snap_to_frame_edges(box: tuple[float, float, float, float], m: float = 0.035):
    x, y, w, h = box
    x1, y1 = x + w, y + h
    if x < m: x = 0.0
    if y < m: y = 0.0
    if 1 - x1 < m: x1 = 1.0
    if 1 - y1 < m: y1 = 1.0
    return (x, y, x1 - x, y1 - y)


def _clamp(b):
    x, y, w, h = b
    x, y = max(0.0, x), max(0.0, y)
    w, h = min(1 - x, w), min(1 - y, h)
    return (x, y, w, h)


def detect(video: Path, duration: float, src_w: int, src_h: int,
           hint: tuple[float, float, float, float] | None = None) -> dict:
    """Return {"present", "box": (x,y,w,h) px, "face": (cx,cy) normalised in box, "how"}."""
    frames = sample_frames(video, duration)
    if not frames:
        return {"present": False, "box": None, "face": None, "how": "no frames"}
    face = stable_face(frames)
    edges = persistent_edges(frames)
    box, how = None, ""
    if face is not None:
        fx, fy, fw, fh = face
        # a webcam is roughly 3.4x the face width and 3.2x the face height, centred a bit above
        guess = _clamp((fx + fw / 2 - fw * 1.7, fy + fh / 2 - fh * 1.5, fw * 3.4, fh * 3.2))
        if hint and _contains(hint, (fx + fw / 2, fy + fh / 2)):
            guess = hint
            how = "лицо + ИИ"
        else:
            how = "лицо"
        box = _snap_to_frame_edges(_snap(edges, guess))
    elif hint:
        box = _snap_to_frame_edges(_snap(edges, hint))
        how = "ИИ"
    if box is None or box[2] * box[3] > 0.6 or box[2] < 0.06 or box[3] < 0.06:
        return {"present": False, "box": None, "face": None, "how": how or "не найдена"}
    x, y, w, h = box
    px = (int(round(x * src_w)), int(round(y * src_h)), int(round(w * src_w)), int(round(h * src_h)))
    if face is not None:
        fcx = (face[0] + face[2] / 2 - x) / w
        fcy = (face[1] + face[3] / 2 - y) / h
        fcn = (min(1.0, max(0.0, fcx)), min(1.0, max(0.0, fcy)))
    else:
        fcn = (0.5, 0.45)
    return {"present": True, "box": px, "face": fcn, "how": how}


def _contains(b, p) -> bool:
    return b[0] <= p[0] <= b[0] + b[2] and b[1] <= p[1] <= b[1] + b[3]


def from_box_norm(b: list[float] | None) -> tuple[float, float, float, float] | None:
    """Gemini returns [x1, y1, x2, y2] normalised 0..1 -> (x, y, w, h)."""
    if not b or len(b) != 4:
        return None
    x1, y1, x2, y2 = (float(v) for v in b)
    if max(x1, y1, x2, y2) > 1.5:  # 0..1000 scale
        x1, y1, x2, y2 = (v / 1000 for v in (x1, y1, x2, y2))
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    if x2 - x1 < 0.04 or y2 - y1 < 0.04:
        return None
    return _clamp((x1, y1, x2 - x1, y2 - y1))


def contact_sheet(frames: list[np.ndarray], path: Path) -> Path:
    """Small image of 4 frames for debugging / for Gemini."""
    import cv2
    pick = [frames[i] for i in np.linspace(0, len(frames) - 1, 4).astype(int)]
    small = [cv2.resize(f, (480, int(f.shape[0] * 480 / f.shape[1]))) for f in pick]
    top = np.hstack(small[:2])
    bot = np.hstack(small[2:])
    cv2.imwrite(str(path), np.vstack([top, bot]))
    return path


if __name__ == "__main__":  # manual check: python -m engine.webcam video.mp4
    import sys
    from .util import probe
    p = Path(sys.argv[1])
    pr = probe(p)
    print(detect(p, pr["duration"], pr["width"], pr["height"]))
