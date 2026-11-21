// Web panel (served by the Worker). Plain HTML + JS, no build step.

const BASE_CSS = `
:root{--bg:#0f1115;--card:#171a21;--line:#2a2f3a;--txt:#e9ecf1;--mut:#8e97a8;--acc:#ffe600;--ok:#3ddc84;--bad:#ff5d5d;--btn:#232834}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--acc)}button,input,textarea,select{font:inherit;color:inherit}
button{background:var(--btn);border:1px solid var(--line);border-radius:10px;padding:8px 12px;cursor:pointer}
button:hover{border-color:var(--acc)}button.pri{background:var(--acc);color:#111;border-color:var(--acc);font-weight:600}
button.bad{border-color:#5a2a2a}input,textarea,select{background:#0c0e12;border:1px solid var(--line);border-radius:10px;padding:8px 10px;width:100%}
textarea{min-height:90px;resize:vertical}.mut{color:var(--mut)}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
`;

export const LOGIN_HTML = `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Фабрика шортсов</title><style>${BASE_CSS}
.box{max-width:420px;margin:18vh auto;padding:28px;background:var(--card);border:1px solid var(--line);border-radius:16px;text-align:center}</style></head>
<body><div class="box"><h2>🎬 Фабрика шортсов GTA 6</h2><p>Вход только для владельца.</p>
<p>Напиши своему боту в Telegram команду <b>/login</b> — он пришлёт одноразовую ссылку для входа.</p></div></body></html>`;

