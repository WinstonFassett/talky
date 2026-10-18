import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const MISSING = "MISSING:";
const SKIPPED = "SKIPPED:";

const ROOT = dirname(fileURLToPath(import.meta.url));

// Headless Chromium has no mic: the same fake-media setup the browser rigs
// use (tests/browser/drive_text.py) — a fake device + a 5s silence WAV as
// the audio capture, and the microphone permission granted. Without it the
// WebRTC connect hangs or errors intermittently (WIN-536).
function fakeMediaArgs() {
	const silence = join(ROOT, "reports", "silence.wav");
	mkdirSync(dirname(silence), { recursive: true });
	const pcm = Buffer.alloc(16000 * 5 * 2, 0); // 5s of 16kHz mono silence
	const h = Buffer.alloc(44);
	h.write("RIFF", 0);
	h.writeUInt32LE(36 + pcm.length, 4);
	h.write("WAVE", 8);
	h.write("fmt ", 12);
	h.writeUInt32LE(16, 16); // fmt chunk size
	h.writeUInt16LE(1, 20); // PCM
	h.writeUInt16LE(1, 22); // mono
	h.writeUInt32LE(16000, 24); // sample rate
	h.writeUInt32LE(32000, 28); // byte rate
	h.writeUInt16LE(2, 32); // block align
	h.writeUInt16LE(16, 34); // bits per sample
	h.write("data", 36);
	h.writeUInt32LE(pcm.length, 40);
	writeFileSync(silence, Buffer.concat([h, pcm]));
	return [
		"--use-fake-device-for-media-stream",
		"--use-fake-ui-for-media-stream",
		`--use-file-for-fake-audio-capture=${silence}`,
	];
}
const args = process.argv.slice(2);
const floorOnly = args.includes("--floor");
const appName = args.find((arg) => arg.startsWith("--app="))?.slice(6) ?? "skin";
const ruleFilter = args.find((arg) => arg.startsWith("--rule="))?.slice(7).toLowerCase();
const BASE = (process.env.BASE ?? "").replace(/\/$/, "");

if (BASE === "") {
	console.error("Set BASE to the app URL, for example BASE=https://your.app");
	process.exit(2);
}

const adapter = (await import(join(ROOT, "adapters", `${appName}.mjs`))).default;
const feature = readFileSync(join(ROOT, "contract", "chat.feature"), "utf8");

const ruleOf = (offset) => [...feature.slice(0, offset).matchAll(/^\s*Rule: (.+)$/gm)].at(-1)?.[1].trim() ?? "";

const scenarios = [...feature.matchAll(/^\s*((?:@[\w.]+\s+)+)Scenario: (.+)$/gm)].map((match) => ({
	tags: match[1].trim().split(/\s+/),
	title: match[2].trim(),
	rule: ruleOf(match.index),
}));

const selected = scenarios.filter((scenario) => (!floorOnly || scenario.tags.includes("@must")) && (ruleFilter === undefined || scenario.rule.toLowerCase().includes(ruleFilter)));

const browser = await chromium.launch({ channel: "chrome", headless: true, args: fakeMediaArgs() });
mkdirSync(join(ROOT, "reports", "shots"), { recursive: true });

const results = [];

for (const scenario of selected) {
	const check = adapter.checks[scenario.title];
	const started = Date.now();

	if (check === undefined) {
		results.push({ ...scenario, status: "untested", reason: "no check in the adapter yet", ms: 0 });
		continue;
	}

	if (typeof check === "string") {
		results.push({ ...scenario, status: check.startsWith(MISSING) ? "fail" : "n/a", reason: check.startsWith(MISSING) ? check.slice(MISSING.length).trim() : check, ms: 0 });
		console.log(`${results.at(-1).status.padEnd(8)} ${scenario.title}`);
		continue;
	}

	// A fresh context per check: the shared browser keeps cold starts cheap,
	// but a 30-check run sharing one context accumulates half-closed SSE /
	// WebRTC connections and the browser's per-host pool stalls new page
	// loads (WIN-536). No check depends on localStorage surviving across
	// checks — openFresh clears drafts and the remembered convo itself.
	const context = await browser.newContext({ extraHTTPHeaders: adapter.headers?.() ?? {}, viewport: { width: 1280, height: 800 }, permissions: ["clipboard-read", "clipboard-write", "microphone"] });
	const page = await context.newPage();

	try {
		const note = await check({ page, base: BASE, adapter });

		const skipped = typeof note === "string" && note.startsWith(SKIPPED);

		results.push({ ...scenario, status: skipped ? "n/a" : "pass", reason: skipped ? note.slice(SKIPPED.length).trim() : (note ?? ""), ms: Date.now() - started });
	} catch (error) {
		results.push({ ...scenario, status: "fail", reason: String(error?.message ?? error).split("\n")[0], ms: Date.now() - started });
		await page.screenshot({ path: join(ROOT, "reports", "shots", `${appName}-${scenario.title.replace(/[^\w]+/g, "-").toLowerCase()}.png`) }).catch(() => {});
	} finally {
		await context.setOffline(false);
		await page.close();
		await context.close();
	}

	console.log(`${results.at(-1).status.padEnd(8)} ${scenario.title}`);
}

await browser.close();

const count = (status) => results.filter((result) => result.status === status).length;
const date = new Date().toISOString().slice(0, 10);

const report = [
	`# ${appName} conformance report`,
	"",
	`Run against the deployed app on ${date}${floorOnly ? ", floor only" : ""}.`,
	"",
	`pass ${count("pass")} · fail ${count("fail")} · n/a ${count("n/a")} · untested ${count("untested")}`,
	"",
	"| Scenario | Tags | Result | Detail |",
	"| --- | --- | --- | --- |",
	...results.map((result) => `| ${result.title} | ${result.tags.join(" ")} | ${result.status} | ${result.reason.replaceAll("|", "\\|")} |`),
	"",
].join("\n");

mkdirSync(join(ROOT, "reports"), { recursive: true });
if (ruleFilter === undefined) writeFileSync(join(ROOT, "reports", `${appName}.md`), report);
else console.log(`\n--rule run: reports/${appName}.md left unchanged`);
console.log(`\npass ${count("pass")} · fail ${count("fail")} · n/a ${count("n/a")} · untested ${count("untested")}\nreport: reports/${appName}.md`);

process.exit(count("fail") + count("untested") === 0 ? 0 : 1);
