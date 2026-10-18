import { chromium } from "playwright";
const base = "http://127.0.0.1:8785";
await fetch(base + "/api/profiles/switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile: "script" }) });
await fetch(base + "/api/convos/new", { method: "POST" });
const b = await chromium.launch();
const p = await (await b.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
p.on("console", (m) => { if (m.type() === "error") console.log("[cerr]", m.text().slice(0, 250)); });
p.on("pageerror", (e) => console.log("[pge]", String(e).slice(0, 400)));
await p.goto(base + "/", { waitUntil: "load" });
await p.waitForSelector('[data-testid="composer"]');
await p.getByRole("button", { name: /^(Start call|Converse)$/ }).first().click();
await p.waitForFunction(() => /IDLE|LISTENING|THINKING|SPEAKING/.test(document.querySelector('[aria-label^="Voice status"]')?.getAttribute("aria-label") ?? ""), null, { timeout: 40000 });
await p.click('[data-testid="composer"]');
await p.keyboard.insertText("[[code]]");
await p.keyboard.press("Enter");
await p.waitForFunction(() => document.querySelector('[data-testid="transcript-list"]')?.innerText.includes("Call it with"), null, { timeout: 30000 });
await p.waitForTimeout(3000);
console.log(await p.evaluate(() => {
  const msg = [...document.querySelectorAll('[data-testid="transcript-message"]')].at(-1);
  const pre = msg?.querySelector("pre");
  return {
    preExists: !!pre,
    preHtml: pre?.outerHTML.slice(0, 1200),
    spansWithColor: [...(pre?.querySelectorAll("span") ?? [])].filter(s => (s.getAttribute("style")||"").includes("color")).length,
    anyColored: [...msg.querySelectorAll("*")].filter(e => (e.getAttribute("style")||"").includes("color")).map(e => e.tagName + ":" + e.getAttribute("style")).slice(0, 8),
  };
}));
await b.close();
