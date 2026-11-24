// Records the walkthrough of https://idea2alpha.pages.dev shot by shot.
// Usage: node record.mjs [shot ...]   (no arguments records every shot)
// Each shot writes capture/<shot>/frames/*.jpg and capture/<shot>/shot.json (frame timestamps + markers).
// Markers: "say:Lxx" = narration line starts; "cut:start"/"cut:end" = waiting for the AI, trimmed in assembly.
import fs from "node:fs";
import path from "node:path";
import { Browser, Recorder, sleep } from "./cdp.mjs";

const ROOT = path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, "$1");
const SITE = "https://idea2alpha.pages.dev";
const DUR = JSON.parse(fs.readFileSync(path.join(ROOT, "voice", "durations.json"), "utf8"));
const CARD = (n) => "file:///" + path.join(ROOT, "cards", n).replace(/\\/g, "/");
const QUESTION = "What is the luck test on the Backtests page?";
const GAP = 250;
const log = (...a) => console.log(new Date().toISOString().slice(11, 23), ...a);

const br = new Browser(ROOT);
let rec = null;

// Narration line: mark its start, run the on-screen actions, then wait until the line (plus a short gap) is over.
async function line(id, fn = async () => {}, gap = GAP) {
  rec.mark("say:" + id);
  const t0 = Date.now();
  await fn(t0);
  const left = DUR[id] * 1000 + gap - (Date.now() - t0);
  if (left > 0) await sleep(left);
}
const at = async (t0, ms) => { const d = t0 + ms - Date.now(); if (d > 0) await sleep(d); };

// Point at the smallest visible element whose text matches; skip quietly if the page has none.
async function moveToText(rx, ms = 700, fx = 0.5, fy = 0.5) {
  const sel = `js:[...document.querySelectorAll('main *')].filter(e => ${rx}.test(e.textContent.trim()) && e.getBoundingClientRect().height > 0).sort((a, b) => a.textContent.length - b.textContent.length)[0]`;
  try { await br.moveTo(sel, ms, fx, fy); } catch (e) { log("  (no element for", String(rx), ")"); }
}

// Wait for the AI; footage between cut:start and cut:end is trimmed later, keeping the first moment of the wait.
async function aiWait(cond, keepMs = 900, limit = 45000) {
  const t0 = Date.now();
  let cut = false;
  for (;;) {
    let v = false;
    try { v = await br.eval(cond); } catch (e) { v = false; }
    if (v) break;
    if (!cut && Date.now() - t0 > keepMs) { rec.mark("cut:start"); cut = true; }
    if (Date.now() - t0 > limit) throw new Error("RETRY: the AI took longer than " + limit / 1000 + " s");
    await sleep(100);
  }
  if (cut) rec.mark("cut:end");
  return Date.now() - t0;
}

async function navClick(label) {
  const sel = `js:[...document.querySelectorAll('.nav a')].find(a => a.textContent.trim() === ${JSON.stringify(label)})`;
  if ((await br.eval("scrollY")) > 0) await br.scrollTo(0, 700);
  br.expectLoad();
  await br.moveTo(sel, 700);
  rec.mark("nav");
  await br.click(sel, 1);
  await sleep(250);
}

async function shot(name, setup, body) {
  log("shot", name);
  if (setup) await setup();
  rec = new Recorder(br, path.join(ROOT, "capture", name));
  await rec.start();
  try { await body(); } catch (e) {
    try { await br.send("Page.stopScreencast"); } catch (e2) { /* not running */ }
    br.off("Page.screencastFrame", rec.onFrame);
    throw e;
  }
  const r = await rec.stop();
  log("  frames", r.frames, "markers", r.markers);
}

const READY_TRY = "(() => { const n = document.querySelector('#live-note'); return n && /relay server/.test(n.textContent) && document.querySelector('#idea-display mark') && document.querySelector('#questions .opts button.opt'); })()";
const READY_ASK = "(() => { const n = document.querySelector('#ask-note'); return n && /relay server/.test(n.textContent) && document.querySelectorAll('#ask-chips button:not([disabled])').length === 6; })()";

