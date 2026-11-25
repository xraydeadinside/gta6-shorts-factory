import { test } from "node:test";
import assert from "node:assert/strict";
import {
  cardKeyboard, cardText, cleanStyle, extractUrl, fixTitle, flowKeyboard, parseCallback, parseCommand,
} from "../src/lib.js";
import { PANEL_HTML, LOGIN_HTML } from "../src/panel.js";

const job = { id: 123456, status: "ready", chosen: 1, title: null, flow_ok: 0 };
const res = {
  titles: ["GTA 6 cop chase goes wrong", "He did NOT see that coming", "GTA 6 streamer loses it"],
  description: "GTA 6 — a <wild> chase.", explanation: "Срезал скучное начало.", score: 8, duration: 24.4,
  streamer: "kaicenat", plan: [{ role: "hook", at: 0, shot: "game", camera: "push", note: "авария" }],
  webcam: { present: true, how: "лицо" }, viewed_pct: 72, risk: "долгая завязка",
};

test("titles always start with GTA 6 and stay short", () => {
  assert.equal(fixTitle("streamer crashes in gta vi #gta6"), "streamer crashes in GTA 6");
  assert.ok(fixTitle("He did not see that coming").startsWith("GTA 6: "));
  assert.ok(fixTitle("x ".repeat(80)).length <= 60);
});

test("commands and links", () => {
  assert.deepEqual(parseCommand("/clip https://clips.twitch.tv/Abc"), { cmd: "clip", arg: "https://clips.twitch.tv/Abc" });
  assert.deepEqual(parseCommand("/status@my_bot"), { cmd: "status", arg: "" });
  assert.equal(parseCommand("hello"), null);
  assert.equal(extractUrl("look twitch.tv/videos/123?t=1h2m3s pls"), "https://twitch.tv/videos/123?t=1h2m3s");
  assert.equal(extractUrl("no link"), null);
});

test("card text is valid, escaped and fits Telegram", () => {
  const t = cardText(job, res);
  assert.ok(t.includes("✅ 2. He did NOT see that coming"));
  assert.ok(t.includes("&lt;wild&gt;"));
  assert.ok(t.includes("Хук — наезд"));
  assert.ok(t.length <= 4096);
  const long = cardText(job, { ...res, explanation: "я".repeat(9000), description: "d".repeat(9000) });
  assert.ok(long.length <= 4096);
});

test("buttons: all actions present, callback data within 64 bytes", () => {
  const kb = cardKeyboard(job, res).inline_keyboard.flat();
  const acts = kb.map((b) => parseCallback(b.callback_data).act);
  for (const a of ["t", "nt", "ct", "cd", "rm", "fp", "pub", "rej"]) assert.ok(acts.includes(a), a);
  for (const b of [...kb, ...flowKeyboard(job.id).inline_keyboard.flat()]) {
    assert.ok(new TextEncoder().encode(b.callback_data).length <= 64);
  }
  assert.deepEqual(parseCallback("a:5:t:2"), { id: 5, act: "t", arg: "2" });
  assert.equal(cardKeyboard({ ...job, status: "rejected" }, res).inline_keyboard.flat()[0].callback_data, "a:123456:un");
});

test("style settings are sanitised", () => {
  const s = cleanStyle({ zoom_strength: "1.3", punch_color: "#ff0000", shouts: "false", evil: "x", title_color: "red" });
  assert.deepEqual(s, { zoom_strength: 1.3, punch_color: "#FF0000", shouts: false });
});

test("panel pages are complete html", () => {
  for (const h of [PANEL_HTML, LOGIN_HTML]) {
    assert.ok(h.startsWith("<!doctype html>") && h.trim().endsWith("</html>"));
  }
  assert.ok(PANEL_HTML.includes("/api/jobs"));
});

test("library: overlapping moments merged, used ones dropped", async () => {
  const { dedupeLibrary } = await import("../src/lib.js");
  const v = "https://www.twitch.tv/videos/1";
  const moments = [
    { id: "a", vod: v, start: 100, end: 140, score: 7 },
    { id: "b", vod: v + "?t=95s", start: 105, end: 150, score: 9 },   // same story, better -> kept
    { id: "c", vod: v, start: 300, end: 330, score: 8 },              // used in a long -> dropped
    { id: "d", vod: "https://www.twitch.tv/videos/2", start: 100, end: 140, score: 6 },
  ];
  const r = dedupeLibrary(moments, [{ vod: v, start: 295, end: 335 }]);
  assert.deepEqual(r.moments.map((m) => m.id), ["b", "d"]);
  assert.equal(r.dupes, 1);
  assert.equal(r.used, 1);
});
