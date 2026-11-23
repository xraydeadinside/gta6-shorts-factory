import fs from "node:fs";
const KEY = process.env.GEMINI_API_KEY;
const vids = [
  ["2J37vvHIFcU", "GTAbites — GTA 6 Can Do Things We Never Imagined in GTA 5 (6K subs, 7.1M views)"],
  ["dUYIMa2ifWM", "Zynxize — GTA 6 WTF and Funny Moments #2 (412K subs, 4.5M views)"],
  ["o4nNjj44d4M", "BreeZe GTA — 15 CRAZY Features In GTA 6 That Were NOT In GTA 5 (25K subs, 4.6M views)"],
  ["AIvSTjArIZo", "Sonny Evans — Do GTA 6 NPCs Actually Have Lives? (551K subs, 4.2M views)"],
];
const prompt = `You are a senior YouTube editor and retention analyst. You see the FIRST 8 MINUTES of a long-form GTA 6 video (the full length is given in the name). Watch AND listen.
Be concrete and measurable. Answer in English, compact, with these sections:
1. STRUCTURE: timeline of segments with timestamps (mm:ss) — hook, intro, each segment, transitions, CTA, ending. How long each segment is on average.
2. HOOK 0:00-0:30: exactly what is shown, said and written, second by second.
3. NARRATION: is there a voice-over? human or AI? tone, speed (words/min estimate), how it addresses the viewer, recurring phrases, humour.
4. FOOTAGE: whose footage (own gameplay, streamers' webcam clips, trailers), how clips are framed (webcam position/size), clip lengths.
5. PACING: average seconds per shot, zooms, jump cuts, speed ramps, freeze frames, replays.
6. TEXT & GRAPHICS: titles/numbers/counters, captions style, arrows/circles, emojis, lower thirds, progress through a list.
7. SOUND: music (genre, volume under voice, changes), sound effects (which, where), how game audio and voice are mixed.
8. RETENTION DEVICES: open loops, teasers ("but #1 is insane"), countdown, re-hooks, where they put the best item, CTA placement and wording.
9. ENDING: last 60 s.
10. WHAT WE SHOULD COPY: 10 concrete rules for an automated channel that makes 9-15 min GTA 6 videos from Twitch streamers' moments + gameplay clips with an AI voice-over.
11. WEAKNESSES: 3 things that are weak and how to beat them.`;
const out = fs.existsSync("research/longs_analysis.json") ? JSON.parse(fs.readFileSync("research/longs_analysis.json","utf8")) : {};
for (const [id, name] of vids) {
  if (out[id]) continue;
  const body = { contents: [{ role: "user", parts: [{ file_data: { file_uri: `https://www.youtube.com/watch?v=${id}` }, video_metadata: { start_offset: "0s", end_offset: "480s", fps: 0.5 } }, { text: prompt }] }],
    generationConfig: { temperature: 0.3, mediaResolution: "MEDIA_RESOLUTION_LOW" } };
  let text = null;
  for (const model of ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite"]) {
    for (let a = 0; a < 3 && !text; a++) {
      let r; try { r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
        method: "POST", headers: { "content-type": "application/json", "x-goog-api-key": KEY }, body: JSON.stringify(body), signal: AbortSignal.timeout(280000) }); } catch (e) { console.log(id, model, "timeout"); continue; }
      const j = await r.json();
      if (r.ok) { text = j.candidates?.[0]?.content?.parts?.map((p) => p.text || "").join("") || ""; out[id] = { name, model, text }; break; }
      console.log(id, model, r.status, (j.error?.message || "").slice(0, 160));
      await new Promise((s) => setTimeout(s, 20000 * (a + 1)));
    }
    if (text) break;
  }
  console.log(`\n##### ${name}\n${(text || "FAILED").slice(0, 400)}...`);
  fs.writeFileSync("research/longs_analysis.json", JSON.stringify(out, null, 1));
}
