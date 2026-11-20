"""Sound: cut the parts, add synthesized whooshes on cuts (no copyright), normalise to -14 LUFS."""
from __future__ import annotations

import json
import re
import wave
from pathlib import Path

import numpy as np

from .util import run

SR = 48000


def read_pcm(src: Path, a: float | None = None, b: float | None = None) -> np.ndarray:
    """Stereo float32 [n, 2] of the source (optionally a..b seconds)."""
    cmd = ["ffmpeg", "-v", "error"]
    if a is not None:
        cmd += ["-ss", f"{a:.3f}"]
    cmd += ["-i", str(src)]
    if b is not None and a is not None:
        cmd += ["-t", f"{max(0.01, b - a):.3f}"]
    cmd += ["-vn", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    import subprocess
    r = subprocess.run(cmd, capture_output=True, check=True)
    x = np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2)
    return x.copy()


def write_wav(path: Path, x: np.ndarray) -> Path:
    y = np.clip(x, -1, 1)
    pcm = (y * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def whoosh(seconds: float = 0.38, seed: int = 1) -> np.ndarray:
    """Synthesised 'whoosh': noise through a sweeping band-pass with a swell envelope. Stereo."""
    rng = np.random.default_rng(seed)
    n = int(SR * seconds)
    noise = rng.standard_normal(n).astype(np.float32)
    spec = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    # broad band around 1.8 kHz, then shape over time by mixing a low-passed and high-passed copy
    band = np.exp(-((np.log(freqs + 1) - np.log(1800)) ** 2) / 0.9)
    base = np.fft.irfft(spec * band, n).astype(np.float32)
    hi = np.fft.irfft(spec * (freqs > 3000), n).astype(np.float32)
    t = np.linspace(0, 1, n, dtype=np.float32)
    env = np.sin(np.pi * np.clip(t, 0, 1) ** 0.7) ** 2          # swell up, fall off
    sweep = 0.6 * base + 0.4 * hi * (1 - t)                       # bright at start, darker at end
    y = sweep * env
    y /= (np.abs(y).max() + 1e-9)
    pan = np.linspace(-0.6, 0.6, n, dtype=np.float32)            # moves left -> right
    left, right = y * (1 - pan) / 1.6, y * (1 + pan) / 1.6
    return np.stack([left, right], axis=1)


def build(src: Path, tl: list[dict], cuts: list[float], out: Path, *, style: dict,
          intro: Path | None = None, intro_len: float = 0.0, total: float | None = None) -> Path:
    """Assemble the soundtrack for the timeline, add whooshes, normalise loudness."""
    pieces = []
    if intro is not None and intro_len > 0:
        try:
            ia = read_pcm(intro, 0.0, intro_len)
        except Exception:  # noqa: BLE001 - intro without audio
            ia = np.zeros((0, 2), np.float32)
        need = int(intro_len * SR)
        ia = np.pad(ia[:need], ((0, max(0, need - len(ia))), (0, 0)))
        pieces.append(ia)
    for p in tl:
        x = read_pcm(src, p["a"], p["b"])
        need = int(round((p["ob"] - p["oa"]) * SR))
        x = np.pad(x[:need], ((0, max(0, need - len(x))), (0, 0)))
        f = min(len(x) // 4, int(0.012 * SR))  # tiny fades: no clicks on cuts
        if f > 0:
            ramp = np.linspace(0, 1, f, dtype=np.float32)[:, None]
            x[:f] *= ramp
            x[-f:] *= ramp[::-1]
        pieces.append(x)
    mix = np.concatenate(pieces) if pieces else np.zeros((SR, 2), np.float32)
    if total:
        need = int(round(total * SR))
        mix = np.pad(mix[:need], ((0, max(0, need - len(mix))), (0, 0)))

    if style.get("whoosh", True):
        vol = float(style.get("whoosh_volume", 0.35))
        points = list(cuts) + ([intro_len] if intro_len > 0 else [])
        for k, c in enumerate(points):
            w = whoosh(seed=k + 1) * vol
            start = int(max(0.0, c - 0.22) * SR)
            end = min(len(mix), start + len(w))
            if end > start:
                mix[start:end] += w[: end - start]

    raw = out.with_name(out.stem + "_raw.wav")
    write_wav(raw, mix)
    target = float(style.get("lufs", -14))
    loudnorm(raw, out, target)
    return trim_loudness(out, target)


def trim_loudness(path: Path, target: float, tol: float = 0.4) -> Path:
    """loudnorm can land 1-2 dB low when peaks are high: correct with gain + a true-peak limiter."""
    for _ in range(3):
        cur = measure_lufs(path)
        if cur < -70 or abs(cur - target) <= tol:
            break
        tmp = path.with_name(path.stem + "_fix.wav")
        run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-af",
             f"volume={target - cur:.2f}dB,alimiter=limit=0.84:attack=2:release=60:level=false",
             "-ar", str(SR), str(tmp)])
        tmp.replace(path)
    return path


def loudnorm(src: Path, out: Path, target: float = -14.0) -> Path:
    """Two-pass EBU R128 normalisation (accurate), true-peak -1.5 dB."""
    err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-af",
               f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    if not m:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11",
             "-ar", str(SR), str(out)])
        return out
    s = json.loads(m.group(0))
    if float(s["input_i"]) < -70:  # silence: nothing to normalise
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ar", str(SR), str(out)])
        return out
    af = (f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={s['input_i']}:measured_TP={s['input_tp']}:"
          f"measured_LRA={s['input_lra']}:measured_thresh={s['input_thresh']}:offset={s['target_offset']}:"
          f"linear=true")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", af, "-ar", str(SR), str(out)])
    return out


def measure_lufs(path: Path) -> float:
    err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"])
    vals = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    return float(vals[-1]) if vals else -99.0


def loudest_moment(src: Path) -> float | None:
    """Time (s) of the loudest 1-second window, for the fallback plan."""
    x = read_pcm(src)
    if len(x) < SR:
        return None
    e = (x.astype(np.float64) ** 2).mean(axis=1)
    cs = np.concatenate([[0.0], np.cumsum(e)])
    starts = np.arange(0, len(e) - SR, SR // 10)
    c = cs[starts + SR] - cs[starts]
    return float(starts[int(np.argmax(c))] / SR + 0.5)
