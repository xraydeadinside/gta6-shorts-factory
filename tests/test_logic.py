"""Fast tests without video: links, camera, plan rules, captions, SEO, chat spikes."""
from engine import camera, fetch, plan, seo, subs
from engine.util import style
from engine.vod_chat import find_spikes

ST = style({})


# ---------------------------------------------------------------- links
def test_timecodes():
    assert fetch.parse_timecode("1h2m3s") == 3723
    assert fetch.parse_timecode("3723") == 3723
    assert fetch.parse_timecode("90s") == 90
    assert fetch.parse_timecode("1:02:03") == 3723
    assert fetch.parse_timecode("2:05") == 125
    assert fetch.parse_timecode("abc") is None


def test_parse_urls():
    c = fetch.parse_url("https://www.twitch.tv/kaicenat/clip/FunnySlug-abc123")
    assert c["kind"] == "twitch_clip" and c["streamer"] == "kaicenat" and c["id"] == "FunnySlug-abc123"
    assert fetch.parse_url("clips.twitch.tv/SomeSlug")["kind"] == "twitch_clip"
    v = fetch.parse_url("https://www.twitch.tv/videos/2345678901?t=1h02m10s")
    assert v["kind"] == "twitch_vod" and v["id"] == "2345678901" and v["t"] == 3730
    y = fetch.parse_url("https://youtu.be/dQw4w9WgXcQ?t=95")
    assert y["kind"] == "youtube" and y["t"] == 95 and y["id"] == "dQw4w9WgXcQ"
    s = fetch.parse_url("https://youtube.com/shorts/abcDEF12345")
    assert s["kind"] == "youtube" and s["id"] == "abcDEF12345"
    w = fetch.parse_url("https://www.youtube.com/watch?v=abcDEF12345&t=1m5s")
    assert w["t"] == 65


def test_window_rules():
    vod = fetch.parse_url("https://www.twitch.tv/videos/1?t=100s")
    a, b = fetch.plan_window(vod, 7200)
    assert a == 60 and b == 125
    clip = fetch.parse_url("https://clips.twitch.tv/X")
    assert fetch.plan_window(clip, 30) == (None, None)
    long_no_t = fetch.parse_url("https://www.youtube.com/watch?v=abc")
    try:
        fetch.plan_window(long_no_t, 3600)
        raise AssertionError("must ask for a timecode")
    except fetch.UserError:
        pass
    assert fetch.plan_window(vod, 7200, 10, 50) == (10, 50)


# ---------------------------------------------------------------- camera
def test_easing_endpoints():
    for f in camera.EASES.values():
        assert abs(f(0.0)) < 1e-9 and abs(f(1.0) - 1) < 1e-9


def test_moves_from_plan():
    parts = [
        {"a": 0, "b": 2, "shot": "game", "camera": "push", "x": 0.3, "y": 0.6, "zoom": 1.4},
        {"a": 2, "b": 4, "shot": "game", "camera": "pan", "x": 0.7, "y": 0.4, "zoom": 1.2},
        {"a": 4, "b": 5, "shot": "react", "camera": "hold", "x": 0.5, "y": 0.5, "zoom": 1.0},
        {"a": 5, "b": 6, "shot": "game", "camera": "whip", "x": 0.2, "y": 0.5, "zoom": 1.5},
        {"a": 6, "b": 9, "shot": "game", "camera": "hold", "x": 0.4, "y": 0.5, "zoom": 1.1},
    ]
    m = camera.build_moves(parts, ST)
    push, pan, react, whip, hold = m
    assert abs(push.z0 - 1.2) < 1e-6 and abs(push.z1 - 1.4) < 1e-6 and push.c1 == (0.3, 0.6)
    assert pan.c0 == (0.3, 0.6) and pan.c1 == (0.7, 0.4)        # continues from where push ended
    assert react.kind == "react" and abs(react.T - 0.28) < 1e-6 and abs(react.z1 - 1.18) < 1e-6
    assert whip.T == 0.18 and whip.c0 == (0.7, 0.4)              # reaction does not move the game camera
    z_start, _ = hold.at(0)
    z_end, _ = hold.at(3)
    assert z_end > z_start                                        # hold never freezes
    z, c = push.at(10)
    assert z >= 1.4 and c == (0.3, 0.6)


def test_crop_stays_inside_and_avoids_webcam():
    cam = (1440, 0, 480, 270)
    for z in (1.0, 1.3, 2.0):
        for c in ((0.0, 0.0), (1.0, 1.0), (0.5, 0.5), (0.85, 0.1)):
            x, y, w, h = camera.crop_rect(z, c, 1920, 1080, 1080 / 1230, avoid=cam)
            assert 0 <= x and 0 <= y and x + w <= 1920 and y + h <= 1080
    # target inside the webcam -> camera slides away from it
    x, y, w, h = camera.crop_rect(1.8, (0.87, 0.1), 1920, 1080, 1080 / 1230, avoid=cam)
    assert x + w <= 1440 + 2


