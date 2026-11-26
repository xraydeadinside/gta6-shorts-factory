// Script for long #32 "GTA 6 beginner mistakes", written by Claude from the chapters of the 4 most watched
// guides on the topic. Every point is named by at least 2 different creators. For every point the laptop
// downloads 2 pieces (~25 s each) from the matching chapters; narrator lines alternate between them.
// Narration stays at the level of what the chapter titles say: no invented numbers, names or locations.
import fs from "node:fs";
import { EXTRA } from "./script32_extra.mjs";

const V = {
  vex: { id: "r3oAEokOT5w", channel: "Vexies", title: "15 Early Game Mistakes NOBODY Warned Me About (GTA 6)", views: 2711416, duration: 982 },
  big: { id: "CiHuzgwaWYw", channel: "BiggySweats", title: "DON'T Finish Chapter 1 in GTA 6 Until You Do THIS!", views: 1213313, duration: 481 },
  holy: { id: "FvbVccBoVds", channel: "Holy13", title: "GTA 6: DON'T Make These Mistakes in Chapter 1 (Spoiler-Free)", views: 1134209, duration: 543 },
  nor: { id: "1ReyVNDmwS4", channel: "NorZZa", title: "Do This Before It's TOO LATE In Chapter 1 Of Grand Theft Auto 6!", views: 733343, duration: 802 },
};
const t = (x) => { const [m, s] = x.split(":").map(Number); return m * 60 + s; };
// a piece inside a chapter [a, b): skip the creator's first seconds of talking, up to `len` seconds
const piece = (v, a, b, line, len = 34) => {
  const A = t(a), B = b ? t(b) : V[v].duration;
  const start = Math.min(A + 7, Math.max(A + 2, B - 12));
  const end = Math.min(B - 1, start + len, V[v].duration - 1);
  return { video: V[v].id, start, end, line };
};

