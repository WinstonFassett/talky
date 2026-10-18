import { chromium } from "playwright";
import { writeFileSync, mkdirSync } from "fs";
import { join } from "path";
import { randomUUID } from "crypto";
const TALKY = process.env.TALKY_HOME;
const base = "http://127.0.0.1:8785";
const id = randomUUID();
const dir = join(TALKY, "convos", id);
mkdirSync(dir, { recursive: true });
const at = Date.now() / 1000;
const list = Array.from({ length: 40 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: i % 2 ? `Answer number ${i}. This reply is long enough to wrap onto a second line in the transcript. This reply is long enough to wrap onto a second line in the transcript. ` : `Question number ${i}?` }));
writeFileSync(join(dir, "transcript.jsonl"), list.map((t, i) => JSON.stringify({ ...t, timestamp: at - (list.length - i) * 5 })).join("\n") + "\n");
writeFileSync(join(dir, "convo.json"), JSON.stringify({ id, backend: "script", profile: "script", started_at: at - list.length * 5, last_active_at: at, title: "Seeded " + id.slice(0, 8), native_session_id: null, pinned: false, archived: false }));
await fetch(base + "/api/profiles/switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile: "script" }) });
await fetch(base + "/api/convos/new", { method: "POST" });
const b = await chromium.launch();
const p = await (await b.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
await p.goto(base + "/", { waitUntil: "load" });
await p.waitForSelector('[data-testid="session-sidebar"], [data-testid="sessions-toggle"]', { timeout: 15000 });
await p.waitForTimeout(300);
await p.click(`[data-testid="session-row"][data-convo-id="${id}"]`, { timeout: 10000 });
await p.getByRole("button", { name: /^(Start call|Converse)$/ }).first().click();
await p.waitForFunction(() => /IDLE|LISTENING|THINKING|SPEAKING/.test(document.querySelector('[aria-label^="Voice status"]')?.getAttribute("aria-label") ?? ""), null, { timeout: 40000 });
await p.click('[data-testid="composer"]');
await p.keyboard.insertText("[[sleep 3]] [[say Here is the newest reply.]]");
await p.keyboard.press("Enter");
await p.hover('[data-testid="transcript-list"]');
await p.mouse.wheel(0, -900);
await p.waitForSelector('[data-testid="jump-to-latest"]', { timeout: 3000 });
for (let i = 0; i < 6; i++) {
  await p.waitForTimeout(1000);
  const s = await p.evaluate(() => ({
    msgs: document.querySelectorAll('[data-testid="transcript-message"]').length,
    unread: document.querySelector('[data-testid="jump-to-latest"]')?.getAttribute("data-unread"),
    lastRole: [...document.querySelectorAll('[data-testid="transcript-message"]')].at(-1)?.innerText.slice(0, 60),
    hasReply: document.querySelector('[data-testid="transcript-list"]')?.innerText.includes("newest reply"),
  }));
  console.log(i, JSON.stringify(s));
  if (s.hasReply && i > 1) break;
}
await b.close();
