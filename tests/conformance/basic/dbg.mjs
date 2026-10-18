// Debug one scenario: node dbg.mjs "<Scenario title>" [shot]
import { chromium } from "playwright";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(fileURLToPath(import.meta.url));

// Same fake-media setup as run.mjs (headless Chromium has no mic).
function fakeMediaArgs() {
	const silence = join(ROOT, "reports", "silence.wav");
	mkdirSync(dirname(silence), { recursive: true });
	const pcm = Buffer.alloc(16000 * 5 * 2, 0);
	const h = Buffer.alloc(44);
	h.write("RIFF", 0);
	h.writeUInt32LE(36 + pcm.length, 4);
	h.write("WAVE", 8);
	h.write("fmt ", 12);
	h.writeUInt32LE(16, 16);
	h.writeUInt16LE(1, 20);
	h.writeUInt16LE(1, 22);
	h.writeUInt32LE(16000, 24);
	h.writeUInt32LE(32000, 28);
	h.writeUInt16LE(2, 32);
	h.writeUInt16LE(16, 34);
	h.write("data", 36);
	h.writeUInt32LE(pcm.length, 40);
	writeFileSync(silence, Buffer.concat([h, pcm]));
	return [
		"--use-fake-device-for-media-stream",
		"--use-fake-ui-for-media-stream",
		`--use-file-for-fake-audio-capture=${silence}`,
	];
}

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
const browser = await chromium.launch({ channel: "chrome", headless: true, args: fakeMediaArgs() });
const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, permissions: ["clipboard-read", "clipboard-write", "microphone"] });
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
	console.log("voice:", await page.locator('[aria-label^="Voice status"]').getAttribute("aria-label").catch(() => "n/a"), "| stop:", await page.locator('[data-testid="composer-stop"]').count(), "| composer:", JSON.stringify(await page.inputValue('[data-testid="composer"]').catch(() => "n/a")), "| conn:", JSON.stringify(await page.locator('[data-testid="connection-status"]').innerText().catch(() => "none")));
	console.log("url:", page.url());
}
await browser.close();
