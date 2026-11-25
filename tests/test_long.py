"""Long video: script selection and a full (tiny) render with a synthetic stream clip and a fake voice."""
import shutil
import subprocess

import pytest

from engine import longform
from engine.util import probe

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _lib(n=8):
    return [{"id": f"m{i}", "vod": "https://www.twitch.tv/videos/1", "start": 100 + i * 100, "end": 130 + i * 100,
             "streamer": ["kaicenat", "ishowspeed", "xqc"][i % 3], "score": 9 - i % 3, "kind": "funny",
             "context_en": "X", "what_ru": f"момент {i}", "why_ru": "y"} for i in range(n)]


def _script(lib, n):
    vo = "This is a short test line for the narrator."
    return {"title": "Streamers React to GTA 6", "alt_titles": [], "thumbnail_text": "THEY LOST IT",
            "thumbnail_idea": "", "hook_vo": vo, "promise_vo": vo, "stakes_vo": vo, "intro_vo": vo,
            "mid_cta_vo": vo, "rehook_vo": vo, "outro_cta_vo": vo, "endscreen_vo": vo,
            "items": [{"cand": m["id"], "label_en": f"MOMENT {k}", "vo_before": vo, "vo_after": "Wow.",
                       "what_ru": "x", "punch_t": 6.0,
                       "vo_lines": [{"t": 1.0, "text": vo}, {"t": 12.0, "text": "And then this."}]}
                      for k, m in enumerate(lib[:n])],
            "description": "GTA 6 test", "tags": ["gta 6"], "explanation_ru": "", "minutes": 4}


def test_script_keeps_only_known_moments(monkeypatch):
    lib = _lib()
    sc = _script(lib, 6)
    sc["items"].append({"cand": "nope", "label_en": "X", "vo_before": "", "vo_after": "", "what_ru": ""})
    monkeypatch.setattr(longform.brain, "generate", lambda *a, **k: dict(sc))
    out = longform.write_script(lib, 6, None, None)
    assert [it["cand"] for it in out["items"]] == [m["id"] for m in lib[:6]] and out["minutes"] == 6
    assert longform.n_items(10) == 10 and longform.n_items(15) == 15 and longform.n_items(5) == 6


def test_full_long_render(monkeypatch, tmp_path):
    src = tmp_path / "stream.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=20",
                    "-f", "lavfi", "-i", "sine=f=330:d=20", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-ar", "48000", "-ac", "2", str(src)], check=True)

    def fake_download(url, wd, a, b):
        wd.mkdir(parents=True, exist_ok=True)
        return {"path": src, "meta": {}}

    def fake_tts(text, out):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=600:d=1.5", "-ar", "48000",
                        "-ac", "2", str(out)], check=True)
        return 1.5

    monkeypatch.setattr(longform.fetch, "download", fake_download)
    monkeypatch.setattr(longform, "tts", fake_tts)
    lib = _lib(6)
    job = {"id": 1, "result": {"script": _script(lib, 6), "library": lib}}
    out = longform.render(job, tmp_path / "w", progress=lambda s: None)
    pr = probe(out["path"])
    assert pr["width"] == 1920 and pr["height"] == 1080 and pr["has_audio"]
    assert pr["duration"] > 60                        # 6 places + hook + intro + outro + 15 s end screen
    assert out["chapters"][0] == (0.0, "Intro") and out["chapters"][1][1].startswith("#6")
    assert "0:00 Intro" in out["description"] and "twitch.tv/kaicenat" in out["description"]
    texts = [x[1] for x in out["narration"]]
    assert texts.count("And then this.") == 6                 # every anchored line made it into the video
    times = [x[0] for x in out["narration"]]
    assert times == sorted(times) and times[-1] < pr["duration"]
    assert out["thumb"].exists()
    pv = longform.preview(out["path"], tmp_path / "pv.mp4", max_mb=5)
    assert pv.stat().st_size < 5.5 * 1024 * 1024


def test_anchor_drops_lines_on_the_punchline(monkeypatch, tmp_path):
    lib = _lib(2)
    sc = _script(lib, 2)
    for it in sc["items"]:
        it.pop("vo_lines"), it.pop("punch_t")

    def fake_low(url, out, a, b):
        p = out.with_suffix(".mp4")
        p.write_bytes(b"x")
        return p

    monkeypatch.setattr("engine.scout.download_low", fake_low)
    monkeypatch.setattr(longform.brain, "make_proxy", lambda src, out: src)
    monkeypatch.setattr(longform.brain, "video_part", lambda p, force_upload=False: {"text": "video"})
    monkeypatch.setattr(longform.brain, "generate", lambda *a, **k: {
        "beats": [{"t": 1, "what": "car"}], "punch_t": 10.0, "vo_after": "Classic.",
        "vo_lines": [{"t": 9.5, "text": "on the punchline"}, {"t": 0.5, "text": "He steals a jet."},
                     {"t": 16, "text": "Then the cops arrive."}]})
    out = longform.anchor_items(sc, lib, tmp_path)
    it = out["items"][0]
    assert [l["text"] for l in it["vo_lines"]] == ["He steals a jet.", "Then the cops arrive."]
    assert it["punch_t"] == 10.0 and it["vo_after"] == "Classic."
