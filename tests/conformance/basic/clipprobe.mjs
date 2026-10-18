import { chromium } from "playwright";
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1280, height: 800 }, permissions: ["clipboard-read", "clipboard-write"] });
const p = await ctx.newPage();
await p.goto("http://127.0.0.1:8785/", { waitUntil: "load" });
await p.waitForSelector('[data-testid="composer"]');
console.log("perm:", await p.evaluate(async () => {
  const q = await navigator.permissions.query({ name: "clipboard-write" });
  let res = "";
  try { await navigator.clipboard.writeText("x"); res = "ok"; } catch (e) { res = String(e); }
  return { state: q.state, write: res };
}));
await b.close();
