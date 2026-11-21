// Pure helpers (no Cloudflare APIs) — shared by the bot and the panel, unit-tested with node --test.

export const ROLE_RU = { hook: "Хук", setup: "Завязка", punchline: "Панчлайн", reaction: "Реакция", payoff: "Развязка" };
export const CAM_RU = { push: "наезд", pan: "проезд", whip: "рывок", hold: "держим кадр", cut: "склейка" };
export const STATUS_RU = {
  queued: "⏳ в очереди", working: "⚙️ монтирую", ready: "🟢 готов", published: "✅ опубликован",
  rejected: "❌ отклонён", failed: "⚠️ ошибка", done: "✔️ готово",
};

export const DEFAULT_STYLE = {
  cam_height: 0.36, max_len: 58, min_len: 20, caption_words: 2, caption_size: 100,
  caption_color: "#FFFFFF", punch_color: "#FFE600", title_color: "#FFFFFF", title_accent: "#FFE600",
  title_seconds: 3.0, zoom_strength: 1.0, react_zoom: 1.18, shouts: true, whoosh: true, whoosh_volume: 0.35,
  watermark: true, lufs: -14,
};

export function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function fmtTime(sec) {
  sec = Math.max(0, Math.round(Number(sec) || 0));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

export function fixTitle(t) {
  t = String(t || "").replace(/#\w+/g, "").replace(/\s+/g, " ").trim().replace(/^["'«»]+|["'«»]+$/g, "");
  t = t.replace(/\bGTA\s*(VI|6)\b/gi, "GTA 6");
  if (!/gta 6/i.test(t)) t = t ? `GTA 6: ${t}` : "GTA 6 moment";
  else if (t.toLowerCase().indexOf("gta 6") > 35) {
    t = "GTA 6: " + t.replace(/gta 6/gi, "").replace(/\s+/g, " ").trim().replace(/^[:\-\s]+|[:\-\s]+$/g, "");
  }
  if (t.length > 60) {
    let cut = t.slice(0, 60);
    const sp = cut.lastIndexOf(" ");
    if (sp > 20) cut = cut.slice(0, sp);
    t = cut.replace(/[\s,:;\-–—]+$/, "");
  }
  return t;
}

export function extractUrl(text) {
  const m = String(text || "").match(/(https?:\/\/[^\s<>]+|(?:www\.)?(?:twitch\.tv|clips\.twitch\.tv|youtube\.com|youtu\.be)\/[^\s<>]+)/i);
  if (!m) return null;
  let u = m[1];
  if (!/^https?:\/\//i.test(u)) u = "https://" + u;
  return u;
}

export function parseCommand(text) {
  const m = String(text || "").trim().match(/^\/(\w+)(?:@\w+)?(?:\s+([\s\S]*))?$/);
  if (!m) return null;
  return { cmd: m[1].toLowerCase(), arg: (m[2] || "").trim() };
}

export function currentTitle(job, r) {
  if (job.title) return job.title;
  const titles = (r && r.titles) || [];
  return titles[job.chosen || 0] || titles[0] || "";
}

export function currentDescription(job, r) {
  return job.description || (r && r.description) || "";
}

export function planLines(plan) {
  return (plan || []).map((p) => {
    const shot = p.shot === "react" ? "крупно лицо" : (CAM_RU[p.camera] || p.camera);
    return `${fmtTime(p.at)} ${ROLE_RU[p.role] || p.role} — ${shot}${p.note ? " · " + p.note : ""}`;
  });
}

export function cardText(job, r) {
  r = r || {};
  const titles = r.titles || [];
  const head = [
    `🎬 <b>#${job.id}</b>`,
    r.streamer ? `twitch.tv/${esc(r.streamer)}` : null,
    r.score ? `⭐ ${r.score}/10` : null,
    r.duration ? fmtTime(r.duration) : null,
  ].filter(Boolean).join(" · ");
  const lines = [head];
  if (r.verdict) lines.push(`<i>${esc(r.verdict)}</i>`);
  lines.push("", "<b>Название</b> (нажми цифру):");
  titles.forEach((t, i) => {
    const on = !job.title && (job.chosen || 0) === i;
    lines.push(`${on ? "✅" : "▫️"} ${i + 1}. ${esc(t)}`);
  });
  if (job.title) lines.push(`✅ ✏️ ${esc(job.title)}`);
  const desc = currentDescription(job, r);
  lines.push("", "<b>Описание:</b>", `<blockquote expandable>${esc(desc.slice(0, 900))}</blockquote>`);
  const expl = [r.explanation || "", "", ...planLines(r.plan)].join("\n").trim();
  if (expl) lines.push("<b>🧠 Как я смонтировал:</b>", `<blockquote expandable>${esc(expl.slice(0, 1500))}</blockquote>`);
  const facts = [];
  if (r.viewed_pct) facts.push(`📈 досмотр ~${r.viewed_pct}%`);
  if (r.risk) facts.push(esc(r.risk));
  if (facts.length) lines.push(facts.join(" · "));
  const tech = [];
  if (r.lufs) tech.push(`${r.lufs} LUFS`);
  if (r.size_mb) tech.push(`${r.size_mb} МБ`);
  if (r.webcam) tech.push(r.webcam.present ? `вебка: ${esc(r.webcam.how || "найдена")}` : "без вебки");
  if (job.intro_file_id) tech.push("интро из Flow");
  if (r.ai_error) tech.push("⚠️ ИИ был недоступен");
  if (tech.length) lines.push(`<i>${tech.join(" · ")}</i>`);
  lines.push(`Статус: ${STATUS_RU[job.status] || job.status}`);
  let text = lines.join("\n");
  if (text.length > 4000) text = text.slice(0, 3990) + "…";
  return text;
}

export function cardKeyboard(job, r) {
  const id = job.id;
  const cb = (act, arg) => ({ callback_data: `a:${id}:${act}${arg !== undefined ? ":" + arg : ""}` });
  if (job.status === "rejected") return { inline_keyboard: [[{ text: "↩️ Вернуть", ...cb("un") }]] };
  if (job.status === "working" || job.status === "queued") return { inline_keyboard: [] };
  const n = Math.min(3, ((r && r.titles) || []).length);
  const titleRow = [];
  for (let i = 0; i < n; i++) titleRow.push({ text: `${!job.title && (job.chosen || 0) === i ? "✅ " : ""}${i + 1}`, ...cb("t", i) });
  const rows = [];
  if (titleRow.length) rows.push(titleRow);
  rows.push([{ text: "🔄 Новые названия", ...cb("nt") }, { text: "✏️ Своё название", ...cb("ct") }]);
  rows.push([{ text: "📝 Своё описание", ...cb("cd") }, { text: "✂️ Перемонтировать", ...cb("rm") }]);
  rows.push([{ text: job.flow_ok ? "🎨 Flow: промпт утверждён" : "🎨 Промпт для Google Flow", ...cb("fp") }]);
  rows.push([
    { text: job.status === "published" ? "✅ Опубликовано" : "🚀 Опубликовать", ...cb("pub") },
    { text: "❌ Отклонить", ...cb("rej") },
  ]);
  return { inline_keyboard: rows };
}

export function flowKeyboard(id) {
  return {
    inline_keyboard: [[
      { text: "✅ Утвердить", callback_data: `a:${id}:fa` },
      { text: "🔁 Другой", callback_data: `a:${id}:fo` },
      { text: "💬 Пожелание", callback_data: `a:${id}:fw` },
    ]],
  };
}

export function parseCallback(data) {
  const m = String(data || "").match(/^a:(\d+):(\w+)(?::(.*))?$/);
  return m ? { id: Number(m[1]), act: m[2], arg: m[3] } : null;
}

export function cleanStyle(input) {
  const out = {};
  for (const [k, d] of Object.entries(DEFAULT_STYLE)) {
    if (!(k in (input || {}))) continue;
    const v = input[k];
    if (typeof d === "boolean") out[k] = v === true || v === "true" || v === "on" || v === 1;
    else if (typeof d === "number") { const n = Number(v); if (Number.isFinite(n)) out[k] = n; }
    else if (/^#[0-9a-f]{6}$/i.test(String(v))) out[k] = String(v).toUpperCase();
  }
  return out;
}

export const HELP = [
  "👋 <b>Фабрика шортсов GTA 6</b>",
  "",
  "/clip ссылка — смонтировать момент (клип Twitch, VOD с таймкодом ?t=1h2m3s, YouTube)",
  "/vod ссылка — ИИ смотрит запись целиком и монтирует лучшие истории",
  "/status — что сейчас в работе",
  "/login — ссылка на веб-панель для ноутбука",
  "/cancel — отменить ожидание ввода",
  "",
  "Можно просто прислать ссылку — это то же, что /clip.",
].join("\n");
