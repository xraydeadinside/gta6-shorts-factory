// Cloudflare Worker: Telegram bot webhook (instant answers) + web panel + state in D1.
// Heavy video work is sent to GitHub Actions (repository_dispatch); results come back to /hook/*.
import {
  HELP, DEFAULT_STYLE, STATUS_RU, cardKeyboard, cardText, cleanStyle, currentDescription, currentTitle,
  esc, extractUrl, fixTitle, flowKeyboard, parseCallback, parseCommand,
} from "./lib.js";
import { LOGIN_HTML, PANEL_HTML } from "./panel.js";

const json = (o, status = 200, headers = {}) =>
  new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json; charset=utf-8", ...headers } });
const now = () => Date.now();

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const p = url.pathname;
    try {
      if (p === "/tg" && req.method === "POST") return await tgWebhook(req, env, ctx, url.origin);
      if (p.startsWith("/hook/")) return await hook(req, env, ctx, p, url.origin);
      if (p === "/login") return await login(env, url);
      if (p === "/logout") return new Response("", { status: 302, headers: { location: "/", "set-cookie": "sid=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax" } });
      if (p.startsWith("/api/")) return await api(req, env, ctx, p, url);
      if (p === "/" || p === "/index.html") {
        const ok = await session(req, env);
        return new Response(ok ? PANEL_HTML : LOGIN_HTML, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
      }
      if (p === "/health") return json({ ok: true });
      return new Response("not found", { status: 404 });
    } catch (e) {
      console.error(e && e.stack || e);
      return json({ error: String(e && e.message || e) }, 500);
    }
  },
};

// ------------------------------------------------------------------ D1 helpers
async function kvGet(env, k) {
  const r = await env.DB.prepare("SELECT v, exp FROM kv WHERE k = ?").bind(k).first();
  if (!r) return null;
  if (r.exp && r.exp < now()) { await env.DB.prepare("DELETE FROM kv WHERE k = ?").bind(k).run(); return null; }
  try { return JSON.parse(r.v); } catch { return r.v; }
}
async function kvSet(env, k, v, ttlMs = 0) {
  await env.DB.prepare("INSERT INTO kv (k, v, exp) VALUES (?, ?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v, exp = excluded.exp")
    .bind(k, JSON.stringify(v), ttlMs ? now() + ttlMs : null).run();
}
async function kvDel(env, k) { await env.DB.prepare("DELETE FROM kv WHERE k = ?").bind(k).run(); }

async function getJob(env, id) {
  const j = await env.DB.prepare("SELECT * FROM jobs WHERE id = ?").bind(id).first();
  if (!j) return null;
  j.result = j.result ? JSON.parse(j.result) : null;
  return j;
}
const JOB_COLS = ["status", "stage", "wish", "intro_file_id", "reuse_plan", "title", "description", "chosen", "flow_prompt",
  "flow_ok", "card_msg_id", "ack_msg_id", "result", "error", "run_url", "version", "video_msg_id", "material", "material_meta", "brief"];
async function updateJob(env, id, fields) {
  const keys = Object.keys(fields).filter((k) => JOB_COLS.includes(k));
  if (!keys.length) return;
  const vals = keys.map((k) => ((k === "result" || k === "material_meta") && fields[k] && typeof fields[k] === "object" ? JSON.stringify(fields[k]) : fields[k]));
  await env.DB.prepare(`UPDATE jobs SET ${keys.map((k) => `${k} = ?`).join(", ")}, updated_at = ? WHERE id = ?`)
    .bind(...vals, now(), id).run();
}
async function getStyle(env) { return { ...DEFAULT_STYLE, ...((await kvGet(env, "style")) || {}) }; }

// ------------------------------------------------------------------ Telegram
async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!data.ok && !/message is not modified/.test(data.description || "")) console.log("tg", method, data.description);
  return data;
}
const say = (env, text, extra = {}) =>
  tg(env, "sendMessage", { chat_id: env.TELEGRAM_CHAT_ID, text, parse_mode: "HTML", link_preview_options: { is_disabled: true }, ...extra });

async function remember(env, msgId, jobId) { if (msgId) await kvSet(env, `m:${msgId}`, jobId, 1000 * 3600 * 24 * 60); }

async function refreshCard(env, job) {
  if (!job.card_msg_id || !job.result) return;
  await tg(env, "editMessageText", {
    chat_id: env.TELEGRAM_CHAT_ID, message_id: job.card_msg_id, text: cardText(job, job.result), parse_mode: "HTML",
    reply_markup: cardKeyboard(job, job.result), link_preview_options: { is_disabled: true },
  });
}

async function sendCard(env, job) {
  const r = job.result || {};
  const m = await say(env, cardText(job, r), {
    reply_markup: cardKeyboard(job, r),
    ...(r.video_msg_id ? { reply_parameters: { message_id: r.video_msg_id, allow_sending_without_reply: true } } : {}),
  });
  const id = m.result && m.result.message_id;
  if (id) { await updateJob(env, job.id, { card_msg_id: id }); await remember(env, id, job.id); }
  await remember(env, r.video_msg_id, job.id);
}