const SHOTS = {
  async title() {
    await shot("title", async () => { await br.goto(CARD("title.html")); br.cur = { x: 1500, y: 900 }; await br.eval("__setCur(1500, 900, 0)"); }, async () => {
      await sleep(250);
      await br.eval("document.documentElement.classList.add('go')");
      await sleep(1100);
      await line("L01");
      await sleep(500);
    });
  },

  async try() {
    await shot("try", async () => {
      await br.goto(SITE + "/", READY_TRY);
      await sleep(1500);
      await br.scrollTo(0, 0);
      br.cur = { x: 1180, y: 330 }; await br.eval("__setCur(1180, 330, 0)");
    }, async () => {
      await sleep(400);
      await line("L02", async (t0) => {
        await at(t0, 300);
        await br.scrollTo(370, 1200);
        await br.moveTo("#idea-input", 800, 0.4, 0.4);
        await at(t0, 3900);
        await br.moveTo("#translate-btn", 700, 0.55, 0.55);
      });
      await line("L03", async () => {
        await br.click("#example-chips button[data-id=B03]", 800);
        await sleep(200);
        await br.moveTo("#idea-display", 700, 0.55, 0.3);
      });
      await line("L04", async () => {
        await br.moveTo("#idea-display mark", 600);
        await sleep(700);
        await br.moveTo("#idea-notes .note-chip", 700, 0.3, 0.5);
      });
      await line("L05", async () => {
        await br.moveTo("#questions .q-text", 800, 0.45, 0.5);
        await sleep(500);
        await br.moveTo("#questions .opts button.opt:nth-child(1)", 700, 0.5, 0.35);
      });
      await line("L06", async () => {
        await br.click("#questions .opts button.opt:nth-child(2)", 700, 0.5, 0.35);
        await sleep(500);
        await br.moveTo(".steps > li:nth-child(2)", 800, 0.35, 0.3);
      });
      await line("L07", async () => {
        await br.scrollToEl(".steps > li:nth-child(3)", 300, 1000);
        await br.moveTo(".steps > li:nth-child(3)", 600, 0.3, 0.5);
        await sleep(500);
        await br.scrollToEl(".steps > li:nth-child(4)", 90, 1200);
        try { await br.moveTo(".steps > li:nth-child(4) table tbody tr:nth-child(2)", 800, 0.35, 0.5); } catch (e) { await br.moveTo(".steps > li:nth-child(4)", 800, 0.35, 0.2); }
      });
      log("  matches:", await br.eval("(document.querySelector('.steps > li:nth-child(4)') || {}).innerText.slice(0, 120)"));
      await sleep(300);
      await navClick("Translator test");
    });
  },

  async evaluation() {
    await shot("evaluation", async () => { await br.waitNavigation("document.querySelector('#eval-bars .ebar-track')"); await sleep(800); }, async () => {
      await sleep(300);
      await line("L08", async (t0) => { await at(t0, 700); await br.scrollTo(345, 1300); });
      await line("L09", async () => {
        await br.moveTo("#eval-bars .ebar-track", 700, 0.08, 0.5);
        await sleep(900);
        await br.moveTo("js:[...document.querySelectorAll('#eval-bars .ebar-track')].at(-1)", 1100, 0.8, 0.5);
      });
      await sleep(300);
      await navClick("Backtests");
    });
  },

  async backtests() {
    await shot("backtests", async () => { await br.waitNavigation("document.querySelector('#eq-chart svg') && document.querySelector('#luck-chart svg')"); await sleep(800); }, async () => {
      await sleep(300);
      await line("L10", async (t0) => {
        await at(t0, 1300);
        await br.scrollTo(330, 1300);
        await br.moveTo("#eq-chart svg", 900, 0.35, 0.45);
        await br.moveTo("#eq-chart svg", 1600, 0.8, 0.4);
      });
      await line("L11", async () => {
        await br.moveTo("#luck-chart svg", 800, 0.5, 0.82);
        await sleep(700);
        await br.moveTo("#bt-takeaway", 900, 0.75, 0.75);
      });
      await sleep(300);
      await navClick("Debate");
    });
  },

  async debate() {
    await shot("debate", async () => { await br.waitNavigation("document.querySelector('.box')"); await sleep(800); }, async () => {
      await sleep(200);
      await line("L12", async (t0) => {
        await at(t0, 300);
        await br.scrollTo(890, 1500);
        await moveToText("/^insufficient/i", 800);
        await sleep(400);
        await br.scrollTo(1330, 1100);
        await moveToText("/^unsupported/i", 700);
      });
      await sleep(200);
      await navClick("Replay");
    });
  },

  async replay() {
    await shot("replay", async () => { await br.waitNavigation("document.querySelector('main svg')"); await sleep(800); }, async () => {
      await sleep(200);
      await line("L13", async (t0) => {
        await at(t0, 300);
        await br.scrollTo(330, 1200);
        await br.moveTo("main svg", 900, 0.42, 0.88);
        await sleep(500);
        await br.moveTo("main svg", 800, 0.92, 0.25);
      });
      await sleep(200);
      await navClick("Ask the AI");
    });
  },

  async ask() {
    await shot("ask", async () => { await br.waitNavigation(READY_ASK); await sleep(800); }, async () => {
      await sleep(300);
      let sent = 0;
      await line("L14", async (t0) => {
        await br.click("#ask-input", 700, 0.4, 0.4);
        await br.type(QUESTION, 16);
        await sleep(250);
        await br.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
        await br.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
        sent = Date.now();
      }, 0);
      await aiWait("document.querySelector('.ask-turn.done, .ask-turn.failed')", Math.max(300, 900 - (Date.now() - sent)));
      if (await br.eval("!!document.querySelector('.ask-turn.failed')")) throw new Error("RETRY: answer 1 failed");
      log("  answer 1:", await br.eval("document.querySelector('.ask-turn:last-child').innerText.replace(/\\s+/g, ' ').slice(0, 500)"));
      await sleep(200);
      await line("L14b", async () => {
        await br.moveTo(".ask-turn:last-child .ask-a", 900, 0.3, 0.3);
        await sleep(600);
        await br.moveTo(".ask-turn:last-child .ask-a", 1200, 0.6, 0.75);
      });
      await sleep(400);
      await br.click("#ask-clear", 800);
      await sleep(400);
      await br.click("#ask-chips button:nth-child(6)", 900);
      sent = Date.now();
      const w2 = await aiWait("document.querySelector('.ask-turn.done, .ask-turn.failed')", 900, 95000);
      log("  answer 2 after", w2, "ms");
      if (await br.eval("document.querySelectorAll('.ask-turn.failed').length > 0")) throw new Error("RETRY: answer 2 failed");
      log("  answer 2:", await br.eval("document.querySelector('.ask-turn:last-child').innerText.replace(/\\s+/g, ' ').slice(0, 500)"));
      await sleep(300);
      await line("L15", async () => { await br.moveTo(".ask-turn:last-child .ask-a", 700, 0.3, 0.4); });
      await sleep(900);
      await br.scrollTo(0, 800);
      await line("L16", async () => { await br.click("#lang-zh", 600); });
      await sleep(1600);
    });
  },

  async outro() {
    await shot("outro", async () => { await br.goto(CARD("outro.html")); br.cur = { x: 1500, y: 900 }; await br.eval("__setCur(1500, 900, 0)"); }, async () => {
      await sleep(250);
      await br.eval("document.documentElement.classList.add('go')");
      await sleep(900);
      await line("L17");
      await sleep(1500);
    });
  },
};

const want = process.argv.slice(2);
const order = ["title", "try", "evaluation", "backtests", "debate", "replay", "ask", "outro"];
await br.launch();
try {
  for (const name of order) {
    if (want.length && !want.includes(name)) continue;
    // A page shot that normally follows a nav click needs its page loaded when recorded on its own.
    const prev = order[order.indexOf(name) - 1];
    if (["evaluation", "backtests", "debate", "replay", "ask"].includes(name) && want.length && !want.includes(prev)) {
      await br.goto(`${SITE}/${name}`);
      br.pendingLoad = Promise.resolve();
    }
    for (let attempt = 1; ; attempt++) {
      try { await SHOTS[name](); break; } catch (e) {
        if (!/^RETRY/.test(e.message) || attempt >= 3) throw e;
        log("  retrying", name, "after:", e.message);
        await br.goto(`${SITE}/${name === "try" ? "" : name}`);
        br.pendingLoad = Promise.resolve();
      }
    }
  }
} finally {
  br.close();
}
log("done");