# ---------------------------------------------------------------- plan
def test_clean_parts_rules():
    raw = [
        {"role": "hook", "start": 20, "end": 26, "shot": "game", "camera": "push", "x": 0.5, "y": 0.5, "zoom": 1.5},
        {"role": "setup", "start": 2, "end": 10, "shot": "game", "camera": "hold", "x": 0.5, "y": 0.5, "zoom": 1.1},
        {"role": "punchline", "start": 9, "end": 14, "shot": "game", "camera": "push", "x": 0.4, "y": 0.5, "zoom": 1.6},
        {"role": "reaction", "start": 14, "end": 20, "shot": "react", "camera": "hold", "x": 0.5, "y": 0.5, "zoom": 1},
        {"role": "payoff", "start": 40, "end": 39, "shot": "game", "camera": "pan"},  # invalid, dropped
        {"role": "weird", "start": 20, "end": 23, "shot": "zzz", "camera": "nope", "zoom": 9},
    ]
    p = plan.clean_parts(raw, 30.0, ST, has_cam=True)
    assert p[0]["role"] == "hook" and p[0]["b"] - p[0]["a"] <= 3.0      # teaser max 3 s
    assert p[1]["a"] == 2 and p[2]["a"] >= p[1]["b"]                      # no going back in time
    react = [x for x in p if x["shot"] == "react"][0]
    assert react["b"] - react["a"] <= 2.2                                 # close-ups are short
    last = p[-1]
    assert last["role"] == "setup" and last["shot"] == "game" and last["camera"] == "hold" and last["zoom"] == 2.0
    no_cam = plan.clean_parts(raw, 30.0, ST, has_cam=False)
    assert all(x["shot"] == "game" for x in no_cam)


def test_max_length_enforced():
    raw = [{"role": "setup", "start": 0, "end": 50}, {"role": "punchline", "start": 50, "end": 60},
           {"role": "payoff", "start": 60, "end": 90}]
    p = plan.clean_parts(raw, 100, style({"max_len": 30}), True)
    assert sum(x["b"] - x["a"] for x in p) <= 30.01
    assert any(x["role"] == "punchline" and x["b"] - x["a"] == 10 for x in p)   # punchline untouched


def test_pace_split_and_timeline():
    words = [{"w": f"w{i}", "s": i * 0.5, "e": i * 0.5 + 0.35} for i in range(40)]
    parts = plan.clean_parts([{"role": "setup", "start": 0, "end": 10, "camera": "hold", "zoom": 1.1}], 20, ST, True)
    sp = plan.split_for_pace(parts, words)
    assert len(sp) >= 3 and all(x["b"] - x["a"] <= 4.0 for x in sp)
    assert sp[1]["camera"] == "cut" and sp[1].get("sub")
    tl = plan.timeline(sp, intro=2.0)
    assert tl[0]["oa"] == 2.0 and abs(tl[-1]["ob"] - 12.0) < 1e-6
    assert plan.cut_points(tl) == []                                  # jump cuts don't get whooshes
    mw = plan.map_words(words, tl)
    assert mw[0]["s"] >= 2.0 and all(a["s"] <= b["s"] for a, b in zip(mw, mw[1:]))


def test_fallback_plan():
    p = plan.fallback_plan(60.0, 30.0, True, ST)
    assert p and any(x["shot"] == "react" for x in p)
    assert sum(x["b"] - x["a"] for x in p) <= ST["max_len"] + 0.01


# ---------------------------------------------------------------- captions
def test_caption_groups_one_or_two_words():
    words = [{"w": w, "s": i * 0.3, "e": i * 0.3 + 0.25, "i": i}
             for i, w in enumerate("bro what is happening right now. extraordinary".split())]
    g2 = subs.group_words(words, 2)
    assert all(1 <= len(g["words"]) <= 2 for g in g2)
    assert any(len(g["words"]) == 2 for g in g2)
    assert all(g["e"] > g["s"] for g in g2)
    assert all(a["e"] <= b["s"] + 1e-6 for a, b in zip(g2, g2[1:]))      # no overlap / flicker
    g1 = subs.group_words(words, 1)
    assert all(len(g["words"]) == 1 for g in g1)
    assert subs.esc("{\\b1}x") == "(b1)x"


