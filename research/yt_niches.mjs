const KEY = process.env.YOUTUBE_API_KEY;
const niches = {
  "WTF / funny moments": "GTA 6 funny moments",
  "Streamer reactions": "GTA 6 streamer reaction",
  "Secrets & easter eggs": "GTA 6 secrets easter eggs",
  "Details & physics": "GTA 6 insane details",
  "GTA 6 vs GTA 5 / RDR2": "GTA 6 vs GTA 5 comparison",
  "Story / ending explained": "GTA 6 ending explained",
  "Lore & theories": "GTA 6 lore theory",
  "Map & hidden places": "GTA 6 map hidden locations",
  "Money / tips guide": "GTA 6 money guide tips",
  "NPC / AI behaviour": "GTA 6 NPC behavior",
  "Challenges / experiments": "GTA 6 challenge",
  "Rankings (cars, weapons)": "GTA 6 ranking every",
};
const since = new Date(Date.now() - 30 * 864e5).toISOString();
const out = [];
for (const [niche, q] of Object.entries(niches)) {
  const s = await (await fetch(`https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&maxResults=15&order=viewCount&relevanceLanguage=en&publishedAfter=${since}&videoDuration=medium&q=${encodeURIComponent(q)}&key=${KEY}`)).json();
  const s2 = await (await fetch(`https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&maxResults=10&order=viewCount&relevanceLanguage=en&publishedAfter=${since}&videoDuration=long&q=${encodeURIComponent(q)}&key=${KEY}`)).json();
  if (s.error) { console.log("ERR", s.error.message); break; }
  const ids = [...(s.items || []), ...(s2.items || [])].map((i) => i.id.videoId);
  const v = await (await fetch(`https://www.googleapis.com/youtube/v3/videos?part=statistics,contentDetails,snippet&id=${ids.join(",")}&key=${KEY}`)).json();
  const vids = (v.items || []).map((x) => {
    const m = String(x.contentDetails.duration || "").match(/P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/) || [];
    const sec = (+m[1] || 0) * 86400 + (+m[2] || 0) * 3600 + (+m[3] || 0) * 60 + (+m[4] || 0);
    return { id: x.id, title: x.snippet.title, ch: x.snippet.channelTitle, chId: x.snippet.channelId,
      views: +x.statistics.viewCount || 0, likes: +x.statistics.likeCount || 0, comments: +x.statistics.commentCount || 0,
      min: +(sec / 60).toFixed(1), days: +((Date.now() - Date.parse(x.snippet.publishedAt)) / 864e5).toFixed(1) };
  }).filter((x) => x.min >= 8).sort((a, b) => b.views - a.views);
  out.push({ niche, q, vids });
}
// channel sizes for outlier detection
const chIds = [...new Set(out.flatMap((n) => n.vids.slice(0, 6).map((v) => v.chId)))];
const subs = {};
for (let i = 0; i < chIds.length; i += 50) {
  const c = await (await fetch(`https://www.googleapis.com/youtube/v3/channels?part=statistics&id=${chIds.slice(i, i + 50).join(",")}&key=${KEY}`)).json();
  for (const x of c.items || []) subs[x.id] = +x.statistics.subscriberCount || 0;
}
for (const n of out) for (const v of n.vids) v.subs = subs[v.chId] ?? null;
const fs = await import("node:fs");
fs.writeFileSync("research/yt_niches.json", JSON.stringify(out, null, 1));
for (const n of out) {
  const top = n.vids.slice(0, 6);
  const sum = top.reduce((a, v) => a + v.views, 0);
  console.log(`\n=== ${n.niche} | top6 views ${Math.round(sum / 1e3)}k | median ${Math.round((top[Math.floor(top.length / 2)]?.views || 0) / 1e3)}k`);
  for (const v of top) console.log(`${String(Math.round(v.views / 1e3)).padStart(6)}k | ${String(v.min).padStart(5)}m | ${v.days}d | subs ${v.subs ? Math.round(v.subs / 1e3) + "k" : "?"} | ${v.ch} | ${v.title.slice(0, 70)} | ${v.id}`);
}
