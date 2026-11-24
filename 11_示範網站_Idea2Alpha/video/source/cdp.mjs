// Minimal Chrome DevTools Protocol driver for recording the Idea2Alpha walkthrough.
// Launches the installed Chrome headless with its own temporary profile (the user's profile is untouched).
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";

export class Browser {
  constructor(root) { this.root = root; this.id = 0; this.pending = new Map(); this.handlers = new Map(); this.cur = { x: 900, y: 620 }; }

  async launch(port = 9333) {
    const prof = path.join(this.root, "chrome-profile");
    fs.rmSync(prof, { recursive: true, force: true });
    this.proc = spawn(CHROME, [
      "--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${prof}`, "--no-first-run", "--no-default-browser-check",
      "--hide-scrollbars", "--mute-audio", "--lang=en-US", "--window-size=1440,810", "--force-device-scale-factor=1.3333333", "--disable-background-timer-throttling",
      "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows", "about:blank",
    ], { stdio: "ignore" });
    let list = null;
    for (let i = 0; i < 80 && !list; i++) {
      await sleep(250);
      try { list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); } catch (e) { list = null; }
    }
    const page = list.find((t) => t.type === "page");
    this.ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((res, rej) => { this.ws.onopen = res; this.ws.onerror = rej; });
    this.ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) { const { res, rej } = this.pending.get(m.id); this.pending.delete(m.id); m.error ? rej(new Error(m.error.message)) : res(m.result); }
      else if (m.method && this.handlers.has(m.method)) for (const fn of this.handlers.get(m.method)) fn(m.params);
    };
    await this.send("Page.enable");
    await this.send("Runtime.enable");
    await this.send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 810, deviceScaleFactor: 0, mobile: false });
    await this.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }, { name: "prefers-reduced-motion", value: "no-preference" }] });
  }

  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((res, rej) => this.pending.set(id, { res, rej }));
  }
  on(method, fn) { if (!this.handlers.has(method)) this.handlers.set(method, new Set()); this.handlers.get(method).add(fn); }
  off(method, fn) { if (this.handlers.has(method)) this.handlers.get(method).delete(fn); }

  async eval(expr) {
    const r = await this.send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error("page error: " + (r.exceptionDetails.exception ? r.exceptionDetails.exception.description : r.exceptionDetails.text));
    return r.result.value;
  }

  async goto(url, ready = "true") {
    const loaded = new Promise((res) => { const fn = () => { this.off("Page.loadEventFired", fn); res(); }; this.on("Page.loadEventFired", fn); });
    await this.send("Page.navigate", { url });
    await loaded;
    await this.waitFor("document.fonts.ready.then(() => true)");
    await this.waitFor(ready);
    await this.injectCursor();
  }

  // Call before clicking a link, so a fast page load cannot be missed.
  expectLoad() {
    this.pendingLoad = new Promise((res) => { const fn = () => { this.off("Page.loadEventFired", fn); res(); }; this.on("Page.loadEventFired", fn); });
    return this.pendingLoad;
  }

  // Waits for a page condition after a navigation the page started itself (a clicked link).
  async waitNavigation(ready = "true") {
    await (this.pendingLoad || this.expectLoad());
    this.pendingLoad = null;
    await this.waitFor("document.fonts.ready.then(() => true)");
    await this.waitFor(ready);
    await this.injectCursor();
  }

  async waitFor(expr, timeout = 90000, every = 100) {
    const t0 = Date.now();
    for (;;) {
      let v = false;
      try { v = await this.eval(expr); } catch (e) { v = false; }
      if (v) return v;
      if (Date.now() - t0 > timeout) throw new Error("timed out waiting for: " + expr);
      await sleep(every);
    }
  }

  async injectCursor() {
    await this.eval(`(() => {
      if (document.getElementById("__cur")) return true;
      const st = document.createElement("style");
      st.textContent = "#__cur{position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none;will-change:transform;filter:drop-shadow(0 2px 3px rgba(20,33,61,.35))}" +
        "#__rip{position:fixed;left:0;top:0;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;border:3px solid #2448C9;opacity:0;z-index:2147483646;pointer-events:none}" +
        "#__rip.go{animation:__rip .45s ease-out}@keyframes __rip{from{opacity:.9;transform:scale(.35)}to{opacity:0;transform:scale(1.25)}}" +
        "html{scroll-behavior:auto!important}";
      document.head.appendChild(st);
      const c = document.createElement("div");
      c.id = "__cur";
      c.innerHTML = '<svg width="30" height="30" viewBox="0 0 30 30"><path d="M5 3 L5 24 L10.6 18.6 L14.4 27 L18.3 25.3 L14.6 17.2 L22.4 17.2 Z" fill="#fff" stroke="#14213D" stroke-width="1.8" stroke-linejoin="round"/></svg>';
      document.documentElement.appendChild(c);
      const r = document.createElement("div"); r.id = "__rip"; document.documentElement.appendChild(r);
      window.__setCur = (x, y, ms) => { c.style.transition = ms ? "transform " + ms + "ms cubic-bezier(.33,.8,.25,1)" : "none"; c.style.transform = "translate(" + (x - 5) + "px," + (y - 3) + "px)"; };
      window.__ripple = (x, y) => { r.classList.remove("go"); r.style.transform = ""; r.style.left = x + "px"; r.style.top = y + "px"; void r.offsetWidth; r.classList.add("go"); };
      window.__scrollTo = (y, ms) => new Promise((res) => {
        const y0 = scrollY, max = document.documentElement.scrollHeight - innerHeight, y1 = Math.max(0, Math.min(max, y)), t0 = performance.now();
        const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
        if (!ms || Math.abs(y1 - y0) < 1) { scrollTo({ top: y1, behavior: "instant" }); return res(y1); }
        const step = (now) => { const k = Math.min(1, (now - t0) / ms); scrollTo({ top: y0 + (y1 - y0) * ease(k), behavior: "instant" }); k < 1 ? requestAnimationFrame(step) : res(y1); };
        requestAnimationFrame(step);
      });
      return true;
    })()`);
    await this.eval(`__setCur(${this.cur.x}, ${this.cur.y}, 0)`);
  }

  async rect(sel) {
    const r = await this.eval(`(() => { const e = ${sel.startsWith("js:") ? sel.slice(3) : `document.querySelector(${JSON.stringify(sel)})`}; if (!e) return null; const b = e.getBoundingClientRect(); return { x: b.left, y: b.top, w: b.width, h: b.height }; })()`);
    if (!r) throw new Error("no element: " + sel);
    return r;
  }

  async moveTo(sel, ms = 650, fx = 0.5, fy = 0.5) {
    let r = typeof sel === "object" ? sel : await this.rect(sel);
    // Scroll the target into view first, the way a person would, so a pointer never lands off-screen.
    const py = r.y + r.h * fy;
    if (typeof sel === "string" && (py < 70 || py > 770)) {
      const y = await this.eval("scrollY");
      await this.scrollTo(y + py - 430, 650);
      r = await this.rect(sel);
    }
    const x = Math.round(r.x + r.w * fx), y = Math.round(r.y + r.h * fy);
    await this.eval(`__setCur(${x}, ${y}, ${ms})`);
    const from = { ...this.cur }, steps = Math.max(2, Math.round(ms / 80));
    for (let i = 1; i <= steps; i++) {
      await sleep(ms / steps);
      await this.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: from.x + (x - from.x) * i / steps, y: from.y + (y - from.y) * i / steps });
    }
    this.cur = { x, y };
  }

  async click(sel, ms = 650, fx = 0.5, fy = 0.5) {
    await this.moveTo(sel, ms, fx, fy);
    await sleep(110);
    await this.eval(`__ripple(${this.cur.x}, ${this.cur.y})`);
    await this.send("Input.dispatchMouseEvent", { type: "mousePressed", x: this.cur.x, y: this.cur.y, button: "left", clickCount: 1 });
    await sleep(70);
    await this.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: this.cur.x, y: this.cur.y, button: "left", clickCount: 1 });
  }

  async type(text, cps = 7) { for (const ch of text) { await this.send("Input.insertText", { text: ch }); await sleep(1000 / cps); } }
  async scrollTo(y, ms) { return this.eval(`__scrollTo(${y}, ${ms})`); }
  async scrollToEl(sel, offset = 90, ms = 900) {
    const y = await this.eval(`(() => { const e = ${sel.startsWith("js:") ? sel.slice(3) : `document.querySelector(${JSON.stringify(sel)})`}; return e.getBoundingClientRect().top + scrollY - ${offset}; })()`);
    return this.scrollTo(y, ms);
  }
  async screenshot(file) {
    const r = await this.send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(file, Buffer.from(r.data, "base64"));
  }
  close() { try { this.ws.close(); } catch (e) { /* closed */ } try { this.proc.kill(); } catch (e) { /* gone */ } }
}

