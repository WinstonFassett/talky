// Debug one scenario: node dbg.mjs "<Scenario title>" [shot]
import { chromium } from "playwright";

const title = process.argv[2];
const adapter = (await import("./adapters/talky.mjs")).default;
const check = adapter.checks[title];
if (!check) {
	console.error("no check for", title, "\navailable:\n" + Object.keys(adapter.checks).join("\n"));
	process.exit(2);
}
if (typeof check === "string") {
	console.log("string result:", check);
	process.exit(0);
}
const BASE = process.env.BASE;
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, permissions: ["clipboard-read", "clipboard-write"] });
const page = await context.newPage();
page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") console.log(`[${m.type()}]`, m.text().slice(0, 300)); });
page.on("pageerror", (e) => console.log("[pageerror]", String(e).slice(0, 400)));
try {
	const note = await check({ page, base: BASE, adapter });
	console.log("PASS", note ?? "");
} catch (e) {
	console.log("FAIL", String(e?.message ?? e).split("\n")[0]);
	await page.screenshot({ path: `dbg-${title.replace(/[^\w]+/g, "-")}.png` }).catch(() => {});
	console.log("transcript:", JSON.stringify(await page.evaluate(() => document.querySelector('[data-testid="transcript-list"]')?.innerText.slice(0, 400) ?? null)));
	console.log("msgs:", await page.locator('[data-testid="transcript-message"]').count(), "| loading:", await page.locator('[data-testid="transcript-loading"]').count(), "| empty:", await page.locator('[data-testid="empty-state"]').count(), "| alert:", JSON.stringify(await page.evaluate(() => [...document.querySelectorAll("[role=alert]")].map((e) => e.innerText).join(" | ").slice(0, 200))));
	console.log("url:", page.url());
}
await browser.close();