async function tgWebhook(req, env, ctx, origin) {
  if (req.headers.get("x-telegram-bot-api-secret-token") !== env.TG_SECRET) return new Response("forbidden", { status: 403 });
  const upd = await req.json();
  ctx.waitUntil(handleUpdate(env, upd, origin).catch((e) => console.error("update failed", e && e.stack || e)));
  return new Response("ok");
}

async function handleUpdate(env, upd, origin) {
  const owner = String(env.TELEGRAM_CHAT_ID);
  if (upd.callback_query) {
    const q = upd.callback_query;
    if (String(q.from.id) !== owner) return tg(env, "answerCallbackQuery", { callback_query_id: q.id, text: "Бот приватный" });
    const c = parseCallback(q.data);
    if (!c) return tg(env, "answerCallbackQuery", { callback_query_id: q.id });
    const job = await getJob(env, c.id);
    if (!job) return tg(env, "answerCallbackQuery", { callback_query_id: q.id, text: "Клип не найден" });
    const toast = quickToast(c.act);
    await tg(env, "answerCallbackQuery", { callback_query_id: q.id, text: toast });   // instant feedback
    return doAction(env, job, c.act, c.arg, null, "tg");
  }
  const msg = upd.message || upd.edited_message;
  if (!msg) return;
  if (String(msg.chat.id) !== owner || String((msg.from || {}).id) !== owner) {
    if (msg.chat.type === "private") await tg(env, "sendMessage", { chat_id: msg.chat.id, text: "Это приватный бот." });
    return;
  }
  const video = msg.video || msg.animation || (msg.document && /^video\//.test(msg.document.mime_type || "") ? msg.document : null);
  if (video) return onIntroVideo(env, msg, video);
  const text = msg.text || msg.caption || "";
  const cmd = parseCommand(text);
  if (cmd) return onCommand(env, cmd, origin);
  const pending = await kvGet(env, "await");
  if (pending && text && pending.type !== "intro") { await kvDel(env, "await"); const job = await getJob(env, pending.job); if (job) return doAction(env, job, pending.type, null, text, "tg"); }
  const u = extractUrl(text);
  if (u) return newJob(env, "render", u, {});
  return say(env, HELP);
}

function quickToast(act) {
  return { t: "Название выбрано", nt: "Придумываю новые…", ct: "Напиши название", cd: "Напиши описание", rm: "Напиши пожелание",
    fp: "Промпт для Flow", fa: "Утверждено", fo: "Придумываю другой…", fw: "Напиши пожелание", pub: "Готовлю публикацию",
    rej: "Отклонено", un: "Вернул", la: "Запускаю монтаж", ll: "Меняю длину…", lr: "Напиши пожелание" }[act] || "";
}

async function onCommand(env, { cmd, arg }, origin) {
  if (cmd === "start" || cmd === "help") return say(env, HELP);
  if (cmd === "clip" || cmd === "vod") {
    const u = extractUrl(arg);
    if (!u) return say(env, `Пришли так: <code>/${cmd} https://…</code>`);
    return newJob(env, cmd === "vod" ? "vod" : "render", u, { note: arg.replace(u, "").trim() || null });
  }
  if (cmd === "ref" || cmd === "example") {
    const u = extractUrl(arg);
    if (!u) return say(env, "Пришли так: <code>/ref https://youtube.com/…</code> и можно коротко, что нравится");
    return newJob(env, "ref", u, { note: arg.replace(u, "").trim() || null });
  }
  if (cmd === "note") {
    if (!arg) return say(env, "Пришли так: <code>/note субтитры слишком большие</code>");
    const notes = (await kvGet(env, "notes")) || [];
    notes.push({ t: now(), text: arg.slice(0, 2000) });
    await kvSet(env, "notes", notes.slice(-200));
    return say(env, `📝 Записал замечание (${notes.length}). Claude прочитает его, когда вернёшься к компьютеру.`);
  }
  if (cmd === "refs") {
    const rows = await env.DB.prepare("SELECT id, url, status, result, note FROM jobs WHERE kind = 'ref' ORDER BY id DESC LIMIT 15").all();
    const notes = (await kvGet(env, "notes")) || [];
    const lines = ["📚 <b>Примеры</b>"];
    for (const j of rows.results || []) {
      const r = j.result ? JSON.parse(j.result) : {};
      lines.push(`#${j.id} ${STATUS_RU[j.status] || j.status} · ${esc(r.format || "")} · ${esc((r.title || j.url).slice(0, 60))}`);
    }
    if (!(rows.results || []).length) lines.push("Пока нет. Пришли /ref ссылка");
    lines.push("", `📝 Замечаний: ${notes.length}`);
    return say(env, lines.join("\n"));
  }
  if (cmd === "collect") {
    const u = extractUrl(arg);
    if (!u) return say(env, "Пришли так: <code>/collect https://twitch.tv/videos/… 3</code> (3 = сколько первых часов смотреть)");
    const rest = arg.replace(u, "").trim().split(/s+/).map(Number).filter((n) => Number.isFinite(n) && n > 0);
    const brief = JSON.stringify({ mode: "collect", hours: rest[0] || 3, from_hours: rest[1] || 0 });
    return newJob(env, "vod", u, { brief, note: "сбор моментов для лонга" });
  }
  if (cmd === "long") {
    const num = (arg.match(/\b(\d{1,2})\b/) || [])[1];
    const minutes = Math.max(6, Math.min(20, Number(num) || 10));
    const wish = arg.replace(/\b\d{1,2}\b/, "").trim() || null;
    return newJob(env, "long", "long:streamers-react", { brief: JSON.stringify({ minutes, wish }), note: wish });
  }
  if (cmd === "status") return say(env, await statusText(env));
  if (cmd === "cancel") { await kvDel(env, "await"); return say(env, "Ок, отменил."); }
  if (cmd === "login" || cmd === "panel") {
    const t = randomToken();
    await kvSet(env, `login:${t}`, 1, 10 * 60 * 1000);
    return say(env, `🔐 Вход в панель (ссылка одноразовая, 10 минут):\n${origin}/login?t=${t}`);
  }
  return say(env, HELP);
}

async function statusText(env) {
  const counts = await env.DB.prepare("SELECT status, COUNT(*) n FROM jobs GROUP BY status").all();
  const last = await env.DB.prepare("SELECT * FROM jobs ORDER BY id DESC LIMIT 8").all();
  const c = Object.fromEntries((counts.results || []).map((r) => [r.status, r.n]));
  const lines = ["📊 <b>Статус</b>",
    `в работе: ${(c.working || 0) + (c.queued || 0)} · готовы: ${c.ready || 0} · опубликовано: ${c.published || 0} · ошибок: ${c.failed || 0}`, ""];
  for (const j of last.results || []) {
    const r = j.result ? JSON.parse(j.result) : null;
    const t = currentTitle(j, r) || j.url;
    lines.push(`#${j.id} ${STATUS_RU[j.status] || j.status}${j.status === "working" && j.stage ? " — " + esc(j.stage) : ""}\n   ${esc(t.slice(0, 70))}`);
  }
  if (!(last.results || []).length) lines.push("Пока пусто. Пришли /clip ссылка");
  return lines.join("\n");
}

// ------------------------------------------------------------------ jobs
async function newJob(env, kind, url, { start = null, end = null, note = null, brief = null, parent = null, silent = false } = {}) {
  const ins = await env.DB.prepare(
    "INSERT INTO jobs (kind, url, status, stage, created_at, updated_at, parent_id, start, end_t, note, brief) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)")
    .bind(["vod", "ref", "long"].includes(kind) ? kind : "render", url, "в очереди", now(), now(), parent, start, end, note, brief).run();
  const id = ins.meta.last_row_id;
  if (kind === "render" && isYouTube(url)) {
    await updateJob(env, id, { status: "laptop", stage: "ждёт помощника на ноутбуке" });
    if (!silent) {
      const m = await say(env, `📥 Принял #${id} (YouTube). YouTube не отдаёт видео серверам GitHub, поэтому его скачивает ноутбук: запусти ярлык «Скачать с YouTube» — он скачает кусок и я смонтирую шортс.`);
      if (m.result) { await updateJob(env, id, { ack_msg_id: m.result.message_id }); await remember(env, m.result.message_id, id); }
    }
    return id;
  }
  if (!silent) {
    const m = await say(env, kind === "long"
      ? `📝 Лонг #${id}: ИИ выбирает лучшие моменты из библиотеки и пишет сценарий. Пришлю его на утверждение.`
      : kind === "ref"
      ? `📚 Сохранил пример #${id}. ИИ разбирает его стиль (хук, темп, субтитры, вебка, звук) — пришлю разбор через пару минут.`
      : kind === "vod"
      ? `✅ Принял запись #${id}. ИИ посмотрит стрим целиком и выберет законченные истории — это 15–40 минут, пришлю шортсы.`
      : `✅ Принял #${id}. Монтирую — пришлю готовый шортс через ~4–7 минут.`);
    if (m.result) { await updateJob(env, id, { ack_msg_id: m.result.message_id }); await remember(env, m.result.message_id, id); }
  }
  await dispatchOrFail(env, kind === "long" ? "longscript" : ["vod", "ref"].includes(kind) ? kind : "render", id);
  return id;
}

async function sendScript(env, id, res) {
  const sc = res.script || {};
  const head = [`📝 <b>Сценарий лонга #${id}</b> (~${sc.minutes} мин, ${(sc.items || []).length} моментов из ${res.library_size || "?"})`,
    `<b>${esc(sc.title || "")}</b>`, `Обложка: «${esc(sc.thumbnail_text || "")}» — ${esc(sc.thumbnail_idea || "")}`, "",
    `🎣 <b>Хук:</b> ${esc(sc.hook_vo || "")} ${esc(sc.promise_vo || "")} ${esc(sc.stakes_vo || "")}`, ""];
  const lib = Object.fromEntries((res.library || []).map((m) => [m.id, m]));
  const n = (sc.items || []).length;
  const items = (sc.items || []).map((it, k) => {
    const m = lib[it.cand] || {};
    return `<b>#${n - k} ${esc(it.label_en || "")}</b> · ${esc(m.streamer || "")}\n${esc(it.what_ru || m.what_ru || "")}\n<i>${esc(it.vo_before || "")}</i>`;
  });
  let text = head.join("\n") + "\n" + items.join("\n\n") + "\n\n🧠 " + esc(sc.explanation_ru || "");
  if (text.length > 4000) text = text.slice(0, 3990) + "…";
  const kb = { inline_keyboard: [
    [{ text: "✅ Утвердить и монтировать", callback_data: `a:${id}:la` }],
    [{ text: "⏱ 9 мин", callback_data: `a:${id}:ll:9` }, { text: "⏱ 12 мин", callback_data: `a:${id}:ll:12` },
      { text: "⏱ 15 мин", callback_data: `a:${id}:ll:15` }],
    [{ text: "🔁 Переписать (с пожеланием)", callback_data: `a:${id}:lr` }],
  ] };
  return tg(env, "sendMessage", { chat_id: env.TELEGRAM_CHAT_ID, text, parse_mode: "HTML", reply_markup: kb,
    link_preview_options: { is_disabled: true } });
}

function isYouTube(u) {
  return /^(https?:\/\/)?((www|m|music)\.)?(youtube\.com|youtu\.be)\//i.test(String(u || "").trim());
}

async function dispatchOrFail(env, kind, id) {
  try {
    await dispatch(env, kind, id);
  } catch (e) {
    await updateJob(env, id, { status: "failed", error: String(e.message || e) });
    await say(env, `⚠️ #${id}: не смог запустить монтаж на GitHub: ${esc(String(e.message || e).slice(0, 300))}`);
  }
}

async function dispatch(env, kind, id) {
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
    method: "POST",
    headers: { authorization: `Bearer ${env.GH_TOKEN}`, accept: "application/vnd.github+json", "user-agent": "gta6-shorts-worker",
      "x-github-api-version": "2022-11-28", "content-type": "application/json" },
    body: JSON.stringify({ event_type: kind, client_payload: { job_id: id } }),
  });
  if (!r.ok) throw new Error(`GitHub ${r.status}: ${(await r.text()).slice(0, 200)}`);
}

