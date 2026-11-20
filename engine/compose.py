"""Picture: build every 1080x1920 frame (webcam on top, moving game camera below, reaction close-ups,
progress bar), then encode with ffmpeg and burn the ASS text on top.

Frames are produced in Python/OpenCV so the virtual camera can change zoom smoothly per frame,
which plain ffmpeg crop filters cannot do reliably.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .camera import Move, crop_rect
from .util import FONTS_DIR, hex_to_bgr, probe, run

W, H, FPS = 1080, 1920, 30
MAX_MB = 19.0          # Telegram lets bots re-download files only up to 20 MB
REACT_CAM_H = 1240     # webcam height during a reaction close-up


def layout_sizes(style: dict) -> tuple[int, int]:
    cam_h = int(round(H * float(style.get("cam_height", 0.36)) / 2) * 2)
    return cam_h, H - cam_h


def frames_of(src: Path, a: float, n: int, w: int, h: int, vf: str | None = None):
    """Yield exactly n BGR frames starting at a seconds (repeat last frame if the source ends)."""
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{a:.3f}", "-i", str(src), "-frames:v", str(n)]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = w * h * 3
    last = None
    got = 0
    try:
        while got < n:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            last = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            got += 1
            yield last
    finally:
        proc.stdout.close()
        proc.wait()
    if last is None:
        last = np.zeros((h, w, 3), np.uint8)
    for _ in range(n - got):
        yield last


def cover_crop(box: tuple[int, int, int, int], aspect: float, focus: tuple[float, float], zoom: float = 1.0):
    """Crop inside `box` with the given aspect (w/h), centred on focus (normalised in box)."""
    bx, by, bw, bh = box
    if bw / bh > aspect:
        h = bh / zoom
        w = h * aspect
    else:
        w = bw / zoom
        h = w / aspect
    cx = bx + focus[0] * bw
    cy = by + focus[1] * bh
    x = min(max(bx, cx - w / 2), bx + bw - w)
    y = min(max(by, cy - h / 2), by + bh - h)
    return int(round(x)), int(round(y)), max(2, int(round(w))), max(2, int(round(h)))


def mosaic(frame: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    """Hide the webcam inside the game picture so it never shows twice."""
    import cv2
    x, y, w, h = box
    pad = 6
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(frame.shape[1], x + w + pad), min(frame.shape[0], y + h + pad)
    out = frame.copy()
    roi = out[y0:y1, x0:x1]
    if roi.size:
        small = cv2.resize(roi, (max(1, roi.shape[1] // 24), max(1, roi.shape[0] // 24)), interpolation=cv2.INTER_AREA)
        out[y0:y1, x0:x1] = cv2.GaussianBlur(cv2.resize(small, (roi.shape[1], roi.shape[0])), (0, 0), 9)
    return out


def _overlap(a, b) -> bool:
    return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])


def _resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    import cv2
    interp = cv2.INTER_AREA if img.shape[1] > w else cv2.INTER_CUBIC
    return cv2.resize(img, (w, h), interpolation=interp)


def _sharpen(img: np.ndarray) -> np.ndarray:
    import cv2
    blur = cv2.GaussianBlur(img, (0, 0), 1.2)
    return cv2.addWeighted(img, 1.35, blur, -0.35, 0)


def render_frames(src: Path, tl: list[dict], moves: list[Move], cam: dict, style: dict, total: float,
                  intro: Path | None = None, intro_len: float = 0.0):
    """Generator of output frames for the whole short."""
    pr = probe(src)
    sw, sh = pr["width"], pr["height"]
    cam_h, game_h = layout_sizes(style)
    has_cam = bool(cam and cam.get("present"))
    box = tuple(cam["box"]) if has_cam else None
    face = tuple(cam.get("face") or (0.5, 0.45)) if has_cam else (0.5, 0.5)
    bar_on = bool(style.get("progress_bar", True))
    bar_col = np.array(hex_to_bgr(style.get("progress_color", "#FFE600")), np.uint8)
    frame_no = 0
    n_total = int(round(total * FPS))

    def finish(canvas: np.ndarray) -> np.ndarray:
        nonlocal frame_no
        if bar_on:
            fill = int(W * min(1.0, (frame_no + 1) / max(1, n_total)))
            canvas[H - 16:H] = (canvas[H - 16:H] * 0.35).astype(np.uint8)
            canvas[H - 14:H - 2, :fill] = bar_col
        frame_no += 1
        return canvas

    if intro is not None and intro_len > 0:
        n = int(round(intro_len * FPS))
        vf = f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
        for f in frames_of(intro, 0.0, n, W, H, vf):
            yield finish(f.copy())

    game_aspect = W / game_h
    nocam_aspect = W / H
    last_game = (1.0, (0.5, 0.5))
    for p, mv in zip(tl, moves):
        n = int(round((p["ob"] - p["oa"]) * FPS))
        for k, fr in enumerate(frames_of(src, p["a"], n, sw, sh)):
            u = k / FPS
            canvas = np.empty((H, W, 3), np.uint8)
            if p["shot"] == "react" and has_cam:
                z, _ = mv.at(u)
                cx, cy, cw, ch = cover_crop(box, W / REACT_CAM_H, face, zoom=z)
                canvas[:REACT_CAM_H] = _sharpen(_resize(fr[cy:cy + ch, cx:cx + cw], W, REACT_CAM_H))
                gz, gc = last_game
                g = mosaic(fr, box)
                x, y, w, h = crop_rect(gz, gc, sw, sh, W / (H - REACT_CAM_H), avoid=box)
                canvas[REACT_CAM_H:] = _resize(g[y:y + h, x:x + w], W, H - REACT_CAM_H)
                canvas[REACT_CAM_H - 3:REACT_CAM_H + 3] = 0
            elif has_cam:
                z, c = mv.at(u)
                last_game = (z, c)
                x, y, w, h = crop_rect(z, c, sw, sh, game_aspect, avoid=box)
                g = mosaic(fr, box) if _overlap((x, y, w, h), box) else fr
                canvas[cam_h:] = _resize(g[y:y + h, x:x + w], W, game_h)
                # webcam panel: gentle breathing zoom so it is never static
                breath = 1.0 + 0.03 * (u / max(0.1, p["b"] - p["a"]))
                cx, cy, cw, ch = cover_crop(box, W / cam_h, face, zoom=breath)
                canvas[:cam_h] = _resize(fr[cy:cy + ch, cx:cx + cw], W, cam_h)
                canvas[cam_h - 3:cam_h + 3] = 0
            else:
                z, c = mv.at(u)
                x, y, w, h = crop_rect(z, c, sw, sh, nocam_aspect)
                canvas[:] = _resize(fr[y:y + h, x:x + w], W, H)
            yield finish(canvas)


def encode(frames, audio: Path, ass: Path, out: Path, total: float) -> Path:
    """Pipe frames into x264 with the soundtrack and burned subtitles. Keeps the file under MAX_MB."""
    vbit = int(min(9000, max(1500, (MAX_MB * 8 * 1024 / max(1.0, total)) * 0.93 - 160)))
    ass_arg = str(ass).replace("\\", "/").replace(":", r"\:")
    fonts = str(FONTS_DIR).replace("\\", "/").replace(":", r"\:")
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(audio),
           "-vf", f"ass='{ass_arg}':fontsdir='{fonts}'",
           "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-b:v", f"{vbit}k", "-maxrate", f"{int(vbit * 1.3)}k", "-bufsize", f"{vbit * 2}k", "-g", "60",
           "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
           "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for f in frames:
            proc.stdin.write(np.ascontiguousarray(f).tobytes())
    except BrokenPipeError:
        pass
    finally:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise RuntimeError("encode failed:\n" + err[-3000:])
    if out.stat().st_size > MAX_MB * 1024 * 1024 * 1.04:
        tmp = out.with_name(out.stem + "_small.mp4")
        vb2 = int(vbit * 0.75)
        run(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-c:v", "libx264", "-preset", "veryfast",
             "-b:v", f"{vb2}k", "-maxrate", f"{int(vb2 * 1.2)}k", "-bufsize", f"{vb2 * 2}k",
             "-c:a", "copy", "-movflags", "+faststart", str(tmp)])
        tmp.replace(out)
    return out


def thumbnail(video: Path, out: Path, t: float = 1.2) -> Path:
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
         "-vf", "scale=360:-2", "-q:v", "4", str(out)])
    return out
