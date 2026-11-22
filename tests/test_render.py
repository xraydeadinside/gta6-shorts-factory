"""Full render on a synthetic 'stream': dark-blue game with a moving white box + a green checkerboard
'webcam' in the top-right corner + a tone with noise. Checks the real ffmpeg output."""
import shutil
import subprocess

import numpy as np
import pytest

from engine import audio, camera, compose, plan, subs, webcam
from engine.util import probe, style

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
CAM = (1440, 0, 480, 270)


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    d = tmp_path_factory.mktemp("src")
    p = d / "source.mp4"
    # the 'face cam' content moves (like a real person), only its frame stays still
    checker = ("nullsrc=s=480x270:r=30,geq=r=0:"
               "g='if(mod(floor((X+T*45)/20)+floor((Y+T*33)/20),2),255,0)':b=0")
    game = "color=c=0x202850:s=1920x1080:r=30:d=8"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", game, "-f", "lavfi", "-t", "8", "-i", checker,
        "-f", "lavfi", "-i", "sine=f=330:d=8,volume=0.3", "-f", "lavfi", "-i", "anoisesrc=d=8:a=0.05",
        "-filter_complex",
        "[0:v]drawbox=x='mod(t*220,1300)':y=600:w=160:h=160:color=white:t=fill[g];"
        "[g][1:v]overlay=1440:0:shortest=1[v];[2:a][3:a]amix=inputs=2[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-t", "8", str(p)], check=True)
    return p


def test_webcam_box_snaps_to_overlay(source):
    pr = probe(source)
    rough = (0.73, 0.02, 0.24, 0.22)  # a sloppy hint, like an AI would give
    res = webcam.detect(source, pr["duration"], pr["width"], pr["height"], hint=rough)
    assert res["present"]
    x, y, w, h = res["box"]
    assert abs(x - CAM[0]) < 30 and y < 30 and abs((x + w) - 1920) < 30 and abs((y + h) - 270) < 30


def test_full_render(source, tmp_path):
    st = style({"min_len": 0})  # exact timings of the synthetic edit are checked below
    raw = [
        {"role": "hook", "start": 5.0, "end": 6.5, "shot": "game", "camera": "push", "x": 0.5, "y": 0.6, "zoom": 1.5},
        {"role": "setup", "start": 0.0, "end": 3.0, "shot": "game", "camera": "hold", "x": 0.3, "y": 0.6, "zoom": 1.1},
        {"role": "punchline", "start": 3.0, "end": 5.0, "shot": "game", "camera": "push", "x": 0.87, "y": 0.1, "zoom": 1.6},
        {"role": "reaction", "start": 5.0, "end": 6.0, "shot": "react", "camera": "hold", "x": 0.5, "y": 0.5, "zoom": 1},
        {"role": "payoff", "start": 6.0, "end": 8.0, "shot": "game", "camera": "pan", "x": 0.6, "y": 0.6, "zoom": 1.2},
    ]
    parts = plan.split_for_pace(plan.clean_parts(raw, 8.0, st, True), [])
    tl = plan.timeline(parts)
    total = tl[-1]["ob"]
    assert abs(total - 9.5) < 0.01
    cam = {"present": True, "box": CAM, "face": (0.5, 0.45)}
    moves = camera.build_moves(tl, st)
    cam_h, _ = compose.layout_sizes(st)
    words = [{"w": "no", "s": 3.2, "e": 3.4, "i": 0}, {"w": "way", "s": 3.5, "e": 3.9, "i": 1}]
    ass = subs.build_ass(tmp_path / "s.ass", words_out=plan.map_words(words, tl), punch_idx={1},
                         title="He did not expect THIS", title_accent_word="THIS", shouts=[{"t": 4.0, "text": "NO WAY"}],
                         streamer="tester", style=st, cam_h=cam_h,
                         layouts=[(p["oa"], p["ob"], "react" if p["shot"] == "react" else "game") for p in tl],
                         total=total)
    snd = audio.build(source, tl, plan.cut_points(tl), tmp_path / "a.wav", style=st, total=total)
    out = tmp_path / "short.mp4"
    compose.encode(compose.render_frames(source, tl, moves, cam, st, total), snd, ass, out, total)

    pr = probe(out)
    assert (pr["width"], pr["height"]) == (1080, 1920)
    assert abs(pr["duration"] - total) < 0.25
    assert pr["has_audio"]
    assert out.stat().st_size < compose.MAX_MB * 1024 * 1024 * 1.05
    assert abs(audio.measure_lufs(out) - (-14)) < 1.2

    # frame at 2.5 s (setup, split layout): webcam on top, game below WITHOUT the webcam
    frame = _frame(out, 2.5)
    top, bottom = frame[60:cam_h - 60], frame[cam_h + 20:1900]
    assert _green_share(top) > 0.25                     # checkerboard webcam is in the top panel
    assert _green_share(bottom) < 0.005                 # ...and never duplicated in the game
    # punchline aims at the webcam corner -> camera must slide away, still no webcam in the game
    f2 = _frame(out, 4.6)
    assert _green_share(f2[cam_h + 20:1900]) < 0.005
    # reaction close-up (output 6.5..7.5 s): the webcam fills the big top area
    f3 = _frame(out, 7.0)
    assert _green_share(f3[100:compose.REACT_CAM_H - 100]) > 0.25
    # no progress bar at the bottom
    assert _bar(_frame(out, 8.0)) < 50


def _frame(video, t):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(1920, 1080, 3)


def _green_share(img):
    b, g, r = img[..., 0].astype(int), img[..., 1].astype(int), img[..., 2].astype(int)
    return float(((g > 200) & (r < 70) & (b < 70)).mean())


def _bar(frame):
    row = frame[1912].astype(int)
    return int(((row[:, 2] > 200) & (row[:, 1] > 180) & (row[:, 0] < 90)).sum())


def test_whoosh_is_synthetic_and_short():
    w = audio.whoosh()
    assert w.shape[1] == 2 and 0.3 < len(w) / audio.SR < 0.5 and np.abs(w).max() <= 1.0


def test_sfx_sounds_are_short_and_clean():
    from engine import sfx
    for k in sfx.KINDS:
        y = sfx.render(k)
        assert y.ndim == 2 and y.shape[1] == 2
        assert 0.05 < len(y) / sfx.SR < 1.5
        assert np.isfinite(y).all() and np.abs(y).max() <= 1.0 + 1e-6