async function rerender(env, job, fields, text) {
  await updateJob(env, job.id, { status: "queued", stage: "в очереди", error: null, ...fields });
  const m = await say(env, text);
  if (m.result) { await updateJob(env, job.id, { ack_msg_id: m.result.message_id }); await remember(env, m.result.message_id, job.id); }
  const j = await getJob(env, job.id);
  if (j.card_msg_id) await refreshCard(env, j);
  await dispatchOrFail(env, "render", job.id);
}

// ------------------------------------------------------------------ actions (same for bot buttons and panel)
async function doAction(env, job, act, arg, text, source) {
  const r = job.result || {};
  const reply = (t) => (source === "tg" ? say(env, t) : null);
  const ask = async (type, prompt) => {
    if (source !== "tg") return { error: "нужен текст" };
    await kvSet(env, "await", { type, job: job.id }, 15 * 60 * 1000);
    return say(env, prompt, { reply_markup: { force_reply: true, input_field_placeholder: "Напиши здесь…" } });
  };
  const done = async (fields, msg) => {
    if (fields) await updateJob(env, job.id, fields);
    const j = await getJob(env, job.id);
    await refreshCard(env, j);
    if (msg) await reply(msg);
    return { ok: true, message: msg || "Готово" };
  };

  if (job.kind === "long") {
    let b = {};
    try { b = JSON.parse(job.brief || "{}"); } catch (e) { b = {}; }
    if (act === "la") {
      if (job.status !== "script") return reply("Этот сценарий уже в работе или готов.");
      await updateJob(env, job.id, { status: "queued", stage: "в очереди на монтаж" });
      await dispatchOrFail(env, "long", job.id);
      return reply(`🎬 Лонг #${job.id}: сценарий утверждён, монтирую. Это займёт 20–40 минут — пришлю превью, обложку и описание.`);
    }
    if (act === "ll") {
      b.minutes = Math.max(6, Math.min(20, Number(arg) || 10));
      await updateJob(env, job.id, { brief: JSON.stringify(b), status: "queued", stage: "переписываю сценарий" });
      await dispatchOrFail(env, "longscript", job.id);
      return reply(`⏱ #${job.id}: переписываю сценарий на ${b.minutes} мин.`);
    }
    if (act === "lr") {
      if (!text) return ask("lr", `🔁 #${job.id}: что поменять в сценарии? (например: «больше Kai Cenat, #1 — кран, меньше шуток»)`);
      b.wish = text.slice(0, 1000);
      await updateJob(env, job.id, { brief: JSON.stringify(b), status: "queued", stage: "переписываю сценарий" });
      await dispatchOrFail(env, "longscript", job.id);
      return reply(`🔁 #${job.id}: переписываю сценарий с учётом пожелания.`);
    }
    return reply("Для лонга доступны кнопки под сценарием.");
  }
  switch (act) {
    case "t": {
      const n = Math.max(0, Math.min(2, Number(arg) || 0));
      return done({ chosen: n, title: null });
    }
    case "nt": {
      const titles = await geminiTitles(env, r, text);
      if (!titles.length) return done(null, "😕 ИИ сейчас перегружен, попробуй ещё раз через минуту.");
      r.titles = titles;
      return done({ result: r, chosen: 0, title: null }, source === "tg" ? null : "Новые названия готовы");
    }
    case "ct":
      if (text) return done({ title: fixTitle(text).slice(0, 100) }, "✅ Название сохранено");
      return ask("ct", `✏️ #${job.id}: напиши своё название (по-английски, коротко; «GTA 6» я добавлю сам, если нет).`);
    case "cd":
      if (text) return done({ description: text.slice(0, 4900) }, "✅ Описание сохранено");
      return ask("cd", `📝 #${job.id}: пришли своё описание целиком.`);
    case "rm":
      if (text) {
        await rerender(env, job, { wish: text.slice(0, 1000), reuse_plan: 0 }, `✂️ #${job.id}: перемонтирую с учётом пожелания. Пришлю новую версию.`);
        return { ok: true, message: "Перемонтирую" };
      }
      return ask("rm", `✂️ #${job.id}: что поменять? Пиши своими словами (например: «короче, начни с аварии, больше лица»).`);
    case "fp": {
      const prompt = job.flow_prompt || r.flow_prompt || "";
      if (!prompt) return reply("У этого клипа нет промпта для Flow.");
      if (source !== "tg") return { ok: true, prompt };
      const m = await say(env, flowText(job, prompt), { reply_markup: flowKeyboard(job.id) });
      if (m.result) await remember(env, m.result.message_id, job.id);
      return;
    }
    case "fa": {
      const prompt = job.flow_prompt || r.flow_prompt || "";
      await updateJob(env, job.id, { flow_ok: 1, flow_prompt: prompt });
      await kvSet(env, "await", { type: "intro", job: job.id }, 24 * 3600 * 1000);
      const j = await getJob(env, job.id);
      await refreshCard(env, j);
      if (source === "tg") {
        const m = await say(env, `✅ Промпт утверждён. Сгенерируй видео в Google Flow (labs.google/flow) и пришли его <b>ответом на это сообщение</b> — оно встанет интро в #${job.id}.\n\n<code>${esc(prompt)}</code>`);
        if (m.result) await remember(env, m.result.message_id, job.id);
      }
      return { ok: true, message: "Промпт утверждён. Пришли видео из Flow боту ответом на сообщение с промптом." };
    }
    case "fo":
    case "fw": {
      if (act === "fw" && !text) return ask("fw", `💬 #${job.id}: что изменить в промпте для Flow?`);
      const np = await geminiFlow(env, r, job.flow_prompt || r.flow_prompt, text);
      if (!np) return done(null, "😕 ИИ сейчас перегружен, попробуй ещё раз.");
      await updateJob(env, job.id, { flow_prompt: np, flow_ok: 0 });
      if (source === "tg") {
        const m = await say(env, flowText({ ...job, flow_prompt: np }, np), { reply_markup: flowKeyboard(job.id) });
        if (m.result) await remember(env, m.result.message_id, job.id);
      }
      return { ok: true, prompt: np };
    }
    case "intro":
      await rerender(env, job, { intro_file_id: arg, reuse_plan: 1 }, `🎞 #${job.id}: интро получил, вставляю в начало. Пришлю новую версию.`);
      return { ok: true };
    case "pub": {
      const yt = await kvGet(env, "yt_auto");
      if (yt) {
        await updateJob(env, job.id, { stage: "загружаю на YouTube" });
        await dispatchOrFail(env, "upload", job.id);
        return done(null, `🚀 #${job.id}: загружаю на YouTube…`);
      }
      const title = currentTitle(job, r);
      const desc = currentDescription(job, r);
      await updateJob(env, job.id, { status: "published" });
      if (source === "tg") {
        await say(env, `🚀 <b>#${job.id} готов к публикации</b>\nСохрани видео выше (⋯ → Сохранить) и выложи в YouTube Shorts.\n\n<b>Название</b> (нажми, чтобы скопировать):\n<code>${esc(title)}</code>\n\n<b>Описание:</b>\n<code>${esc(desc)}</code>\n\n<b>Теги:</b>\n<code>${esc((r.tags || []).join(", "))}</code>`,
          r.video_msg_id ? { reply_parameters: { message_id: r.video_msg_id, allow_sending_without_reply: true } } : {});
      }
      return done(null, null);
    }
    case "rej":
      return done({ status: "rejected" }, null);
    case "un":
      return done({ status: job.result ? "ready" : "failed" }, null);
    default:
      return { error: "unknown action" };
  }
}

