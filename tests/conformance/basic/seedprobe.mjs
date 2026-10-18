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
const list = [
  { role: "user", content: "copy test" },
  { role: "assistant", content: "Here is **bold** and a [link](https://example.com)." },
];
writeFileSync(join(dir, "transcript.jsonl"), list.map((t, i) => JSON.stringify({ role: t.role, content: t.content, timestamp: at - (list.length - i) * 5 })).join("\n") + "\n");
writeFileSync(join(dir, "convo.json"), JSON.stringify({ id, backend: "script", profile: "script", started_at: at - list.length * 5, last_active_at: at, title: "Seeded " + id.slice(0, 8), native_session_id: null, pinned: false, archived: false }));
// replicate openSeeded: fresh() + open + click
await fetch(base + "/api/profiles/switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: '{"profile":"script"}' });
await fetch(base + "/api/convos/new", { method: "POST" });
const b = await chromium.launch();
const p = await (await b.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
p.on("console", (m) => { if (m.type() === "error") console.log("[cerr]", m.text().slice(0, 250)); });
p.on("pageerror", (e) => console.log("[pge]", String(e).slice(0, 400)));
await p.goto(base + "/", { waitUntil: "load" });
await p.waitForSelector('[data-testid="session-sidebar"], [data-testid="sessions-toggle"]', { timeout: 15000 });
await p.waitForTimeout(300);
await p.click(`[data-testid="session-row"][data-convo-id="${id}"]`, { timeout: 10000 });
for (const t of [1000, 3000, 6000]) {
  await p.waitForTimeout(t === 1000 ? 1000 : t - (t === 3000 ? 1000 : 3000));
  console.log(`t=${t} msgs:`, await p.locator('[data-testid="transcript-message"]').count(), "| url:", p.url().slice(30), "| transcript:", JSON.stringify(await p.evaluate(() => document.querySelector('[data-testid="transcript-list"]')?.innerText.slice(0, 200) ?? "NO-LIST")));
}
await b.close();