export const PANEL_HTML = `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Фабрика шортсов</title><style>${BASE_CSS}
header{display:flex;gap:12px;align-items:center;padding:12px 18px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:2}
header h1{font-size:18px;margin:0 12px 0 0;white-space:nowrap}header form{flex:1;display:flex;gap:8px;min-width:0}
main{display:grid;grid-template-columns:330px 1fr;gap:0;min-height:calc(100vh - 62px)}
#list{border-right:1px solid var(--line);overflow:auto;max-height:calc(100vh - 62px)}
.item{padding:10px 14px;border-bottom:1px solid var(--line);cursor:pointer}.item:hover,.item.on{background:var(--card)}
.item .t{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.item .s{font-size:13px;color:var(--mut)}
#detail{padding:18px;overflow:auto;max-height:calc(100vh - 62px)}
.grid{display:grid;grid-template-columns:minmax(220px,320px) 1fr;gap:20px}
video{width:100%;border-radius:14px;background:#000;aspect-ratio:9/16}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:14px}
.card h3{margin:0 0 10px;font-size:15px}.titles label{display:flex;gap:8px;align-items:flex-start;padding:6px 0;cursor:pointer}
.titles input{width:auto;margin-top:4px}table{width:100%;border-collapse:collapse;font-size:14px}
td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--mut);font-weight:500}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;background:var(--btn);font-size:12px}
pre{white-space:pre-wrap;margin:0;font:inherit}.toast{position:fixed;right:18px;bottom:18px;background:var(--acc);color:#111;padding:10px 14px;border-radius:10px;display:none;font-weight:600}
dialog{background:var(--card);color:var(--txt);border:1px solid var(--line);border-radius:16px;max-width:640px;width:92vw}
.set{display:grid;grid-template-columns:1fr 1fr;gap:10px 16px}.set label{font-size:13px;color:var(--mut)}
@media (max-width:860px){main{grid-template-columns:1fr}#list{max-height:none;border-right:0}.grid{grid-template-columns:1fr}header{flex-wrap:wrap}}
</style></head><body>
<header><h1>🎬 Шортсы GTA 6</h1>
<form id="newf"><input id="newurl" placeholder="Ссылка: клип Twitch, VOD с ?t=…, YouTube" autocomplete="off">
<button class="pri" data-kind="render">Смонтировать</button><button data-kind="vod" title="ИИ смотрит запись и находит лучшие истории">VOD</button></form>
<button id="setbtn">⚙️ Стиль</button><a href="/logout" class="mut">Выйти</a></header>
<main><div id="list"></div><div id="detail"><p class="mut">Выбери клип слева или пришли ссылку.</p></div></main>
<dialog id="setd"><h3>Стиль монтажа</h3><div class="set" id="setf"></div>
<p><label><input type="checkbox" id="ytauto" style="width:auto"> Автозагрузка на YouTube (пока выключена — нужны ключи YT_*)</label></p>
<div class="row"><button class="pri" id="setsave">Сохранить</button><button id="setreset">Сбросить</button><button onclick="setd.close()">Закрыть</button></div></dialog>
<div class="toast" id="toast"></div>
<script>
const ST={queued:"⏳ в очереди",working:"⚙️ монтирую",ready:"🟢 готов",published:"✅ опубликован",rejected:"❌ отклонён",failed:"⚠️ ошибка",done:"✔️ готово"};
const ROLE={hook:"Хук",setup:"Завязка",punchline:"Панчлайн",reaction:"Реакция",payoff:"Развязка"};
const CAM={push:"наезд",pan:"проезд",whip:"рывок",hold:"держим кадр",cut:"склейка"};
const LBL={cam_height:"Доля кадра под вебку (0.25–0.5)",max_len:"Макс. длина, сек",min_len:"Мин. длина, сек",caption_words:"Слов в субтитре (1 или 2)",caption_size:"Размер субтитров",caption_color:"Цвет субтитров",punch_color:"Цвет панчлайна",title_color:"Цвет заголовка",title_accent:"Акцент заголовка",title_seconds:"Заголовок, сек",zoom_strength:"Сила наездов (0.5–1.5)",react_zoom:"Зум на реакции",shouts:"Всплывающие выкрики",whoosh:"«Вжух» на склейках",whoosh_volume:"Громкость вжуха",sfx:"Звуковые эффекты",sfx_volume:"Громкость эффектов (0.5–1.5)",watermark:"Подпись twitch.tv/стример",lufs:"Громкость, LUFS"};
let jobs=[],cur=null,timer=null;
const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmt=s=>{s=Math.round(+s||0);return Math.floor(s/60)+":"+String(s%60).padStart(2,"0")};
function toast(t){const e=$("#toast");e.textContent=t;e.style.display="block";clearTimeout(e._t);e._t=setTimeout(()=>e.style.display="none",2600)}
async function api(path,body){const r=await fetch(path,body?{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)}:{});
 if(r.status===401){location.reload();throw new Error("login")}const d=await r.json();if(!r.ok||d.error)throw new Error(d.error||r.status);return d}
async function loadList(){jobs=await api("/api/jobs");renderList();
 const busy=jobs.some(j=>j.status==="working"||j.status==="queued");clearTimeout(timer);timer=setTimeout(async()=>{await loadList();if(cur)refreshCur(true)},busy?5000:20000)}
function renderList(){$("#list").innerHTML=jobs.map(j=>'<div class="item'+(cur&&cur.id===j.id?' on':'')+'" data-id="'+j.id+'"><div class="t">#'+j.id+' '+esc(j.title||j.url)+'</div><div class="s">'+(ST[j.status]||j.status)+(j.status==="working"&&j.stage?" — "+esc(j.stage):"")+(j.score?' · ⭐'+j.score:'')+(j.duration?' · '+fmt(j.duration):'')+(j.streamer?' · '+esc(j.streamer):'')+(j.kind==="vod"?' · VOD':'')+'</div></div>').join("")||'<p class="mut" style="padding:14px">Пока пусто.</p>';
 document.querySelectorAll(".item").forEach(e=>e.onclick=()=>openJob(+e.dataset.id))}
async function openJob(id){cur=await api("/api/job/"+id);renderList();renderDetail()}
async function refreshCur(quiet){if(!cur)return;const old=cur.status+cur.version;cur=await api("/api/job/"+cur.id);if(!quiet||old!==cur.status+cur.version)renderDetail()}
function renderDetail(){const j=cur,r=j.result||{};const d=$("#detail");
 if(j.kind==="vod"){d.innerHTML='<div class="card"><h3>#'+j.id+' VOD · '+(ST[j.status]||j.status)+'</h3><p>'+esc(j.url)+'</p>'+(r.moments?'<table><tr><th>Момент</th><th>Почему</th></tr>'+r.moments.map(m=>'<tr><td><a href="'+esc(m.url)+'" target="_blank">'+esc(m.url.split("t=")[1]||"")+'</a></td><td>'+esc(m.note)+'</td></tr>').join("")+'</table>':'<p class="mut">'+esc(j.stage||"")+'</p>')+(j.error?'<p style="color:var(--bad)">'+esc(j.error)+'</p>':'')+'</div>';return}
 const titles=r.titles||[];const busy=j.status==="working"||j.status==="queued";
 d.innerHTML='<div class="grid"><div>'+(r.video_file_id?'<video controls playsinline preload="metadata" src="/api/video/'+j.id+'?v='+j.version+'"></video><div class="row" style="margin-top:10px"><a href="/api/video/'+j.id+'?dl=1"><button>⬇️ Скачать mp4</button></a></div>':'<div class="card mut">'+(busy?'⚙️ '+esc(j.stage||'в очереди')+'…':'Видео пока нет')+'</div>')+
 '<div class="card" style="margin-top:14px"><div class="mut" style="font-size:13px">'+[r.streamer?'twitch.tv/'+esc(r.streamer):'',r.score?'⭐ '+r.score+'/10':'',r.duration?fmt(r.duration):'',r.lufs?r.lufs+' LUFS':'',r.size_mb?r.size_mb+' МБ':'',r.webcam?(r.webcam.present?'вебка: '+esc(r.webcam.how||''):'без вебки'):''].filter(Boolean).join(' · ')+'</div><p><a href="'+esc(j.url)+'" target="_blank">Источник</a>'+(j.run_url?' · <a href="'+esc(j.run_url)+'" target="_blank">лог монтажа</a>':'')+'</p><span class="pill">'+(ST[j.status]||j.status)+'</span></div></div>'+
 '<div>'+(j.error?'<div class="card" style="border-color:#5a2a2a"><b>Ошибка:</b> '+esc(j.error)+'</div>':'')+
 (r.verdict?'<div class="card"><b>Оценка ИИ:</b> '+esc(r.verdict)+(r.viewed_pct?'<br><span class="mut">📈 прогноз досмотра ~'+r.viewed_pct+'% · '+esc(r.risk||'')+'</span>':'')+'</div>':'')+
 '<div class="card titles"><h3>Название</h3>'+titles.map((t,i)=>'<label><input type="radio" name="tt" value="'+i+'"'+(!j.title&&(j.chosen||0)===i?' checked':'')+'> <span>'+esc(t)+'</span></label>').join("")+
 '<label><input type="radio" name="tt" value="c"'+(j.title?' checked':'')+'> <input id="ctitle" placeholder="Своё название" value="'+esc(j.title||'')+'"></label>'+
 '<div class="row"><button id="b_tsave">Сохранить название</button><button id="b_nt">🔄 Новые названия</button></div></div>'+
 '<div class="card"><h3>Описание</h3><textarea id="desc" rows="6">'+esc(j.current_description||'')+'</textarea><div class="row" style="margin-top:8px"><button id="b_dsave">Сохранить описание</button><span class="mut">Теги: '+esc((r.tags||[]).join(", "))+'</span></div></div>'+
 '<div class="card"><h3>🧠 План монтажа</h3><pre>'+esc(r.explanation||'')+'</pre>'+(r.plan?'<table style="margin-top:10px"><tr><th>Время</th><th>Часть</th><th>Кадр</th><th>Что происходит</th></tr>'+r.plan.map(p=>'<tr><td>'+fmt(p.at)+'</td><td>'+(ROLE[p.role]||p.role)+'</td><td>'+(p.shot==="react"?"крупно лицо":(CAM[p.camera]||p.camera))+(p.zoom>1.01?' ×'+(+p.zoom).toFixed(2):'')+'</td><td>'+esc(p.note||'')+' <span class="mut">('+p.from+'–'+p.to+' с)</span></td></tr>').join("")+'</table>':'')+'</div>'+
 '<div class="card"><h3>✂️ Перемонтировать</h3><textarea id="wish" placeholder="Что поменять? Например: короче, начни с аварии, больше лица стримера">'+esc(j.wish||'')+'</textarea><div class="row" style="margin-top:8px"><button id="b_rm"'+(busy?' disabled':'')+'>Перемонтировать</button></div></div>'+
 '<div class="card"><h3>🎨 Промпт для Google Flow '+(j.flow_ok?'<span class="pill">утверждён</span>':'')+'</h3><textarea id="flow" rows="4">'+esc(j.flow_prompt||r.flow_prompt||'')+'</textarea>'+
 '<div class="row" style="margin-top:8px"><button id="b_fa">✅ Утвердить</button><button id="b_fo">🔁 Другой</button><input id="fwish" placeholder="Пожелание к промпту" style="flex:1;min-width:160px"><button id="b_fw">💬 Применить</button></div>'+
 '<p class="mut" style="font-size:13px">Готовое видео из Flow пришли боту в Telegram ответом на сообщение с промптом — оно встанет интро.</p></div>'+
 '<div class="row"><button class="pri" id="b_pub">🚀 Опубликовать</button><button class="bad" id="b_rej">'+(j.status==="rejected"?"↩️ Вернуть":"❌ Отклонить")+'</button></div></div></div>';
 const act=async(a,extra,msg)=>{try{const res=await api("/api/job/"+j.id,{act:a,...extra});cur=res.job;renderDetail();loadList();toast(res.message||msg||"Готово");return res}catch(e){toast("Ошибка: "+e.message)}};
 $("#b_tsave").onclick=()=>{const v=document.querySelector('input[name=tt]:checked');if(!v)return;v.value==="c"?act("ct",{text:$("#ctitle").value},"Название сохранено"):act("t",{arg:v.value},"Название выбрано")};
 $("#b_nt").onclick=()=>{toast("Придумываю…");act("nt",{},"Новые названия")};
 $("#b_dsave").onclick=()=>act("cd",{text:$("#desc").value},"Описание сохранено");
 $("#b_rm").onclick=()=>{const w=$("#wish").value.trim();if(!w)return toast("Напиши пожелание");act("rm",{text:w},"Перемонтирую")};
 $("#b_fa").onclick=()=>act("fa",{},"Утверждено");
 $("#b_fo").onclick=async()=>{toast("Придумываю…");const r=await act("fo",{});};
 $("#b_fw").onclick=()=>{const w=$("#fwish").value.trim();if(!w)return toast("Напиши пожелание");toast("Переписываю…");act("fw",{text:w})};
 $("#b_pub").onclick=()=>act("pub",{},"Отмечено как опубликованное. Скачай mp4 и выложи, название и описание — выше.");
 $("#b_rej").onclick=()=>act(j.status==="rejected"?"un":"rej",{});
}
$("#newf").onsubmit=e=>e.preventDefault();
document.querySelectorAll("#newf button").forEach(b=>b.onclick=async e=>{e.preventDefault();const url=$("#newurl").value.trim();if(!url)return toast("Вставь ссылку");
 try{const r=await api("/api/new",{url,kind:b.dataset.kind});$("#newurl").value="";toast("Принял #"+r.id);await loadList();openJob(r.id)}catch(err){toast("Ошибка: "+err.message)}});
let S=null;
$("#setbtn").onclick=async()=>{S=await api("/api/settings");drawSet(S.style);$("#ytauto").checked=S.yt_auto;setd.showModal()};
function drawSet(st){$("#setf").innerHTML=Object.keys(S.defaults).map(k=>{const v=st[k],d=S.defaults[k];
 const inp=typeof d==="boolean"?'<input type="checkbox" style="width:auto" data-k="'+k+'"'+(v?' checked':'')+'>':(/color|accent/.test(k)?'<input type="color" data-k="'+k+'" value="'+esc(v)+'">':'<input type="number" step="any" data-k="'+k+'" value="'+esc(v)+'">');
 return '<div><label>'+(LBL[k]||k)+'</label><br>'+inp+'</div>'}).join("")}
$("#setsave").onclick=async()=>{const st={};document.querySelectorAll("#setf [data-k]").forEach(e=>st[e.dataset.k]=e.type==="checkbox"?e.checked:e.value);
 await api("/api/settings",{style:st,yt_auto:$("#ytauto").checked});toast("Стиль сохранён — применится к следующим монтажам");setd.close()};
$("#setreset").onclick=()=>drawSet(S.defaults);
loadList().then(()=>{const first=jobs[0];if(first)openJob(first.id)});
</script></body></html>`;
