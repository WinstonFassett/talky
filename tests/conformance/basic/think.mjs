import { chromium } from "playwright";
const b = await chromium.launch();
const p = await (await b.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
const base = "http://127.0.0.1:8785";
await fetch(base + "/api/profiles/switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: '{"profile":"script"}' });
await fetch(base + "/api/convos/new", { method: "POST" });
await p.goto(base + "/", { waitUntil: "load" });
await p.waitForSelector('[data-testid="composer"]');
await p.getByRole("button", { name: /^(Start call|Converse)$/ }).first().click();
await p.waitForFunction(() => /IDLE|LISTENING|THINKING|SPEAKING/.test(document.querySelector('[aria-label^="Voice status"]')?.getAttribute("aria-label") ?? ""), null, { timeout: 40000 });
await p.click('[data-testid="composer"]');
await p.keyboard.insertText("[[think weighing the options carefully]] [[sleep 3]] [[say the answer.]]");
await p.keyboard.press("Enter");
await p.waitForTimeout(2500);
console.log(await p.evaluate(() => {
  const btns = [...document.querySelectorAll("button")].filter((x) => /Thinking|Thought/.test(x.textContent));
  return btns.map((x) => ({ text: x.textContent.trim().slice(0, 40), expanded: x.getAttribute("aria-expanded"), outer: x.outerHTML.slice(0, 220) }));
}));
await b.close();