// ordered from #10 (first shown) to #1
const items = [
  { label_en: "RUSHING THE STORY", what_ru: "Проскакивать сюжет и закончить 1-ю главу, не сделав главного",
    shots: [piece("holy", "3:47", "5:21", 0), piece("big", "0:00", "0:35", 1)], sources: ["vex", "big", "holy"],
    vo_lines: [
      "Number ten: rushing the story.",
      "I get it. The missions are exciting, and you want to see what happens next.",
      "But almost every big guide says the same thing: slow down.",
      "GTA 6 hides a lot of its best stuff off the main path.",
      "If you only chase the next mission marker, you'll blow past most of it.",
      "Take a detour now and then. That's where this game really opens up.",
    ], vo_after: "Leonida isn't going anywhere." },
  { label_en: "DRIVING EVERYWHERE", what_ru: "Ездить через всю карту вместо быстрого перемещения",
    shots: [piece("vex", "5:00", "5:51", 0), piece("nor", "8:52", "10:14", 1)], sources: ["vex", "nor"],
    vo_lines: [
      "Number nine: driving everywhere.",
      "Cruising through Vice City at sunset is amazing. The first ten times.",
      "But when you're crossing the whole map just to start a mission, it adds up fast.",
      "Fast travel exists for a reason.",
      "Guides point out it can even save you money, not just time.",
      "Drive when you want to enjoy the ride, not because you forgot there's a shortcut.",
    ], vo_after: "Your evenings will thank you." },
  { label_en: "WRONG CAR UPGRADES", what_ru: "Вкладывать деньги не в те машины",
    shots: [piece("vex", "5:51", "6:21", 0), piece("holy", "6:17", "7:17", 1)], sources: ["vex", "holy"],
    vo_lines: [
      "Number eight: upgrading the wrong cars.",
      "Early on, every dollar matters.",
      "And it's really tempting to dump it all into the first shiny car you steal.",
      "Then you find something better an hour later, and that money is just gone.",
      "Spend on a car you'll actually keep using.",
      "Everything else can wait until you're rich.",
    ], vo_after: "Pretty doesn't pay the rent." },
  { label_en: "SKIPPING BUFFS", what_ru: "Пропускать постоянные улучшения персонажа",
    shots: [piece("holy", "5:21", "6:17", 0), piece("nor", "6:36", "8:12", 1)], sources: ["holy", "nor"],
    vo_lines: [
      "Number seven: skipping your permanent upgrades.",
      "GTA 6 lets you make your character stronger for good.",
      "And those upgrades don't just look nice on a menu. You feel them in every fight and every chase.",
      "The earlier you start, the easier every mission after that becomes.",
      "So many players only find out about this deep into the game.",
      "Don't be one of them.",
    ], vo_after: "Free power. Take it." },
  { label_en: "BAD ROBBERIES", what_ru: "Грабить магазины неправильно",
    shots: [piece("vex", "12:04", "12:48", 0), piece("nor", "10:14", "12:20", 1)], sources: ["vex", "nor"],
    vo_lines: [
      "Number six: robbing stores the wrong way.",
      "Running in with no plan feels great for about ten seconds.",
      "Then the police show up, the payout is tiny, and you spend the next five minutes running for your life.",
      "Robberies in GTA 6 reward a little planning.",
      "Creators who tested it say a smarter approach pays more and brings less heat.",
      "Think first, then cause chaos.",
    ], vo_after: "Crime pays. Bad crime doesn't." },
  { label_en: "WEAK WEAPONS", what_ru: "Не прокачивать оружие и пропускать особое оружие",
    shots: [piece("vex", "3:05", "4:04", 0), piece("nor", "0:17", "3:42", 1)], sources: ["vex", "nor", "big"],
    vo_lines: [
      "Number five: sticking with weak weapons.",
      "A lot of players fight the whole first chapter with whatever they started with.",
      "Upgrading your guns makes missions so much easier.",
      "And some special weapons can be picked up early, if you know where to look.",
      "Several guides call them out as things you should grab before moving on.",
      "Better gear now means fewer restarts later.",
    ], vo_after: "Don't bring a pea shooter to Vice City." },
  { label_en: "MISSING FREE CASH", what_ru: "Пропускать бесплатные деньги в начале игры",
    shots: [piece("vex", "2:04", "3:05", 0), piece("big", "1:52", "2:56", 1)], sources: ["vex", "big", "nor"],
    vo_lines: [
      "Number four: walking right past free money.",
      "The early game is when you're the most broke.",
      "And it's exactly when there's cash hidden in places most players never check.",
      "Different creators found it in different spots, which tells you how easy it is to miss.",
      "A few minutes of looking around can set you up for hours.",
      "Check those guides, then go collect.",
    ], vo_after: "Free money is the best money." },
  { label_en: "TRUSTING AUTOSAVE", what_ru: "Полагаться на автосохранение и не сохраняться вручную",
    shots: [piece("vex", "6:21", "7:18", 0), piece("big", "6:32", "7:08", 1)], sources: ["vex", "big", "holy"],
    vo_lines: [
      "Number three: trusting autosave.",
      "This one sounds boring. It's also the one that hurts the most when it goes wrong.",
      "Autosave won't always save right where you need it.",
      "Lose a big mission or a lot of progress, and you'll wish you'd done it yourself.",
      "Make a manual save before anything important.",
      "It takes ten seconds.",
      "Guides recommend specific moments in Chapter One where a manual save really matters.",
    ], vo_after: "Ten seconds now, hours saved later." },
  { label_en: "SPENDING TOO EARLY", what_ru: "Тратить деньги слишком рано",
    shots: [piece("vex", "0:29", "2:04", 0), piece("holy", "2:23", "3:47", 1), piece("nor", "12:20", null, 2)],
    sources: ["vex", "holy", "big", "nor"],
    vo_lines: [
      "Number two: spending your money too early.",
      "Almost every guide puts this near the top of the list.",
      "Your first paychecks feel huge. So you buy clothes, cars, random stuff.",
      "And then the things that actually matter unlock, and you can't afford them.",
      "One creator literally calls it a massive cash mistake.",
      "Protect your money in the first hours.",
      "Spend on what makes you stronger or makes you more money. The rest can wait.",
    ], vo_after: "Be patient. Be rich." },
  { label_en: "MISSING CHAPTER 1", what_ru: "Пропустить пропускаемые задания и предметы 1-й главы",
    shots: [piece("big", "2:56", "4:02", 0), piece("big", "4:44", "5:11", 1), piece("holy", "0:48", "2:23", 2)],
    sources: ["big", "holy", "nor"],
    vo_lines: [
      "And number one: leaving Chapter One too early.",
      "This is the mistake you can't undo.",
      "Some side missions and unique items in Chapter One are missable.",
      "That means once you move the story forward, they can be gone for good.",
      "Different guides list missable side missions, a unique weapon, and things you should do before you leave.",
      "So before you finish Chapter One, stop, and go through that checklist.",
      "Your future self will thank you.",
    ], vo_after: "You've been warned. Now go check." },
];