// Records screencast frames (JPEG) with their timestamps, plus named markers on the same clock.
export class Recorder {
  constructor(br, dir) {
    this.br = br; this.dir = dir; this.frames = []; this.markers = []; this.n = 0;
    fs.rmSync(dir, { recursive: true, force: true });
    fs.mkdirSync(path.join(dir, "frames"), { recursive: true });
    this.writes = [];
    // Acknowledge first so Chrome keeps sending frames, then write the file in the background.
    this.onFrame = (p) => {
      this.br.send("Page.screencastFrameAck", { sessionId: p.sessionId }).catch(() => {});
      const f = `f${String(this.n++).padStart(5, "0")}.jpg`;
      this.frames.push({ f, ts: p.metadata.timestamp });
      this.writes.push(fs.promises.writeFile(path.join(this.dir, "frames", f), Buffer.from(p.data, "base64")));
    };
  }
  async start() {
    this.br.on("Page.screencastFrame", this.onFrame);
    await this.br.send("Page.startScreencast", { format: "jpeg", quality: 84, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });
    this.mark("start");
  }
  async restart() {
    try { await this.br.send("Page.stopScreencast"); } catch (e) { /* not running */ }
    await this.br.send("Page.startScreencast", { format: "jpeg", quality: 84, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });
  }
  mark(name) { this.markers.push({ name, t: Date.now() / 1000 }); }
  async stop() {
    this.mark("end");
    await sleep(300);
    try { await this.br.send("Page.stopScreencast"); } catch (e) { /* stopped */ }
    this.br.off("Page.screencastFrame", this.onFrame);
    await Promise.all(this.writes);
    fs.writeFileSync(path.join(this.dir, "shot.json"), JSON.stringify({ frames: this.frames, markers: this.markers }, null, 1));
    return { frames: this.frames.length, markers: this.markers.length };
  }
}
