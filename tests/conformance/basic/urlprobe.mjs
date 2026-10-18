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
const list = Array.from({ length: 30 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: i % 2 ? `Answer number ${i}. This reply is long enough to wrap onto a second line in the transcript. This reply is long enough to wrap onto a second line in the transcript. ` : `Question number ${i}?` }));
writeFileSync(join(dir, "transcript.jsonl"), list.map((t, i) => JSON.stringify({ ...t, timestamp: at - (list.length - i) * 5 })).join("\n") + "\n");
writeFileSync(join(dir, "convo.json"), JSON.stringify({ id, backend: "script", profile: "script", started_at: at - list.length * 5, last_active_at: at, title: "Seeded " + id.slice(0, 8), native_session_id: null, pinned: false, archived: false }));
await fetch(base + "/api/profiles/switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile: "script" }) });
await fetch(base + "/api/convos/new", { method: "POST" });
const b = await chromium.launch();
const p = await (await b.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
p.on("console", (m) => { if (m.type() === "error") console.log("[cerr]", m.text().slice(0, 250)); });
p.on("pageerror", (e) => console.log("[pge]", String(e).slice(0, 400)));
await p.addInitScript(() => {
  globalThis.__basicFlash = { empty: 0, loading: 0 };
  new MutationObserver(() => {
    if (document.querySelector('[data-testid="empty-state"]')) globalThis.__basicFlash.empty += 1;
    if (document.querySelector('[data-testid="transcript-loading"]')) globalThis.__basicFlash.loading += 1;
  }).observe(document, { childList: true, subtree: true });
});
await p.goto(`${base}/?session=${id}`, { waitUntil: "load" });
for (const t of [1500, 4000]) {
  await p.waitForTimeout(t === 1500 ? 1500 : 2500);
  console.log(`t=${t}`, "msgs:", await p.locator('[data-testid="transcript-message"]').count(),
    "| loading:", await p.locator('[data-testid="transcript-loading"]').count(),
    "| empty:", await p.locator('[data-testid="empty-state"]').count(),
    "| transcript:", JSON.stringify(await p.evaluate(() => document.querySelector('[data-testid="transcript-list"]')?.innerText.slice(0, 160) ?? "NONE")),
    "| alert:", JSON.stringify(await p.evaluate(() => [...document.querySelectorAll("[role=alert]")].map(e => e.innerText).join(" | "))));
}
await b.close();