def test_ass_file(tmp_path):
    words = [{"w": "no", "s": 3.2, "e": 3.4, "i": 0}, {"w": "way!", "s": 3.5, "e": 3.9, "i": 1}]
    p = subs.build_ass(tmp_path / "a.ass", words_out=words, punch_idx={1}, title="He did NOT expect this",
                       title_accent_word="NOT", shouts=[{"t": 4.0, "text": "bro 💀"}], streamer="kaicenat",
                       style=ST, cam_h=690, layouts=[(0, 10, "game")], total=10)
    t = p.read_text(encoding="utf-8")
    assert "Style: Title" in t and "twitch.tv/kaicenat" in t and "BRO" in t
    assert t.count("Dialogue:") == 1 + 1 + 1 + 1   # 1 caption group, title, shout, credit
    assert "0:00:00.00,0:00:03.00,Title" in t


# ---------------------------------------------------------------- SEO
def test_seo_rules():
    ai = {"titles": ["Streamer loses it in gta vi #gta6", "x" * 90, ""],
          "hashtags": ["gta6", "#Shorts", "#kaicenat", "#funny", "#gaming", "#viceCity", "#extra"],
          "tags": ["GTA 6 funny"] * 3 + [f"tag{i}" for i in range(30)], "description": "He crashed the car."}
    out = seo.normalise(ai, "kaicenat", "https://twitch.tv/x")
    assert len(out["titles"]) == 3
    for t in out["titles"]:
        assert "GTA 6" in t[:40] and "#" not in t and len(t) <= 60
    hs = out["hashtags"]
    assert 3 <= len(hs) <= 5 and hs[-1] == "#Shorts" and "#GTA6" in hs
    assert len(out["tags"]) <= 15 and len(", ".join(out["tags"])) < 500 and out["tags"][0] == "gta 6"
    d = out["description"]
    assert "GTA 6" in d[:125] and "twitch.tv/kaicenat" in d and d.rstrip().endswith("#Shorts")


# ---------------------------------------------------------------- VOD chat
def test_chat_spikes():
    msgs = [(t * 2.0, "hi") for t in range(1500)]                        # calm chat for 50 min
    msgs += [(1200 + i * 0.1, "LMAO KEKW") for i in range(150)]          # big laugh at 20:00
    msgs += [(2400 + i * 0.2, "no way POG") for i in range(80)]          # hype at 40:00
    s = find_spikes(msgs, top=3)
    assert s and 1190 <= s[0]["peak"] <= 1230 and s[0]["kind"] == "funny"
    assert s[0]["start"] < s[0]["peak"] < s[0]["end"]
    assert any(2390 <= x["peak"] <= 2440 for x in s)


def test_chat_spikes_busy_chat():
    """A huge chat that is always busy (no global 2x jump) must still give its best moments."""
    import random
    rnd = random.Random(1)
    msgs = [(rnd.uniform(0, 14400), rnd.choice(["hi", "speed", "W", "chat", "LUL"])) for _ in range(150000)]
    msgs += [(5000 + i * 0.02, "KEKW LMAO") for i in range(900)]     # laugh burst at 1:23:20
    s = find_spikes(msgs, top=3)
    assert len(s) == 3
    assert any(4980 <= x["peak"] <= 5030 for x in s)


def test_chat_spikes_weighted_samples():
    """Sampled chat: (t, text, weight) where weight says how many real messages one read message stands for."""
    msgs = [(t * 5.0 + 1, "hi", 1.0) for t in range(720)]                 # calm hour, sampled every 5 s
    msgs += [(1800 + i * 0.05, "LMAO", 40.0) for i in range(20)]          # page filled in 1 s at 30:00
    s = find_spikes(msgs, top=2)
    assert 1790 <= s[0]["peak"] <= 1815 and s[0]["kind"] == "funny"


def test_scout_rank_prefers_score_no_overlap():
    from engine import scout
    cands = [
        {"start": 100, "end": 140, "score": 7},
        {"start": 120, "end": 160, "score": 9},      # overlaps the first, better
        {"start": 900, "end": 930, "score": 7},      # chat spike inside -> bonus
        {"start": 2000, "end": 2030, "score": 4},    # too weak
        {"start": 3000, "end": 3040, "score": 6},
    ]
    best = scout.rank(cands, [{"peak": 925, "score": 2.0}], top=3)
    assert [m["start"] for m in best] == [120, 900, 3000]


def test_scout_chat_hint_is_relative_to_piece():
    from engine import scout
    hint = scout.chat_hint_for([{"peak": 1900, "score": 2.5}, {"peak": 50, "score": 3}], 1800, 3600)
    assert "100s" in hint and "x2.5" in hint and "50s" not in hint
    assert scout.chat_hint_for([], 0, 1800) == ""