function flowText(job, prompt) {
  return `🎨 <b>Промпт для Google Flow</b> (#${job.id})\nИнтро 6–8 сек, вертикальное 9:16. Нажми на текст, чтобы скопировать:\n\n<code>${esc(prompt)}</code>`;
}

async function onIntroVideo(env, msg, video) {
  let jobId = null;
  if (msg.reply_to_message) jobId = await kvGet(env, `m:${msg.reply_to_message.message_id}`);
  if (!jobId) { const a = await kvGet(env, "await"); if (a && a.type === "intro") jobId = a.job; }
  if (!jobId) return say(env, "Не понял, к какому клипу это видео. Пришли его <b>ответом</b> на сообщение с промптом Flow.");
  if ((video.file_size || 0) > 20 * 1024 * 1024) return say(env, "Видео больше 20 МБ — Telegram не даст мне его скачать. Сожми или обрежь до 8 сек.");
  const job = await getJob(env, jobId);
  if (!job) return;
  await kvDel(env, "await");
  return doAction(env, job, "intro", video.file_id, null, "tg");
}

// ------------------------------------------------------------------ Gemini (titles / Flow prompt — fast, from the Worker)
const RULES = "YouTube Shorts title rules: 4-6 words, 20-40 characters, starts with 'GTA 6', curiosity or emotion, no hashtags, no lies. Give 3 different: [0] search-optimised, [1] curiosity/browse, [2] hybrid.";
async function gemini(env, prompt, temperature = 0.9) {
  const models = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"];
  for (const m of models) {
    try {
      const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${m}:generateContent`, {
        method: "POST", headers: { "content-type": "application/json", "x-goog-api-key": env.GEMINI_API_KEY },
        body: JSON.stringify({ contents: [{ role: "user", parts: [{ text: prompt }] }], generationConfig: { temperature, responseMimeType: "application/json" } }),
      });
      if (!r.ok) continue;
      const d = await r.json();
      const t = d.candidates[0].content.parts.map((p) => p.text || "").join("");
      return JSON.parse(t);
    } catch (e) { /* next model */ }
  }
  return null;
}
async function geminiTitles(env, r, wish) {
  const out = await gemini(env, `GTA 6 Shorts clip from a Twitch stream. What happens: ${r.summary || r.source_title || ""}\nStreamer: ${r.streamer || "unknown"}\nOld titles (do not repeat): ${JSON.stringify(r.titles || [])}\n${RULES}${wish ? "\nOwner wish: " + wish : ""}\nReturn JSON {"titles": ["...","...","..."]}`);
  return ((out && out.titles) || []).filter(Boolean).slice(0, 3).map(fixTitle);
}
async function geminiFlow(env, r, old, wish) {
  const out = await gemini(env, `Write a prompt for Google Flow (Veo) to generate a 6-8 second vertical 9:16 cinematic INTRO for a GTA 6 Shorts clip. Clip: ${r.summary || ""}\nStyle: Vice City neon, dramatic, fast camera, no real people's likeness, no logos, no on-screen text. ${old ? "Previous prompt: " + old : ""}${wish ? "\nOwner wish (follow it): " + wish : "\nMake it clearly different from the previous one."}\nReturn JSON {"prompt": "..."}`);
  return out && out.prompt ? String(out.prompt).slice(0, 1500) : null;
}

// ------------------------------------------------------------------ hooks from GitHub Actions
async function hook(req, env, ctx, p, origin) {
  if (req.headers.get("x-hook-secret") !== env.HOOK_SECRET) return json({ error: "forbidden" }, 403);
  let m = p.match(/^\/hook\/job\/(\d+)$/);
  if (m && req.method === "GET") {
    const job = await getJob(env, Number(m[1]));
    if (!job) return json({ error: "no job" }, 404);
    return json({ ...job, end: job.end_t, style: await getStyle(env) });
  }
  if (p === "/hook/library" && req.method === "GET") {
    const rows = await env.DB.prepare("SELECT id, kind, url, start, end_t, brief, result FROM jobs WHERE (kind = 'vod' AND status = 'done') OR (kind = 'render' AND brief IS NOT NULL AND status IN ('ready','published'))").all();
    const moments = [];
    for (const j of rows.results || []) {
      const r = j.result ? JSON.parse(j.result) : {};
      if (j.kind === "vod") {
        (r.library || []).forEach((m, i) => moments.push({ ...m, id: `v${j.id}_${i}` }));
      } else {
        let b = {};
        try { b = JSON.parse(j.brief); } catch (e) { b = {}; }
        const vod = String(j.url).split("?")[0];
        if (!/twitch\.tv\/videos\//.test(vod)) continue;
        moments.push({ id: `r${j.id}`, vod, start: (j.start || 0) + 3, end: (j.end_t || 0) - 3, streamer: r.streamer || null,
          score: b.score, kind: b.kind, context_en: b.context_en, what_ru: b.what_ru, why_ru: b.why_ru, title_en: b.title_en });
      }
    }
    return json({ moments });
  }
  // --- laptop helper (YouTube is downloaded from the owner's home internet, not from GitHub servers)
  if (p === "/hook/materials" && req.method === "GET") {
    const rows = await env.DB.prepare("SELECT id, url, start, end_t FROM jobs WHERE status = 'laptop' ORDER BY id").all();
    return json({ jobs: (rows.results || []).map((r) => ({ id: r.id, url: r.url, start: r.start, end: r.end_t })) });
  }
  const body = await req.json();
  if (p === "/hook/newclip") {
    const u = extractUrl(String(body.url || ""));
    if (!u) return json({ error: "no url" }, 400);
    const id = await newJob(env, "render", u, {});
    return json({ ok: true, id });
  }
  const job = await getJob(env, Number(body.job_id));
  if (p === "/hook/material") {
    if (!job) return json({ error: "no job" }, 404);
    if (body.error) {
      await updateJob(env, job.id, { status: "failed", stage: "ошибка", error: String(body.error).slice(0, 500) });
      await say(env, `⚠️ #${job.id}: помощник не смог скачать: ${esc(String(body.error).slice(0, 300))}`);
      return json({ ok: true });
    }
    await updateJob(env, job.id, { material: String(body.asset), material_meta: body.meta || {}, status: "queued",
      stage: "скачано помощником" });
    if (job.ack_msg_id) {
      ctx.waitUntil(tg(env, "editMessageText", { chat_id: env.TELEGRAM_CHAT_ID, message_id: job.ack_msg_id,
        text: `⏳ #${job.id}: скачал с YouTube через ноутбук, монтирую…`, link_preview_options: { is_disabled: true } }));
    }
    await dispatchOrFail(env, "render", job.id);
    return json({ ok: true });
  }
  if (p === "/hook/spawn") {
    const moments = body.moments || [];
    let made = 0;
    for (const mo of moments) {
      if (body.dedupe) {
        const ex = await env.DB.prepare("SELECT id FROM jobs WHERE url = ?").bind(mo.url).first();
        if (ex) continue;
      }
      await newJob(env, "render", mo.url, { start: mo.start ?? null, end: mo.end ?? null, note: mo.note || null, brief: mo.brief ? String(mo.brief).slice(0, 2000) : null, parent: job ? job.id : null, silent: true });
      made++;
    }
    if (made) await say(env, `🔎 ${job ? `#${job.id}: ` : ""}нашёл ${made} момент(ов). Монтирую, пришлю каждый отдельно.`);
    return json({ ok: true, made });
  }
  if (!job) return json({ error: "no job" }, 404);
  if (p === "/hook/update") {
    const f = { stage: String(body.stage || "").slice(0, 120) };
    if (job.status === "queued") f.status = "working";
    if (body.run_url) f.run_url = body.run_url;
    await updateJob(env, job.id, f);
    if (job.ack_msg_id) {
      ctx.waitUntil(tg(env, "editMessageText", { chat_id: env.TELEGRAM_CHAT_ID, message_id: job.ack_msg_id,
        text: `⏳ #${job.id}: ${f.stage}…`, link_preview_options: { is_disabled: true } }));
    }
    return json({ ok: true });
  }
  if (p === "/hook/done") {
    if (body.kind === "vod") {
      await updateJob(env, job.id, { status: "done", stage: "готово", result: body.result });
      const r = body.result || {};
      const lines = (r.moments || []).map((m, i) => `${i + 1}. ${esc(m.note || "")}`).join("\n");
      if (r.collect) await say(env, `📚 #${job.id}: ${esc(r.how || "")}. В библиотеке ${(r.library || []).length} моментов (оценка 6+) из ${r.candidates || 0}. Лучшие:\n${lines.split("\n").slice(0, 10).join("\n")}`);
      else if (lines) await say(env, `🎯 #${job.id}: ${esc(r.how || "")}. Выбрал ${(r.moments || []).length} из ${r.candidates || 0} историй:\n${lines}`);
      return json({ ok: true });
    }
    if (body.kind === "longscript") {
      await updateJob(env, job.id, { status: "script", stage: "сценарий ждёт утверждения", result: body.result });
      await sendScript(env, job.id, body.result);
      return json({ ok: true });
    }
    if (body.kind === "long") {
      await updateJob(env, job.id, { status: "ready", stage: "готов", result: body.result, error: null });
      const r = body.result || {};
      await say(env, `✅ Лонг #${job.id} готов: ${Math.round((r.duration || 0) / 60)} мин.\nВыше — превью (сжатое, чтобы влезло в Telegram), обложка и файл с названием, описанием и главами.${r.full_url ? "\nПолное качество: " + esc(r.full_url) : ""}`);
      return json({ ok: true });
    }
    if (body.kind === "ref") {
      const r = body.result || {};
      await updateJob(env, job.id, { status: "done", stage: "разобран", result: r });
      const rules = (r.rules_ru || []).slice(0, 8).map((x) => "• " + esc(x)).join("\n");
      await say(env, `📚 Пример #${job.id} разобран (${esc(r.format || "")}, ${Math.round(r.duration || 0)} сек):\n<b>${esc(r.title || "")}</b>\n\n${esc(r.summary_ru || "")}\n\n<b>Что взять себе:</b>\n${rules}`);
      return json({ ok: true });
    }
    if (body.kind === "upload") {
      const r = { ...(job.result || {}), ...body.result };
      await updateJob(env, job.id, { status: "published", stage: "на YouTube", result: r });
      await say(env, `✅ #${job.id} на YouTube: ${esc(body.result.youtube_url || "")}`);
      return json({ ok: true });
    }
    const r = body.result || {};
    await updateJob(env, job.id, { status: "ready", stage: "готов", result: r, chosen: 0, title: null,
      flow_prompt: job.flow_ok ? job.flow_prompt : (r.flow_prompt || null), version: (job.version || 0) + 1, error: null });
    const j = await getJob(env, job.id);
    await sendCard(env, j);
    if (job.ack_msg_id) await tg(env, "editMessageText", { chat_id: env.TELEGRAM_CHAT_ID, message_id: job.ack_msg_id, text: `✅ #${job.id} готов — смотри ниже.` });
    return json({ ok: true });
  }
  if (p === "/hook/fail") {
    await updateJob(env, job.id, { status: "failed", stage: "ошибка", error: String(body.error || "").slice(0, 1000) });
    const head = body.user ? "😕" : "⚠️";
    await say(env, `${head} #${job.id}: ${esc(String(body.error || "ошибка").slice(0, 600))}`);
    if (job.ack_msg_id) await tg(env, "editMessageText", { chat_id: env.TELEGRAM_CHAT_ID, message_id: job.ack_msg_id, text: `${head} #${job.id}: не получилось` });
    return json({ ok: true });
  }
  return json({ error: "unknown hook" }, 404);
}

// ------------------------------------------------------------------ panel auth + API
function randomToken() {
  const b = new Uint8Array(24);
  crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}
function cookie(req, name) {
  const m = (req.headers.get("cookie") || "").match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`));
  return m ? m[1] : null;
}
async function session(req, env) {
  const sid = cookie(req, "sid");
  return sid ? !!(await kvGet(env, `sess:${sid}`)) : false;
}
async function login(env, url) {
  const t = url.searchParams.get("t") || "";
  if (!t || !(await kvGet(env, `login:${t}`))) return new Response("Ссылка устарела. Напиши боту /login ещё раз.", { status: 403, headers: { "content-type": "text/plain; charset=utf-8" } });
  await kvDel(env, `login:${t}`);
  const sid = randomToken();
  await kvSet(env, `sess:${sid}`, 1, 30 * 24 * 3600 * 1000);
  return new Response("", { status: 302, headers: { location: "/", "set-cookie": `sid=${sid}; Max-Age=${30 * 24 * 3600}; Path=/; Secure; HttpOnly; SameSite=Lax` } });
}

async function api(req, env, ctx, p, url) {
  if (!(await session(req, env))) return json({ error: "login" }, 401);
  if (req.method !== "GET") {
    const o = req.headers.get("origin");
    if (o && o !== url.origin) return json({ error: "bad origin" }, 403);
  }
  if (p === "/api/jobs") {
    const rows = await env.DB.prepare("SELECT * FROM jobs ORDER BY id DESC LIMIT 60").all();
    return json((rows.results || []).map((j) => {
      const r = j.result ? JSON.parse(j.result) : null;
      return { id: j.id, kind: j.kind, url: j.url, status: j.status, stage: j.stage, created_at: j.created_at, note: j.note,
        parent_id: j.parent_id, title: currentTitle(j, r), score: r && r.score, duration: r && r.duration, streamer: r && r.streamer,
        has_video: !!(r && r.video_file_id) };
    }));
  }
  let m = p.match(/^\/api\/job\/(\d+)$/);
  if (m) {
    const job = await getJob(env, Number(m[1]));
    if (!job) return json({ error: "no job" }, 404);
    if (req.method === "POST") {
      const b = await req.json();
      const res = await doAction(env, job, b.act, b.arg, b.text || null, "web");
      return json({ ...(res || { ok: true }), job: publicJob(await getJob(env, job.id)) });
    }
    return json(publicJob(job));
  }
  if (p === "/api/new" && req.method === "POST") {
    const b = await req.json();
    const u = extractUrl(b.url);
    if (!u) return json({ error: "Нужна ссылка" }, 400);
    const id = await newJob(env, b.kind === "vod" ? "vod" : "render", u, {});
    return json({ ok: true, id });
  }
  if (p === "/api/settings") {
    if (req.method === "POST") {
      const b = await req.json();
      await kvSet(env, "style", cleanStyle(b.style || {}));
      if ("yt_auto" in b) await kvSet(env, "yt_auto", !!b.yt_auto);
    }
    return json({ style: await getStyle(env), defaults: DEFAULT_STYLE, yt_auto: !!(await kvGet(env, "yt_auto")) });
  }
  m = p.match(/^\/api\/video\/(\d+)$/);
  if (m) {
    const job = await getJob(env, Number(m[1]));
    const fid = job && job.result && job.result.video_file_id;
    if (!fid) return new Response("no video", { status: 404 });
    const f = await tg(env, "getFile", { file_id: fid });
    if (!f.ok) return new Response("telegram: " + (f.description || "error"), { status: 502 });
    const range = req.headers.get("range");
    const up = await fetch(`https://api.telegram.org/file/bot${env.TELEGRAM_BOT_TOKEN}/${f.result.file_path}`, { headers: range ? { range } : {} });
    const h = new Headers();
    for (const k of ["content-length", "content-range", "accept-ranges"]) if (up.headers.get(k)) h.set(k, up.headers.get(k));
    h.set("content-type", "video/mp4");
    h.set("cache-control", "private, max-age=3600");
    if (url.searchParams.get("dl")) h.set("content-disposition", `attachment; filename="gta6_short_${job.id}.mp4"`);
    return new Response(up.body, { status: up.status, headers: h });
  }
  return json({ error: "not found" }, 404);
}

function publicJob(job) {
  const r = job.result ? { ...job.result } : null;
  if (r) delete r.words, delete r.ai;
  return { ...job, result: r, current_title: currentTitle(job, r), current_description: currentDescription(job, r) };
}