for (const it of items) it.vo_lines.push(...(EXTRA[it.label_en] || []));

const footage = [];
for (const [k, it] of items.entries()) {
  it.sources = it.sources.map((x) => V[x].id);
  for (const sh of it.shots) {
    sh.part = footage.length;
    const v = Object.values(V).find((x) => x.id === sh.video);
    if (sh.end > v.duration - 0.5 || sh.end - sh.start < 8) throw new Error(`bad shot ${v.channel} ${sh.start}-${sh.end}`);
    footage.push({ part: sh.part, url: `https://www.youtube.com/watch?v=${sh.video}`, start: sh.start,
      end: sh.end, item: k, channel: v.channel });
  }
}

const script = {
  title: "10 GTA 6 Beginner Mistakes Almost Everyone Makes (Don't Do #1!)",
  alt_titles: ["GTA 6: 10 Mistakes That Ruin Your First Week", "Stop Doing This in GTA 6 — 10 Beginner Mistakes"],
  thumbnail_text: "DON'T DO THIS",
  thumbnail_idea: "Jason looking shocked at an empty wallet, big red X, yellow text DON'T DO THIS",
  hook_vo: "Most GTA 6 players are making these mistakes right now.",
  promise_vo: "I went through the most watched GTA 6 guides and pulled out the ten mistakes they all keep warning about.",
  stakes_vo: "And number one? If you've already finished Chapter One, you might be too late.",
  intro_vo: "Every mistake here shows up in at least two different guides. No rumors. Let's start at number ten.",
  mid_cta_vo: "If this is already saving you some pain, subscribe. We break down GTA 6 like this every week.",
  rehook_vo: "Now the top three. These are the ones that cost players the most. And number one is the only one you can't fix later.",
  outro_cta_vo: "So, which mistake did you already make? Be honest, and tell me in the comments.",
  endscreen_vo: "And if you want to see how the biggest streamers handled GTA 6, this video is next.",
  items,
  description: "GTA 6 beginner mistakes: the 10 mistakes almost every new player makes in Grand Theft Auto 6, and how to avoid them. Every point is confirmed by at least two of the most watched GTA 6 guides.\n\nFrom spending your money too early to the missable content in Chapter One, these are the things the biggest GTA 6 creators keep warning about. Watch to the end, number one is the only mistake you can't undo.\n\nFootage: videos credited below. Go support these creators.",
  tags: ["gta 6 beginner mistakes", "gta 6 tips", "gta 6 mistakes", "gta 6 beginner guide", "gta 6 chapter 1", "gta 6 money", "gta 6 missable", "gta 6 tips and tricks", "gta 6 guide", "gta vi", "grand theft auto 6", "gta 6"],
  explanation_ru: "Сценарий написал Claude (Gemini был перегружен) по главам 4 самых популярных гайдов: Vexies 2,7 млн, BiggySweats, Holy13, NorZZa. Взял только ошибки, которые называют минимум 2 автора. #1 — пропускаемое в 1-й главе (единственное, что нельзя исправить), #2 — ранние траты (есть почти у всех). Под каждый пункт — 2–3 куска из соответствующих глав, кадр меняется с каждой фразой.",
  minutes: 10,
  topic: "beginner mistakes",
};

const result = { script, footage, sources: Object.values(V) };
fs.writeFileSync("research/script32.json", JSON.stringify(result, null, 1));
const all = [script.hook_vo, script.promise_vo, script.stakes_vo, script.intro_vo, script.mid_cta_vo, script.rehook_vo,
  script.outro_cta_vo, script.endscreen_vo, ...items.flatMap((i) => [...i.vo_lines, i.vo_after])].join(" ");
const w = all.split(/\s+/).length;
console.log(`items ${items.length}, pieces ${footage.length}, narration ${w} words (~${(w / 150).toFixed(1)} min of voice), ${all.length} chars`);
console.log(footage.map((f) => `${f.part}:${f.channel} ${f.start}-${f.end}`).join("  "));
