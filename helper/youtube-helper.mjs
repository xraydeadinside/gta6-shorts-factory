// Laptop helper: downloads YouTube pieces from the owner's HOME internet (YouTube blocks GitHub servers),
// puts them into the private repo "gta6-materials" and tells the bot to edit them.
//
// Runs only when you start it (shortcut "Скачать с YouTube.cmd"), does its jobs and exits. Not a server.
// Needs: tools/bin/yt-dlp.exe, tools/bin/ffmpeg.exe, tools/gh.exe (logged in), .env with WORKER_URL + HOOK_SECRET.
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline/promises";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const BIN = join(ROOT, "tools", "bin");
const YTDLP = join(BIN, "yt-dlp.exe");
const GH = join(ROOT, "tools", "gh.exe");
const TMP = join(ROOT, "work", "helper");
const REPO = "xraydeadinside/gta6-materials";
const WINDOW_BEFORE = 40, WINDOW_AFTER = 25, MAX_FULL = 150;

const env = Object.fromEntries(readFileSync(join(ROOT, ".env"), "utf8").split(/\r?\n/)
  .filter((l) => /^[A-Z_]+=/.test(l)).map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1).trim()]));
const H = { "x-hook-secret": env.HOOK_SECRET, "content-type": "application/json" };

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { encoding: "utf8", maxBuffer: 64 << 20, ...opts,
    env: { ...process.env, GH_CONFIG_DIR: join(ROOT, "tools", "ghconf") } });
  if (r.status !== 0) throw new Error((r.stderr || r.stdout || "").trim().split("\n").slice(-3).join(" ").slice(0, 400));
  return r.stdout;
}

function timecode(url) {
  const m = String(url).match(/[?&#](?:t|start)=([0-9hms:]+)/i);
  if (!m) return null;
  const v = m[1];
  if (/^\d+$/.test(v)) return Number(v);
  if (v.includes(":")) return v.split(":").map(Number).reduce((a, b) => a * 60 + b, 0);
  const g = v.match(/(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?/);
  return g ? (Number(g[1] || 0) * 3600 + Number(g[2] || 0) * 60 + Number(g[3] || 0)) : null;
}

async function api(path, body) {
  let last;
  for (let i = 0; i < 4; i++) {   // home internet drops sometimes: retry
    try {
      const r = await fetch(env.WORKER_URL + path, body ? { method: "POST", headers: H, body: JSON.stringify(body) } : { headers: H });
      if (!r.ok) throw new Error(`бот ответил ${r.status}`);
      return await r.json();
    } catch (e) { last = e; await new Promise((s) => setTimeout(s, 3000 * (i + 1))); }
  }
  throw last;
}

async function one(job) {
  console.log(`\n▶ #${job.id}: ${job.url}`);
  const info = JSON.parse(run(YTDLP, ["-J", "--no-playlist", "--no-warnings", job.url]));
  let a = job.start, b = job.end;
  if (a == null || b == null) {
    const t = timecode(job.url);
    if (t != null) { a = Math.max(0, t - WINDOW_BEFORE); b = t + WINDOW_AFTER; }
    else if ((info.duration || 0) <= MAX_FULL) { a = 0; b = info.duration || MAX_FULL; }
    else throw new Error("Видео длинное, а таймкода нет. Добавь в ссылку ?t=1h2m3s (место момента).");
  }
  mkdirSync(TMP, { recursive: true });
  for (const f of readdirSync(TMP)) if (f.startsWith(`job${job.id}_`)) rmSync(join(TMP, f));
  const name = job.part != null ? `job${job.id}_p${job.part}_${Date.now()}` : `job${job.id}_${Date.now()}`;
  console.log(`  скачиваю кусок ${Math.round(a)}–${Math.round(b)} сек…`);
  // short shots for long videos: 720p is plenty and 2-3x less to download on a slow connection
  const h = job.part != null ? 720 : 1080;
  let file, lastErr;
  for (let attempt = 1; attempt <= 3 && !file; attempt++) {
    for (const f of readdirSync(TMP)) if (f.startsWith(name)) rmSync(join(TMP, f));
    try {
      run(YTDLP, ["--no-playlist", "--no-warnings", "--ffmpeg-location", BIN, "--retries", "10",
        "--fragment-retries", "10", "--socket-timeout", "30",
        "-f", `bv*[height<=${h}][vcodec^=avc1]+ba/b[height<=${h}][ext=mp4]/bv*[height<=${h}]+ba/b`,
        "--merge-output-format", "mp4", "--download-sections", `*${a}-${b}`, "--force-keyframes-at-cuts",
        "-o", join(TMP, `${name}.%(ext)s`), job.url], { stdio: ["ignore", "inherit", "pipe"] });
      file = readdirSync(TMP).find((f) => f.startsWith(name) && f.endsWith(".mp4"));
    } catch (e) {
      lastErr = e;
      console.log(`  попытка ${attempt} не удалась, пробую ещё…`);
      await new Promise((s) => setTimeout(s, 5000 * attempt));
    }
  }
  if (!file) throw lastErr || new Error("файл не скачался");
  const mb = (statSync(join(TMP, file)).size / 1048576).toFixed(1);
  console.log(`  загружаю в хранилище (${mb} МБ)…`);
  for (let attempt = 1; ; attempt++) {
    try { run(GH, ["release", "upload", "materials", join(TMP, file), "-R", REPO, "--clobber"]); break; } catch (e) {
      if (attempt >= 3) throw e;
      await new Promise((s) => setTimeout(s, 5000 * attempt));
    }
  }
  rmSync(join(TMP, file));
  const meta = { title: info.title, channel: info.channel, uploader_id: info.uploader_id, uploader: info.uploader,
    view_count: info.view_count, duration: info.duration, start: a, end: b };
  await api("/hook/material", { job_id: job.id, asset: file, meta, part: job.part });
  console.log(job.part != null ? `  ✅ кадр ${job.part + 1} для лонга #${job.id} готов` : `  ✅ готово — бот монтирует шортс #${job.id}`);
}

async function main() {
  for (const p of [YTDLP, join(BIN, "ffmpeg.exe"), GH]) if (!existsSync(p)) throw new Error(`нет файла ${p}`);
  let { jobs } = await api("/hook/materials");
  if (!jobs.length) {
    const rl = createInterface({ input: process.stdin, output: process.stdout });
    const url = (await rl.question("Заданий от бота нет. Вставь ссылку YouTube (с ?t= на нужный момент) или просто Enter для выхода:\n> ")).trim();
    rl.close();
    if (!url) return;
    await api("/hook/newclip", { url });
    ({ jobs } = await api("/hook/materials"));
  }
  console.log(`Заданий: ${jobs.length}`);
  for (const job of jobs) {
    try { await one(job); } catch (e) {
      console.log(`  ⚠️ не получилось: ${e.message}`);
      await api("/hook/material", { job_id: job.id, part: job.part, error: e.message }).catch(() => {});
    }
  }
}

main().catch((e) => { console.error("Ошибка:", e.message); process.exitCode = 1; });
