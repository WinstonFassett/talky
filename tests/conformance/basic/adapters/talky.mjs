// Talky adapter for the basic conformance suite (WIN-536).
//
// Drives the real client served by an isolated Talky daemon. Turns go through
// the real pipeline (WebRTC → script backend → kokoro TTS → bot output), not
// through injected DOM. The `script` profile plays directives written in the
// typed message — see src/talky/backends/script.py.
//
// Hooks are talky's own data-testids and accessible names, never voice-ui-kit
// internals, so the checks survive the kit rip-out.
//
// Env:
//   TALKY_HOME        the daemon's ~/.talky (seeded conversations are written here)
//   TALKY_DAEMON_CTL  a command taking stop | start, for the restart scenarios
//                     (tests/conformance/basic/talky-daemon.sh)

import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const T = (id) => `[data-testid="${id}"]`;

const sel = {
	list: T("transcript-list"),
	message: T("transcript-message"),
	// Falls back to the pre-WIN-536 single-line input so a baseline run against the old client is meaningful.
	composer: `${T("composer")}, main input[placeholder]`,
	send: T("composer-send"),
	stop: T("composer-stop"),
	hint: T("composer-hint"),
	jump: T("jump-to-latest"),
	thinking: T("thinking-indicator"),
	toolRow: T("tool-row"),
	taskTimer: T("task-timer"),
	notice: T("transcript-notice"),
	connection: T("connection-status"),
	retry: T("connection-retry"),
	rail: T("session-rail"),
	resize: T("session-rail-resize"),
	row: T("session-row"),
	group: T("session-group"),
	search: T("session-search"),
	empty: T("empty-state"),
	starter: T("starter"),
	loading: T("transcript-loading"),
	voice: '[aria-label^="Voice status"]',
};

const fail = (message) => {
	throw new Error(message);
};

const HOME = process.env.TALKY_HOME ?? "";
const CTL = process.env.TALKY_DAEMON_CTL ?? "";

// ─── daemon + seeded history ────────────────────────────────────────────

const api = async (base, path, body, method = "POST") => {
	const res = await fetch(base + path, {
		method: body === undefined && method === "POST" ? "POST" : method,
		headers: { "Content-Type": "application/json" },
		body: body === undefined ? undefined : JSON.stringify(body),
		signal: AbortSignal.timeout(15000),
	});
	return res.json().catch(() => ({}));
};

/** The next page opens on a brand-new chat, on the script profile. */
const fresh = async (base) => {
	await api(base, "/api/profiles/switch", { profile: "script" });
	await api(base, "/api/convos/new", {});
};

/** Write a saved conversation straight into the daemon's store. */
const seed = ({ title, turns = 40, daysAgo = 0, content } = {}) => {
	if (!HOME) fail("set TALKY_HOME to the daemon's .talky directory to seed conversations");
	const id = randomUUID();
	const dir = join(HOME, "convos", id);
	const at = Date.now() / 1000 - daysAgo * 86400;
	mkdirSync(dir, { recursive: true });
	const lines = [];
	const list = content ?? Array.from({ length: turns }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: i % 2 ? `Answer number ${i}. ${"This reply is long enough to wrap onto a second line in the transcript. ".repeat(2)}` : `Question number ${i}?` }));
	list.forEach((t, i) => lines.push(JSON.stringify({ role: t.role, content: t.content, timestamp: at - (list.length - i) * 5 })));
	writeFileSync(join(dir, "transcript.jsonl"), lines.join("\n") + "\n");
	writeFileSync(join(dir, "convo.json"), JSON.stringify({ id, backend: "script", profile: "script", started_at: at - list.length * 5, last_active_at: at, title: title ?? `Seeded ${id.slice(0, 8)}`, native_session_id: null, pinned: false, archived: false }));
	return { id, title: title ?? `Seeded ${id.slice(0, 8)}`, last: list.at(-1).content };
};

const daemon = (action) => {
	if (!CTL) fail("set TALKY_DAEMON_CTL to a command that takes stop|start");
	execFileSync(CTL, [action], { stdio: "ignore", timeout: 180000 });
};

// ─── page helpers ───────────────────────────────────────────────────────

/** Load the app and wait for its shell (the session list), not for any one feature. */
const open = async (page, base, path = "/") => {
	await page.goto(base + path, { waitUntil: "load" });
	await page.waitForSelector('[data-testid="session-sidebar"], [data-testid="sessions-toggle"]', { timeout: 15000 });
	await page.waitForTimeout(300);
};

/** Each check's tab starts without a new-chat draft left by an earlier check (one browser context is shared). */
const noLeftoverDraft = (page) =>
	page.addInitScript(() => {
		if (sessionStorage.getItem("basic.started")) return;
		sessionStorage.setItem("basic.started", "1");
		for (const key of Object.keys(localStorage)) if (key.startsWith("talky.draft")) localStorage.removeItem(key);
	});

// A "fresh chat" must forget what this browser had open, or the app reopens
// the last-opened convo instead of showing a blank chat (WIN-535's
// remembered-session feature). The real New-chat flow clears it client-side;
// openFresh does the same. Runs on every load, not just the first: later
// checks (openSeeded) re-set the key, and drafts must survive reloads.
const noRememberedConvo = (page) =>
	page.addInitScript(() => {
		localStorage.removeItem("talky.openConvo");
	});

const openFresh = async (page, base) => {
	await fresh(base);
	await noLeftoverDraft(page);
	await noRememberedConvo(page);
	await open(page, base);
};

/** Open a saved conversation from the session list, the way a user does. */
const openSeeded = async (page, base, convo) => {
	await fresh(base);
	await noLeftoverDraft(page);
	await open(page, base);
	await page.click(`${sel.row}[data-convo-id="${convo.id}"]`, { timeout: 10000 });
	// Match on plain words: the transcript renders markdown, so literal
	// `**` / backticks / link syntax never appear in innerText. Normalise both
	// sides the same way so "greet(name):" still lines up.
	const strip = /[*_`[\]()#>~|\\-]+/g;
	const plain = convo.last.replace(strip, " ").replace(/\s+/g, " ").trim().slice(0, 24).trimEnd();
	await page.waitForFunction(({ list, text, re }) => (document.querySelector(list)?.innerText ?? "").replace(new RegExp(re, "g"), " ").replace(/\s+/g, " ").includes(text), { list: sel.list, text: plain, re: strip.source }, { timeout: 10000 });
};

const isConnected = (voice) => /Voice status: (IDLE|LISTENING|THINKING|SPEAKING)/.test(document.querySelector(voice)?.getAttribute("aria-label") ?? "");

const connect = async (page) => {
	if (await page.evaluate(isConnected, sel.voice)) return;
	// Headless WebRTC occasionally leaves the client stuck "connecting" (the
	// offer never reaches the daemon); a fresh attempt disconnects and retries.
	for (let attempt = 1; attempt <= 3; attempt++) {
		try {
			const start = page.getByRole("button", { name: /^(Start call|Converse)$/ }).first();
			await start.click({ timeout: 5000 });
			const ok = await page
				.waitForFunction(isConnected, sel.voice, { timeout: 30000 })
				.then(() => true)
				.catch(() => false);
			if (ok) return;
		} catch {
			// click could not land (covered / mid-transition): retry after a beat
		}
		if (attempt < 3) await page.waitForTimeout(500);
	}
	fail("the call did not connect after three attempts");
};

const typeInto = async (page, text) => {
	await page.fill(sel.composer, "");
	await page.click(sel.composer);
	await page.keyboard.insertText(text);
};

const messageCount = (page) => page.$$eval(sel.message, (els) => els.length);

const send = async (page, text) => {
	const before = await messageCount(page);
	await typeInto(page, text);
	await page.press(sel.composer, "Enter");
	await page.waitForFunction(({ m, n }) => document.querySelectorAll(m).length > n, { m: sel.message, n: before }, { timeout: 15000 });
};

const lastText = (page) => page.$$eval(sel.message, (els) => els.at(-1)?.innerText ?? "");

/** The turn is over: no Stop button, voice idle, and nothing changed for `quiet` ms. */
const waitTurnDone = async (page, timeout = 90000, quiet = 1500) => {
	await page.waitForFunction(
		({ stop, list, voice, quiet }) => {
			const w = globalThis.__basicQuiet ?? (globalThis.__basicQuiet = { text: "", since: Date.now() });
			const text = document.querySelector(list)?.innerText ?? "";
			if (text !== w.text) Object.assign(w, { text, since: Date.now() });
			const idle = /IDLE|LISTENING/.test(document.querySelector(voice)?.getAttribute("aria-label") ?? "");
			return !document.querySelector(stop) && idle && Date.now() - w.since > quiet;
		},
		{ stop: sel.stop, list: sel.list, voice: sel.voice, quiet },
		{ timeout, polling: 200 },
	);
	await page.evaluate(() => delete globalThis.__basicQuiet);
};

const distanceFromBottom = (page) => page.$eval(sel.list, (el) => el.scrollHeight - el.clientHeight - el.scrollTop);
const scrollTop = (page) => page.$eval(sel.list, (el) => el.scrollTop);
const nextFrame = (page) => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

/** Record, on the frame after every transcript change, how far from the bottom it sits. */
const watchScroll = (page) =>
	page.evaluate((list) => {
		const el = document.querySelector(list);
		const log = (globalThis.__basicScroll = []);
		new MutationObserver(() => requestAnimationFrame(() => log.push({ top: el.scrollTop, distance: el.scrollHeight - el.clientHeight - el.scrollTop, at: performance.now() }))).observe(el, { childList: true, subtree: true, characterData: true });
	}, sel.list);
const scrollLog = (page) => page.evaluate(() => globalThis.__basicScroll ?? []);

const wheelUp = async (page, dy) => {
	await page.hover(sel.list);
	await page.mouse.wheel(0, -dy);
	await page.waitForTimeout(150);
};

const panelWidth = (page, selector) => page.$eval(selector, (el) => Math.round(el.getBoundingClientRect().width)).catch(() => 0);

const dragBy = async (page, selector, dx) => {
	const box = await page.locator(selector).boundingBox();
	if (box === null) fail(`${selector} is not on screen`);
	const x = box.x + box.width / 2;
	const y = box.y + Math.min(200, box.height / 2);
	await page.mouse.move(x, y);
	await page.mouse.down();
	await page.mouse.move(x + dx, y, { steps: 10 });
	await page.mouse.up();
	await page.waitForTimeout(100);
};

const phone = async (page, fn) => {
	// A phone is its own browser context (touch, 390px); it shares the runner's browser.
	const ctx = await page.context().browser().newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, permissions: ["clipboard-read", "clipboard-write", "microphone"] });
	const p = await ctx.newPage();
	try {
		return await fn(p);
	} finally {
		await ctx.close();
	}
};

const SIGN_IN_NA = "Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked as its own ticket.)";

// Text rectangles of every visible leaf text run, for the overlap check.
const textOverlaps = (page) =>
	page.evaluate(() => {
		const rects = [];
		// Text clipped by an overflowed ancestor is not on screen — intersect
		// each range with EVERY clip box up the tree so a row cut off by a
		// scroll viewport doesn't count as overlapping what it sits under.
		const clipBox = (el) => {
			let box = null;
			for (let a = el.parentElement; a; a = a.parentElement) {
				const o = getComputedStyle(a);
				if (/hidden|auto|scroll|clip/.test(o.overflow) || /hidden|auto|scroll|clip/.test(o.overflowX) || /hidden|auto|scroll|clip/.test(o.overflowY)) {
					const r = a.getBoundingClientRect();
					box = box === null
						? r
						: new DOMRect(Math.max(box.left, r.left), Math.max(box.top, r.top), Math.max(0, Math.min(box.right, r.right) - Math.max(box.left, r.left)), Math.max(0, Math.min(box.bottom, r.bottom) - Math.max(box.top, r.top)));
				}
			}
			return box;
		};
		const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
		while (walker.nextNode()) {
			const node = walker.currentNode;
			if (!node.textContent.trim()) continue;
			const el = node.parentElement;
			const cs = getComputedStyle(el);
			if (cs.visibility === "hidden" || cs.opacity === "0" || el.closest("[aria-hidden=true], .sr-only")) continue;
			const clip = clipBox(el);
			const range = document.createRange();
			range.selectNodeContents(node);
			for (const r of range.getClientRects()) {
				const rr = clip
					? new DOMRect(Math.max(r.left, clip.left), Math.max(r.top, clip.top), Math.max(0, Math.min(r.right, clip.right) - Math.max(r.left, clip.left)), Math.max(0, Math.min(r.bottom, clip.bottom) - Math.max(r.top, clip.top)))
					: r;
				if (rr.width > 1 && rr.height > 1 && rr.bottom > 0 && rr.top < innerHeight) rects.push({ r: rr, el, text: node.textContent.trim().slice(0, 30) });
			}
		}
		for (const input of document.querySelectorAll("input[placeholder], textarea[placeholder]")) {
			if (input.value || !input.placeholder || input.offsetParent === null) continue;
			const r = input.getBoundingClientRect();
			rects.push({ r: new DOMRect(r.x + 8, r.y + r.height / 2 - 6, Math.min(r.width - 16, input.placeholder.length * 6), 12), el: input, text: input.placeholder.slice(0, 30) });
		}
		// Things stacked over the page on purpose (popovers, the bot-speaking bar) are layers, not overlaps.
		const layer = (el) => el.closest('[role=dialog], [role=menu], [role=listbox], [data-radix-popper-content-wrapper], [role=tooltip]');
		const hits = [];
		for (let i = 0; i < rects.length; i++)
			for (let j = i + 1; j < rects.length; j++) {
				const a = rects[i];
				const b = rects[j];
				if (a.el === b.el || a.el.contains(b.el) || b.el.contains(a.el) || layer(a.el) !== layer(b.el)) continue;
				const w = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
				const h = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
				if (w > 2 && h > 2) hits.push(`"${a.text}" overlaps "${b.text}"`);
			}
		return hits;
	});

export default {
	checks: {
		// ─── 1. Scroll follows the reader, never the stream ─────────────────
		"Following the stream while at the bottom": async ({ page, base }) => {
			await openSeeded(page, base, seed());
			await connect(page);
			if ((await distanceFromBottom(page)) > 80) fail("the conversation did not open at the bottom");
			await send(page, "[[lines 10]]");
			await watchScroll(page);
			await waitTurnDone(page);
			const log = await scrollLog(page);
			if (log.length < 3) fail(`only ${log.length} transcript updates arrived`);
			const worst = Math.max(...log.map((l) => l.distance));
			if (worst > 2) fail(`a frame after new content sat ${worst}px from the bottom`);
			const behaviour = await page.$eval(sel.list, (el) => getComputedStyle(el).scrollBehavior);
			if (behaviour === "smooth") fail("the transcript scrolls with scroll-behavior: smooth");
			return `${log.length} updates, every one pinned on the next frame, scroll-behavior ${behaviour}`;
		},

		"Reading history while the agent streams": async ({ page, base }) => {
			await openSeeded(page, base, seed());
			await connect(page);
			await send(page, "[[sleep 2]] [[think reading]] [[tool grep 1]] [[lines 8]]");
			await wheelUp(page, 900);
			const before = await scrollTop(page);
			await watchScroll(page);
			await waitTurnDone(page);
			const log = await scrollLog(page);
			if (log.length < 3) fail(`only ${log.length} transcript updates arrived`);
			const moved = log.filter((l) => l.top !== before);
			if (moved.length) fail(`scrollTop moved from ${before} to ${moved[0].top} while reading`);
			return `${log.length} updates (thinking, tool rows, text), scrollTop held at ${before}`;
		},

		"A scroll-wheel up releases the pin at once": async ({ page, base }) => {
			await openSeeded(page, base, seed());
			await connect(page);
			await send(page, "[[sleep 1]] [[lines 12]]");
			await page.waitForFunction((m) => /Line 1 of/.test(document.querySelector(m.list)?.innerText ?? ""), { list: sel.list }, { timeout: 30000 });
			await page.hover(sel.list);
			await page.mouse.wheel(0, -300);
			await watchScroll(page);
			await page.waitForFunction(() => (globalThis.__basicScroll ?? []).length > 0, null, { timeout: 20000 });
			const [first] = await scrollLog(page);
			if (first.distance < 150) fail(`the next update pulled the reader back (${Math.round(first.distance)}px from bottom)`);
			return `after one wheel tick the next update left the view ${Math.round(first.distance)}px from the bottom`;
		},

		"Scrolling up during a real reply is never pulled back": async ({ page, base }) => {
			await openSeeded(page, base, seed());
			await connect(page);
			await send(page, "[[sleep 1]] [[lines 16]]");
			await page.waitForFunction((m) => /Line 1 of/.test(document.querySelector(m.list)?.innerText ?? ""), { list: sel.list }, { timeout: 30000 });
			await wheelUp(page, 600);
			const held = await scrollTop(page);
			await watchScroll(page);
			await page.waitForTimeout(3500);
			const during = await scrollLog(page);
			if (during.length === 0) fail("no update arrived while scrolled up");
			if (during.some((l) => l.top !== held)) fail(`a render moved the reader from ${held} to ${during.find((l) => l.top !== held).top}`);
			await page.$eval(sel.list, (el) => (el.scrollTop = el.scrollHeight - el.clientHeight - 40));
			await page.waitForTimeout(100);
			await watchScroll(page);
			await page.waitForFunction(() => (globalThis.__basicScroll ?? []).length > 0, null, { timeout: 20000 });
			const [resumed] = await scrollLog(page);
			if (resumed.distance > 2) fail(`back within 40px, the next update left the view ${Math.round(resumed.distance)}px from the bottom`);
			await waitTurnDone(page);
			return `${during.length} renders while scrolled up left scrollTop at ${held}; following resumed 40px from the bottom`;
		},

		"A way back to the newest message": async ({ page, base }) => {
			await openSeeded(page, base, seed());
			await connect(page);
			const before = await page.locator(sel.message).count();
			await send(page, "[[sleep 3]] [[say Here is the newest reply.]]");
			await wheelUp(page, 900);
			await page.waitForSelector(sel.jump, { timeout: 3000 });
			// The reply's own message (message N+2 — the user's turn at N+1 holds the same words in its [[say]] script).
			await page.waitForFunction(({ m, n }) => document.querySelectorAll(m).length > n + 1 && [...document.querySelectorAll(m)].at(-1).innerText.includes("Here is the newest reply"), { m: sel.message, n: before }, { timeout: 30000 });
			// The reply is out of view, so the count appears once the streamed
			// message is in the transcript (the unread effect and the reply can
			// land in the same frame).
			await page.waitForFunction((j) => Number(document.querySelector(j)?.getAttribute("data-unread") ?? 0) >= 1, sel.jump, { timeout: 5000 }).catch(() => fail("the button shows no unread after a reply arrived"));
			const unread = await page.$eval(sel.jump, (el) => Number(el.getAttribute("data-unread")));
			const label = await page.$eval(sel.jump, (el) => el.getAttribute("aria-label") ?? el.textContent);
			if (!/latest/i.test(label)) fail(`the button is labelled "${label}"`);
			const composerTop = await page.$eval(sel.composer, (el) => el.getBoundingClientRect().top);
			const jumpBottom = await page.$eval(sel.jump, (el) => el.getBoundingClientRect().bottom);
			if (jumpBottom > composerTop) fail("the button is not above the composer");
			await page.click(sel.jump);
			// The jump lands at the bottom; the bot-speaking bar can shrink the
			// transcript a beat later, so wait for the pinned state rather than a
			// fixed delay.
			await page.waitForFunction(({ list, j }) => {
				const el = document.querySelector(list);
				const dist = el.scrollHeight - el.clientHeight - el.scrollTop;
				return dist <= 2 && document.querySelector(j) === null;
			}, { list: sel.list, j: sel.jump }, { timeout: 5000 }).catch(async () => {
				const d = await distanceFromBottom(page);
				fail(`after the button the transcript is ${d}px from the bottom`);
			});
			await waitTurnDone(page);
			return `"${label}" showed ${unread} unread above the composer and jumped to the bottom`;
		},

		"Opening a conversation lands at the bottom": async ({ page, base }) => {
			const convo = seed({ turns: 80 });
			await fresh(base);
			await open(page, base);
			await page.evaluate(({ list, message }) => {
				const frames = (globalThis.__basicFrames = []);
				const tick = () => {
					const el = document.querySelector(list);
					if (el && el.querySelector(message)) frames.push(el.scrollHeight - el.clientHeight - el.scrollTop);
					if (frames.length < 120) requestAnimationFrame(tick);
				};
				requestAnimationFrame(tick);
			}, { list: sel.list, message: sel.message });
			await page.click(`${sel.row}[data-convo-id="${convo.id}"]`);
			await page.waitForFunction((list) => document.querySelector(list)?.innerText.includes("Answer number 79"), sel.list, { timeout: 10000 });
			await page.waitForTimeout(800);
			const frames = await page.evaluate(() => globalThis.__basicFrames);
			const opened = frames;
			if (!opened.length) fail("no frame with messages was painted");
			const bad = opened.filter((d) => d > 2);
			if (bad.length) fail(`${bad.length} painted frames were not at the bottom (worst ${Math.round(Math.max(...bad))}px)`);
			return `every painted frame with messages (${opened.length}) was at the bottom`;
		},

		"An existing conversation never flashes the empty state": async ({ page, base }) => {
			const convo = seed({ turns: 30 });
			await fresh(base);
			await page.addInitScript((empty) => {
				globalThis.__basicFlash = { empty: 0, loading: 0 };
				new MutationObserver(() => {
					if (document.querySelector(empty)) globalThis.__basicFlash.empty += 1;
					if (document.querySelector('[data-testid="transcript-loading"]')) globalThis.__basicFlash.loading += 1;
				}).observe(document, { childList: true, subtree: true });
			}, sel.empty);
			await page.goto(`${base}/?session=${convo.id}`, { waitUntil: "load" });
			const needle = convo.last.slice(0, 20);
			await page.waitForFunction(({ list, text }) => document.querySelector(list)?.innerText.includes(text), { list: sel.list, text: needle }, { timeout: 10000 }).catch(() => fail(`/?session=${convo.id} did not show that conversation`));
			const seen = await page.evaluate(() => globalThis.__basicFlash);
			if (seen.empty) fail(`the empty state appeared ${seen.empty} times while the conversation loaded`);
			if (!seen.loading) fail("no loading state was shown before the messages");
			return "the address showed a loading state, then the messages, and never the empty state";
		},

		"My own message always pins": async ({ page, base }) => {
			await openSeeded(page, base, seed());
			await connect(page);
			await wheelUp(page, 1200);
			if ((await distanceFromBottom(page)) < 500) fail("could not scroll up first");
			await send(page, "[[say pinned.]] my own message");
			await nextFrame(page);
			const distance = await distanceFromBottom(page);
			if (distance > 2) fail(`after sending, the transcript is ${distance}px from the bottom`);
			await waitTurnDone(page);
		},

		// ─── 2. The composer keeps what I type ──────────────────────────────
		"Enter sends, Shift+Enter breaks a line, on desktop": async ({ page, base }) => {
			await openFresh(page, base);
			await page.click(sel.composer);
			await page.keyboard.insertText("[[say ok.]] first line");
			await page.keyboard.press("Shift+Enter");
			await page.keyboard.insertText("second line");
			const value = await page.inputValue(sel.composer);
			if (!value.includes("first line\nsecond line")) fail(`Shift+Enter gave ${JSON.stringify(value)}`);
			if ((await messageCount(page)) !== 0) fail("Shift+Enter sent the message");
			await page.keyboard.press("Enter");
			await page.waitForFunction((m) => [...document.querySelectorAll(m)].some((e) => e.innerText.includes("second line")), sel.message, { timeout: 30000 });
			if ((await page.inputValue(sel.composer)) !== "") fail("the composer kept the text after Enter");
			await waitTurnDone(page);
			return "Shift+Enter inserted a newline; Enter sent both lines and cleared the composer";
		},

		"Enter during IME composition never sends": async ({ page, base }) => {
			await openFresh(page, base);
			await page.click(sel.composer);
			await page.keyboard.insertText("にほん");
			await page.$eval(sel.composer, (el) => {
				el.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true, data: "" }));
				el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 229, isComposing: true, bubbles: true, cancelable: true }));
				el.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true, data: "にほん" }));
			});
			await page.waitForTimeout(1500);
			if ((await messageCount(page)) !== 0) fail("Enter while composing sent the message");
			if ((await page.inputValue(sel.composer)) !== "にほん") fail("the composition text was lost");
			return "a composing Enter (isComposing, keyCode 229) left the text in place";
		},

		"On a phone, Enter is a newline": async ({ page: desktop, base }) =>
			phone(desktop, async (page) => {
				await fresh(base);
				await open(page, base);
				await page.tap(sel.composer);
				await page.keyboard.insertText("[[say ok.]] one");
				await page.keyboard.press("Enter");
				await page.keyboard.insertText("two");
				if (!(await page.inputValue(sel.composer)).includes("one\ntwo")) fail("Return did not insert a newline on a touch device");
				if ((await messageCount(page)) !== 0) fail("Return sent the message on a touch device");
				await page.tap(sel.send);
				await page.waitForFunction((m) => [...document.querySelectorAll(m)].some((e) => e.innerText.includes("two")), sel.message, { timeout: 30000 });
				return "Return inserted a newline; the Send button sent";
			}),

		"The shortcut is written down": async ({ page, base }) => {
			await openFresh(page, base);
			const hint = await page.$eval(sel.hint, (el) => el.innerText).catch(() => "");
			if (!/Enter to send/i.test(hint) || !/Shift\s*\+\s*Enter for (a )?new line/i.test(hint)) fail(`the composer hint reads "${hint}"`);
			return hint;
		},

		"The composer is focused and ready on load": async ({ page, base }) => {
			await openFresh(page, base);
			await page.waitForTimeout(300);
			const focused = await page.evaluate((c) => document.activeElement === document.querySelector(c), sel.composer);
			if (!focused) fail(`focus is on ${await page.evaluate(() => document.activeElement?.outerHTML.slice(0, 80))}`);
			if (await page.$eval(sel.composer, (el) => el.disabled)) fail("the composer is disabled");
			await page.keyboard.type("hello");
			if ((await page.inputValue(sel.composer)) !== "hello") fail("typing without a click did not reach the composer");
		},

		"The composer grows with the text, up to a limit": async ({ page, base }) => {
			await openFresh(page, base);
			const one = await page.$eval(sel.composer, (el) => el.getBoundingClientRect().height);
			await typeInto(page, "1\n2\n3");
			const three = await page.$eval(sel.composer, (el) => el.getBoundingClientRect().height);
			await typeInto(page, Array.from({ length: 30 }, (_, i) => `line ${i}`).join("\n"));
			const many = await page.$eval(sel.composer, (el) => ({ h: el.getBoundingClientRect().height, sh: el.scrollHeight, ch: el.clientHeight, oy: getComputedStyle(el).overflowY }));
			if (!(three > one)) fail(`three lines are ${three}px, one line ${one}px`);
			if (many.h < 140 || many.h > 180) fail(`thirty lines make it ${many.h}px tall`);
			if (!(many.sh > many.ch) || !/auto|scroll/.test(many.oy)) fail("past the limit it does not scroll inside itself");
			return `${Math.round(one)}px → ${Math.round(three)}px → capped at ${Math.round(many.h)}px, scrolling inside`;
		},

		"Send is disabled for empty input, and says why": async ({ page, base }) => {
			await openFresh(page, base);
			const state = await page.$eval(sel.send, (el) => ({ disabled: el.disabled || el.getAttribute("aria-disabled") === "true", label: el.getAttribute("aria-label") ?? "", title: el.getAttribute("title") ?? "" }));
			if (!state.disabled) fail("Send is enabled with nothing typed");
			if (!/Message requires text/.test(state.label + state.title)) fail(`Send is labelled "${state.label}" / "${state.title}"`);
			await typeInto(page, "x");
			if (await page.$eval(sel.send, (el) => el.disabled)) fail("Send stays disabled with text");
		},

		"A send that cannot go out keeps the draft": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			daemon("stop");
			try {
				await page.waitForFunction((c) => /reconnecting|offline/i.test(document.querySelector(c)?.innerText ?? ""), sel.connection, { timeout: 20000 });
				const before = await messageCount(page);
				await typeInto(page, "keep this draft please");
				await page.click(sel.send);
				await page.waitForTimeout(800);
				if ((await messageCount(page)) !== before) fail("the message was added to the transcript while the connection was down");
				if ((await page.inputValue(sel.composer)) !== "keep this draft please") fail("the draft left the composer");
				const told = await page.evaluate(() => document.body.innerText);
				if (!told.includes("Message not sent: reconnecting. Your draft is still in the composer")) fail("no 'Message not sent' notice");
			} finally {
				daemon("start");
			}
			return "with the daemon down, Send kept the text and said why";
		},

		"The draft survives a reload or a deploy": async ({ page, base }) => {
			await openFresh(page, base);
			await typeInto(page, "a draft I have not sent");
			await page.waitForTimeout(600);
			await page.reload({ waitUntil: "load" });
			await page.waitForSelector(sel.composer);
			if ((await page.inputValue(sel.composer)) !== "a draft I have not sent") fail("the draft did not come back after a reload");
			await page.evaluate(() => {
				for (const key of Object.keys(localStorage)) {
					if (!key.startsWith("talky.draft")) continue;
					const d = JSON.parse(localStorage.getItem(key));
					d.at -= 61 * 60 * 1000;
					localStorage.setItem(key, JSON.stringify(d));
				}
			});
			await page.reload({ waitUntil: "load" });
			await page.waitForSelector(sel.composer);
			if ((await page.inputValue(sel.composer)) !== "") fail("a draft older than an hour came back");
			return "the draft came back after a reload; one aged past an hour was dropped. Talky has no attachments, so there are none to restore";
		},

		// ─── 3. A running turn is visible, timed, and stoppable ─────────────
		"Send becomes Stop": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			await send(page, "[[sleep 4]] [[say done.]]");
			const stop = await page.waitForSelector(sel.stop, { timeout: 2000 });
			const label = await stop.getAttribute("aria-label");
			if (label !== "Cancel message") fail(`the Stop button is labelled "${label}"`);
			if ((await page.$(sel.send)) !== null) fail("Send is still shown during the turn");
			await page.click(sel.composer);
			await page.keyboard.insertText("next message draft");
			if ((await page.inputValue(sel.composer)) !== "next message draft") fail("the composer is not usable during a turn");
			await waitTurnDone(page);
			if ((await page.$(sel.send)) === null) fail("Send did not come back after the turn");
		},

		"Esc stops the agent": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			await send(page, "[[sleep 1]] [[lines 30]]");
			await page.waitForFunction((list) => /Line 1 of 30/.test(document.querySelector(list)?.innerText ?? ""), sel.list, { timeout: 30000 });
			await page.locator("body").press("Escape");
			await page.waitForSelector(sel.stop, { state: "detached", timeout: 5000 }).catch(() => fail("the turn was still running 5s after Esc"));
			await page.waitForTimeout(2000);
			const text = await lastText(page);
			if (/Line 30 of 30/.test(text)) fail("the reply ran to the end after Esc");
			return `cancelled at "${text.match(/Line \d+ of 30/g)?.at(-1) ?? "the first line"}"`;
		},

		"A thinking indicator appears only when it is needed": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			await page.evaluate((t) => {
				globalThis.__basicThinking = 0;
				new MutationObserver(() => {
					if (document.querySelector(t)) globalThis.__basicThinking += 1;
				}).observe(document.body, { childList: true, subtree: true });
			}, sel.thinking);
			await send(page, "[[say quick.]]");
			await waitTurnDone(page);
			const fast = await page.evaluate(() => globalThis.__basicThinking);
			if (fast) fail("the indicator flashed on a fast reply");
			await send(page, "[[sleep 4]] [[say slow.]]");
			const indicator = await page.waitForSelector(sel.thinking, { timeout: 3000 });
			const polite = await indicator.evaluate((el) => !!el.closest('[aria-live="polite"], [role="status"]'));
			if (!polite) fail("the indicator is not in a polite live region");
			await waitTurnDone(page);
			if ((await page.$(sel.thinking)) !== null) fail("the indicator stayed after the reply");
			return "no indicator on a fast reply; a polite status on a slow one";
		},

		"Each tool call shows a live timer": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			await send(page, "[[tool build 4]] [[say built.]]");
			const row = page.locator(`${sel.toolRow}[data-state="running"]`).last();
			await row.waitFor({ timeout: 10000 });
			const t1 = await row.getAttribute("data-elapsed");
			await page.waitForTimeout(2100);
			const t2 = await row.getAttribute("data-elapsed");
			if (!(Number(t2) >= Number(t1) + 2)) fail(`the timer went from ${t1}s to ${t2}s over two seconds`);
			const shown = await row.innerText();
			if (!new RegExp(`${t2}s`).test(shown)) fail(`the row shows "${shown}"`);
			const done = page.locator(`${sel.toolRow}[data-state="done"]`).last();
			await done.waitFor({ timeout: 10000 });
			const f1 = await done.innerText();
			await page.waitForTimeout(2100);
			const f2 = await done.innerText();
			if (f1 !== f2) fail(`the finished row kept counting: "${f1}" → "${f2}"`);
			await waitTurnDone(page);
			return `running ${t1}s → ${t2}s; finished at "${f2.replace(/\s+/g, " ")}"`;
		},

		"The whole task shows a live timer": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			await send(page, "[[tool longjob 75]] [[say finished.]]");
			await page.waitForTimeout(63000);
			const timer = await page.$eval(sel.taskTimer, (el) => el.innerText).catch(() => fail("no task timer after a minute"));
			if (!/task 1m \d+s/.test(timer)) fail(`the task timer reads "${timer}"`);
			const title = await page.title();
			if (!/working/i.test(title)) fail(`the tab title is "${title}"`);
			await page.waitForTimeout(1100);
			const later = await page.$eval(sel.taskTimer, (el) => el.innerText);
			if (later === timer) fail("the task timer is not live");
			await page.locator("body").press("Escape");
			return `"${timer}", tab title "${title}"`;
		},

		"A stuck tool row never runs forever": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			await send(page, "[[toolhang fetch]] [[say gave up.]]");
			await waitTurnDone(page);
			const row = page.locator(sel.toolRow).last();
			const state = await row.getAttribute("data-state");
			if (state !== "error") fail(`the unfinished tool row is "${state}"`);
			const a = await row.innerText();
			await page.waitForTimeout(2100);
			if ((await row.innerText()) !== a) fail("the unfinished row's timer keeps climbing");
			return a.replace(/\s+/g, " ");
		},

		"A turn that ends with nothing visible says so": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			await send(page, "[[sleep 1]] [[empty]]");
			await page.waitForFunction((n) => document.querySelector(n)?.innerText.includes("Agent completed without a visible response. Please retry."), sel.notice, { timeout: 15000 });
		},

		"Reasoning is folded": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			await send(page, "[[think weighing the options carefully]] [[sleep 2]] [[say the answer.]]");
			// Scoped to the last message: the ConverseButton also reads
			// "Thinking…" while a turn runs, and the user's own turn holds the
			// same words in its [[think]] directive.
			const lastMsg = page.locator(sel.message).last();
			const trigger = lastMsg.getByRole("button", { name: /Thinking|Thought/ }).last();
			await trigger.waitFor({ timeout: 10000 });
			const check = async (when) => {
				if ((await trigger.getAttribute("aria-expanded")) !== "false") fail(`the reasoning is open ${when}`);
				if (await lastMsg.getByText("weighing the options carefully").isVisible()) fail(`the reasoning text is visible ${when}`);
			};
			await check("while streaming");
			await waitTurnDone(page);
			await check("after the reply");
			const order = await page.evaluate(() => {
				const msg = [...document.querySelectorAll('[data-testid="transcript-message"]')].at(-1);
				const t = msg.querySelector("[aria-expanded]");
				const answer = [...msg.querySelectorAll(".karaoke-part")].find((e) => e.innerText.includes("the answer"));
				return t && answer ? t.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING : 0;
			});
			if (!order) fail("the reasoning is not above the answer");
			await trigger.click();
			if (!(await lastMsg.getByText("weighing the options carefully").isVisible())) fail("opening the disclosure does not show the reasoning");
		},

		// ─── 4. The conversation list tells me what needs me ────────────────
		"A new chat appears in the list at once": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			const before = page.url();
			const word = `zebra${Date.now() % 100000}`;
			await send(page, `[[sleep 5]] [[say hi.]] ${word}`);
			const row = page.locator(`${sel.group}[data-group="Today"] ${sel.row}`, { hasText: word });
			await row.waitFor({ timeout: 3000 }).catch(() => fail("the chat was not listed under Today within 3s"));
			if (await page.locator(sel.list).innerText().then((t) => t.includes("you said"))) fail("the reply arrived before the check");
			const id = await row.getAttribute("data-convo-id");
			if (page.url() === before || !page.url().includes(id)) fail(`the URL stayed ${page.url()}`);
			await waitTurnDone(page);
			return `listed under Today before the reply; URL is ${page.url().replace(base, "")}`;
		},

		"Each chat shows whether it is working": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			const word = `otter${Date.now() % 100000}`;
			await send(page, `[[sleep 4]] [[say done.]] ${word}`);
			const row = page.locator(sel.row, { hasText: word });
			await row.getByRole("status", { name: "Response in progress" }).waitFor({ timeout: 3000 }).catch(() => fail("the row has no 'Response in progress' status"));
			await waitTurnDone(page);
			await page.waitForTimeout(1000);
			if (await row.getByRole("status", { name: "Response in progress" }).count()) fail("the indicator stayed after the turn");
		},

		"Each chat shows working, idle, or failed at a glance": async ({ page, base }) => {
			seed({ turns: 4, daysAgo: 1 });
			await openFresh(page, base);
			await connect(page);
			const word = `heron${Date.now() % 100000}`;
			await send(page, `[[sleep 4]] [[fail the backend fell over]] ${word}`);
			const dot = (row) => row.locator('[data-testid="session-status"]');
			const mine = page.locator(sel.row, { hasText: word });
			await mine.waitFor();
			const look = async (row) =>
				dot(row).evaluate((el) => {
					const cs = getComputedStyle(el);
					const [r, g, b] = cs.backgroundColor.match(/[\d.]+/g).map(Number);
					return { status: el.getAttribute("data-status"), animated: cs.animationName !== "none", red: r > g * 1.3 && r > b * 1.3, green: g > r * 1.1 && g > b * 1.1 };
				});
			const working = await look(mine);
			if (working.status !== "working" || !working.animated) fail(`a working row shows ${JSON.stringify(working)}`);
			await page.waitForFunction((w) => document.querySelector(`[data-testid="session-row"]:has(+ *), [data-testid="session-row"]`) && [...document.querySelectorAll('[data-testid="session-row"]')].find((r) => r.innerText.includes(w))?.querySelector('[data-status="failed"]'), word, { timeout: 15000 }).catch(() => fail("the row never showed failed"));
			const failed = await look(mine);
			if (!failed.red) fail(`a failed row shows ${JSON.stringify(failed)}`);
			const rows = await page.locator(sel.row).all();
			for (const row of rows) {
				if ((await dot(row).count()) === 0) fail(`row "${(await row.innerText()).split("\n")[0]}" has no dot`);
			}
			const idle = page.locator(sel.row).filter({ hasNot: page.getByText(word) }).first();
			const idleLook = await look(idle);
			if (idleLook.status !== "idle" || !idleLook.green) fail(`an idle row shows ${JSON.stringify(idleLook)}`);
			return `${rows.length} rows with dots: working animated, failed red, idle green`;
		},

		"The list is grouped by time and searchable": async ({ page, base }) => {
			const tag = `g${Date.now() % 100000}`;
			seed({ title: `${tag} today`, turns: 2, daysAgo: 0 });
			seed({ title: `${tag} yesterday`, turns: 2, daysAgo: 1 });
			seed({ title: `${tag} this week`, turns: 2, daysAgo: 4 });
			seed({ title: `${tag} long ago`, turns: 2, daysAgo: 40 });
			await openFresh(page, base);
			const groups = await page.$$eval(sel.group, (els) => els.map((e) => e.getAttribute("data-group")));
			const want = ["Today", "Yesterday", "Last 7 days", "Older"];
			const order = groups.filter((g) => want.includes(g));
			if (order.join() !== want.join()) fail(`groups are ${groups.join(", ")}`);
			await page.fill(sel.search, `${tag} yesterday`);
			await page.waitForTimeout(800);
			const titles = await page.$$eval(`${sel.row} [data-testid="session-title"]`, (els) => els.map((e) => e.innerText));
			if (titles.length !== 1 || titles[0] !== `${tag} yesterday`) fail(`searching shows ${JSON.stringify(titles)}`);
			return `groups ${order.join(" / ")}; search narrowed to one title`;
		},

		// ─── 5. Messages have actions where I need them ─────────────────────
		"Copy a message": async ({ page, base }) => {
			const md = "Here is **bold** and a [link](https://example.com).";
			const convo = seed({ content: [{ role: "user", content: "copy test" }, { role: "assistant", content: md }] });
			await openSeeded(page, base, convo);
			const msg = page.locator(sel.message).last();
			await msg.hover();
			await msg.getByRole("button", { name: "Copy message" }).click();
			const copied = await page.evaluate(() => navigator.clipboard.readText());
			if (copied !== md) fail(`the clipboard holds ${JSON.stringify(copied)}`);
			return "the clipboard got the message's markdown";
		},

		"Copy a code block": async ({ page, base }) => {
			const code = 'def greet(name):\n    return f"hello, {name}"';
			const convo = seed({ content: [{ role: "user", content: "code please" }, { role: "assistant", content: "Sure:\n\n```python\n" + code + "\n```" }] });
			await openSeeded(page, base, convo);
			const button = page.locator(sel.message).last().getByRole("button", { name: /^copy( code)?$/i }).first();
			const name = (b) => b.evaluate((el) => (el.getAttribute("aria-label") || el.textContent || "").trim().toLowerCase());
			if (!/^copy( code)?$/.test(await name(button))) fail(`the code button is named "${await name(button)}"`);
			await button.click();
			const after = await name(button);
			if (!/copied/.test(after)) fail(`after a click the button says "${after}"`);
			await page.waitForTimeout(1600);
			const later = await name(button);
			if (/copied/.test(later)) fail("the button still says copied after 1.6s");
			const copied = await page.evaluate(() => navigator.clipboard.readText());
			if (copied.trim() !== code) fail(`the clipboard holds ${JSON.stringify(copied)}`);
			return `"copy" → "${after}" → "${later}"`;
		},

		"Retry, edit, and branch": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			const word = `branchy${Date.now() % 100000}`;
			await send(page, `[[say ok.]] ${word}`);
			await waitTurnDone(page);
			const mine = page.locator(sel.message, { hasText: word }).first();
			await mine.hover();
			for (const name of ["Retry", "Edit", "Branch off"]) {
				const b = mine.getByRole("button", { name });
				if ((await b.count()) === 0) fail(`no ${name} action on my message`);
				if (await b.isDisabled()) {
					const why = (await b.getAttribute("title")) ?? "";
					if (!why) fail(`${name} is disabled without saying why`);
				}
			}
			const n = await page.locator(sel.message, { hasText: word }).count();
			await mine.getByRole("button", { name: "Retry" }).click();
			await page.waitForFunction(({ m, w, n }) => [...document.querySelectorAll(m)].filter((e) => e.innerText.includes(w)).length > n, { m: sel.message, w: word, n }, { timeout: 10000 }).catch(() => fail("Retry did not send the message again"));
			await waitTurnDone(page);
			await mine.hover();
			await mine.getByRole("button", { name: "Edit" }).click();
			if (!(await page.inputValue(sel.composer)).includes(word)) fail("Edit did not put the message in the composer");
			await page.fill(sel.composer, "");
			await mine.hover();
			await mine.getByRole("button", { name: "Branch off" }).click();
			await page.waitForFunction((m) => document.querySelectorAll(m).length === 0, sel.message, { timeout: 5000 }).catch(() => fail("Branch off did not open a new chat"));
			// The message lands in the new chat's composer as soon as the new chat
			// is created (a short async gap after the transcript clears).
			await page.waitForFunction(({ c, w }) => (document.querySelector(c)?.value ?? "").includes(w), { c: sel.composer, w: word }, { timeout: 5000 }).catch(() => fail("Branch off did not carry the message into the new chat's composer"));
			return "Retry re-sent it, Edit put it back in the composer, Branch off opened a new chat with it in the composer";
		},

		"Code is highlighted without blocking the first token": async ({ page, base }) => {
			const shiki = [];
			page.on("request", (r) => {
				if (/shiki|streamdown_code|code-[\w-]+\.js|langs?\//i.test(r.url())) shiki.push({ url: r.url(), at: Date.now() });
			});
			await openFresh(page, base);
			await connect(page);
			const loadedEarly = shiki.length;
			await send(page, "[[code]]");
			await page.waitForFunction((list) => /Here is the function/.test(document.querySelector(list)?.innerText ?? ""), sel.list, { timeout: 20000 });
			const firstRender = await page.$$eval(sel.message, (els) => {
				const m = els.at(-1);
				return { raw: m.innerText.includes("```"), markdown: !!m.querySelector("p, pre, code") };
			});
			if (firstRender.raw || !firstRender.markdown) fail(`the first render is ${JSON.stringify(firstRender)}`);
			// Highlighted = token spans carry different colours (shiki sets them as
			// --sdm-c / --shiki-dark custom properties or inline color).
			await page.waitForFunction((list) => {
				const spans = [...document.querySelectorAll(`${list} pre span`)];
				if (spans.some((s) => /--sdm-c|--shiki-dark|color\s*:/.test(s.getAttribute("style") ?? ""))) return true;
				return new Set(spans.map((s) => getComputedStyle(s).color)).size > 1;
			}, sel.list, { timeout: 15000 }).catch(() => fail("the code block was never highlighted"));
			await waitTurnDone(page);
			return `markdown on the first render; highlighter fetched lazily (${loadedEarly ? "preloaded" : "after load"}), then the block upgraded`;
		},

		// ─── 6. Connection state is truthful ────────────────────────────────
		"Reconnecting is shown, then gives up honestly": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			daemon("stop");
			try {
				await page.waitForFunction((c) => /Reconnecting/.test(document.querySelector(c)?.innerText ?? ""), sel.connection, { timeout: 15000 }).catch(() => fail("no 'Reconnecting' after the daemon went away"));
				await page.waitForFunction((c) => /offline/i.test(document.querySelector(c)?.innerText ?? ""), sel.connection, { timeout: 60000 }).catch(() => fail("still not 'offline' a minute after the daemon went away"));
				if ((await page.locator(sel.retry).count()) === 0) fail("offline, but no Retry");
				const spinning = await page.$eval(sel.connection, (el) => [...el.querySelectorAll("*")].some((c) => getComputedStyle(c).animationName !== "none"));
				if (spinning) fail("the offline notice still spins");
			} finally {
				daemon("start");
			}
			await page.click(sel.retry);
			await page.waitForFunction(isConnected, sel.voice, { timeout: 30000 }).catch(() => fail("Retry did not reconnect"));
			return "Reconnecting, then Offline with Retry; Retry reconnected once the daemon was back";
		},

		"A stale close cannot undo a good connection": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			// End and immediately start again: the first peer's close lands after the second one opened.
			const end = page.getByRole("button", { name: /^End/ }).first();
			await end.click();
			await page.getByRole("button", { name: /^(Converse|Start call)$/ }).first().click();
			await page.waitForFunction(isConnected, sel.voice, { timeout: 30000 });
			const states = [];
			for (let i = 0; i < 20; i++) {
				await page.waitForTimeout(500);
				states.push(await page.$eval(sel.voice, (el) => el.getAttribute("aria-label")));
			}
			const lost = states.find((s) => !/IDLE|LISTENING|THINKING|SPEAKING/.test(s));
			if (lost) fail(`after the new connection opened the state went to "${lost}"`);
			await send(page, "[[say still here.]]");
			await page.waitForFunction((list) => document.querySelector(list)?.innerText.includes("still here"), sel.list, { timeout: 20000 });
			return "ten seconds after an end-and-restart the call stayed connected and a turn went through";
		},

		"A backgrounded tab catches up when it returns": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			await send(page, "[[sleep 2]] [[say finished while you were away.]]");
			const cdp = await page.context().newCDPSession(page);
			await page.evaluate(() => {
				Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
				Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
				document.dispatchEvent(new Event("visibilitychange"));
			});
			await cdp.send("Page.setWebLifecycleState", { state: "frozen" });
			await new Promise((r) => setTimeout(r, 15000));
			await cdp.send("Page.setWebLifecycleState", { state: "active" });
			await page.evaluate(() => {
				Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
				Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
				document.dispatchEvent(new Event("visibilitychange"));
			});
			await page.waitForFunction((list) => document.querySelector(list)?.innerText.includes("finished while you were away"), sel.list, { timeout: 15000 }).catch(() => fail("the finished turn did not show after returning"));
			await page.waitForFunction(isConnected, sel.voice, { timeout: 20000 }).catch(() => fail("the page did not reconnect after returning"));
			return "frozen hidden for 15s while the turn finished; on return it was connected and showed the reply";
		},

		"A turn interrupted by a restart resumes": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			const word = `resume${Date.now() % 100000}`;
			await send(page, `[[tool migrate 6]] [[say migration finished.]] ${word}`);
			await page.locator(`${sel.toolRow}[data-state="running"]`).last().waitFor({ timeout: 10000 });
			await page.waitForTimeout(1500);
			daemon("stop");
			daemon("start");
			await page.waitForFunction((n) => /interrupted by a restart/i.test([...document.querySelectorAll(n)].map((e) => e.innerText).join(" ")), sel.notice, { timeout: 60000 }).catch(() => fail("the transcript never said the turn was interrupted"));
			await page.waitForFunction((list) => document.querySelector(list)?.innerText.includes("migration finished"), sel.list, { timeout: 60000 }).catch(() => fail("the interrupted turn never finished"));
			return "after a daemon restart mid tool call the transcript said so and the turn ran to its reply";
		},

		// ─── 7. Notices are placed in time, not pinned on top ───────────────
		"Errors sit in the transcript in order": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			await send(page, "[[say first reply.]]");
			await waitTurnDone(page);
			await send(page, "[[fail the backend fell over]]");
			const notice = page.locator(sel.notice, { hasText: "the backend fell over" });
			await notice.waitFor({ timeout: 15000 });
			const placed = await page.evaluate(({ list, message }) => {
				const notices = [...document.querySelectorAll('[data-testid="transcript-notice"]')];
				const n = notices.at(-1);
				const last = [...document.querySelectorAll(message)].at(-1);
				return { inside: document.querySelector(list).contains(n), after: !!(last.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING) };
			}, { list: sel.list, message: sel.message });
			if (!placed.inside || !placed.after) fail(`the notice is ${JSON.stringify(placed)}`);
			await send(page, "[[say back again.]]");
			await waitTurnDone(page);
			if (await notice.count()) fail("starting a new turn did not clear the notice");
			return "the notice sat after the turn it belongs to and cleared on the next turn";
		},

		"A long error is summarised, with detail on demand": async ({ page, base }) => {
			await openFresh(page, base);
			await connect(page);
			const detail = "Traceback: " + Array.from({ length: 12 }, (_, i) => `frame ${i} in module_${i}.py line ${i * 10}`).join("; ");
			await send(page, `[[fail Agent process exited. ${detail}]]`);
			const notice = page.locator(sel.notice, { hasText: "Agent process exited" }).last();
			await notice.waitFor({ timeout: 15000 });
			const summaryHeight = await notice.evaluate((el) => {
				const s = el.querySelector("summary") ?? el.firstElementChild;
				return s.getBoundingClientRect().height;
			});
			if (summaryHeight > 40) fail(`the summary line is ${summaryHeight}px tall`);
			// Scoped to the notice: the user's own message legitimately holds the
			// same text (the [[fail]] script is how the error is synthesised).
			if (await notice.getByText(/frame 11 in module_11\.py/).isVisible()) fail("the full text is shown up front");
			await notice.getByText(/details/i).click();
			if (!(await notice.getByText(/frame 11 in module_11\.py/).isVisible())) fail("the details do not show the full text");
			return "one line, the full traceback behind Details";
		},

		// ─── 8. Side panels fit the reader's screen ─────────────────────────
		"A side panel can be hidden and shown again": async ({ page, base }) => {
			await openFresh(page, base);
			await page.waitForSelector(sel.rail);
			await dragBy(page, sel.resize, 40);
			const width = await panelWidth(page, sel.rail);
			const main = await panelWidth(page, "main");
			await page.getByRole("button", { name: "Hide sessions" }).click();
			if ((await page.locator(sel.rail).count()) && (await page.locator(sel.rail).isVisible())) fail("the panel is still shown");
			const wider = await panelWidth(page, "main");
			if (wider < main + width - 4) fail(`the conversation grew from ${main}px to ${wider}px, not by the panel's ${width}px`);
			await page.getByRole("button", { name: "Show sessions" }).click();
			await page.waitForSelector(sel.rail);
			const back = await panelWidth(page, sel.rail);
			if (Math.abs(back - width) > 2) fail(`the panel came back at ${back}px, not ${width}px`);
			return `hid (conversation ${main}px → ${wider}px) and came back at ${back}px`;
		},

		"A side panel can be resized on desktop": async ({ page, base }) => {
			await openFresh(page, base);
			await page.waitForSelector(sel.resize);
			const w0 = await panelWidth(page, sel.rail);
			await dragBy(page, sel.resize, 60);
			const w1 = await panelWidth(page, sel.rail);
			if (Math.abs(w1 - (w0 + 60)) > 4) fail(`dragging 60px moved the panel from ${w0} to ${w1}`);
			await dragBy(page, sel.resize, 2000);
			const max = await panelWidth(page, sel.rail);
			const convo = await panelWidth(page, "main");
			await dragBy(page, sel.resize, -2000);
			const min = await panelWidth(page, sel.rail);
			if (max > 1280 - 400) fail(`the panel grew to ${max}px`);
			if (convo < 400) fail(`at the panel's widest the conversation is ${convo}px`);
			if (min < 160) fail(`the panel shrank to ${min}px`);
			return `follows the pointer (${w0} → ${w1}px), stops at ${min}–${max}px, conversation keeps ${convo}px`;
		},

		"Panel sizes and visibility survive a reload": async ({ page, base }) => {
			await openFresh(page, base);
			await page.waitForSelector(sel.resize);
			await dragBy(page, sel.resize, -2000);
			await dragBy(page, sel.resize, 70);
			const width = await panelWidth(page, sel.rail);
			await page.reload({ waitUntil: "load" });
			await page.waitForSelector(sel.rail);
			const after = await panelWidth(page, sel.rail);
			if (Math.abs(after - width) > 2) fail(`the panel was ${width}px and came back ${after}px`);
			await page.getByRole("button", { name: "Hide sessions" }).click();
			await page.reload({ waitUntil: "load" });
			await page.waitForSelector(sel.composer);
			if (await page.locator(sel.rail).isVisible().catch(() => false)) fail("a hidden panel came back after a reload");
			await page.getByRole("button", { name: "Show sessions" }).click();
			return `${width}px survived a reload, and so did hidden`;
		},

		"What is inside a panel adapts to its width": async ({ page, base }) => {
			seed({ title: "A very long session title that will not fit in a narrow sidebar at all, really", turns: 2 });
			await openSeeded(page, base, seed({ turns: 6 }));
			const overflow = () =>
				page.evaluate(() => {
					const out = [];
					for (const root of [document.querySelector('[data-testid="session-rail"]'), document.querySelector("main"), document.querySelector("header")]) {
						if (!root) continue;
						const box = root.getBoundingClientRect();
						const clipped = (el) => {
							for (let a = el.parentElement; a && a !== root.parentElement; a = a.parentElement) if (/hidden|auto|scroll|clip/.test(getComputedStyle(a).overflowX)) return a.getBoundingClientRect();
							return null;
						};
						for (const el of root.querySelectorAll("*")) {
							const r = el.getBoundingClientRect();
							if (!r.width || getComputedStyle(el).visibility === "hidden") continue;
							const clip = clipped(el) ?? box;
							const right = Math.min(clip.right, box.right);
							const left = Math.max(clip.left, box.left);
							// Clipped by an overflow:hidden ancestor (truncate/ellipsis) is fine; only content that pokes out of view is not.
							if (clip !== box && clip.right <= box.right + 1 && clip.left >= box.left - 1) continue;
							if (r.right > right + 1 || r.left < left - 1) out.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 40)} sticks out of ${root.tagName.toLowerCase()}`);
						}
						if (root.scrollWidth > root.clientWidth + 1) out.push(`${root.tagName.toLowerCase()} scrolls sideways`);
					}
					return out;
				});
			const widths = [];
			for (const dx of [-2000, 2000]) {
				await dragBy(page, sel.resize, dx);
				widths.push(await panelWidth(page, sel.rail));
				const bad = await overflow();
				if (bad.length) fail(`at a ${widths.at(-1)}px panel: ${bad.slice(0, 3).join("; ")}`);
			}
			for (const w of [900, 700]) {
				await page.setViewportSize({ width: w, height: 800 });
				await page.waitForTimeout(200);
				const bad = await overflow();
				if (bad.length) fail(`at ${w}px wide: ${bad.slice(0, 3).join("; ")}`);
			}
			return `no sideways overflow at panel widths ${widths.join(" and ")}px, or at 900 and 700px windows`;
		},

		"On a phone, side panels open over the conversation": async ({ page: desktop, base }) =>
			phone(desktop, async (page) => {
				await fresh(base);
				await open(page, base);
				const main = await panelWidth(page, "main");
				if (main < 385) fail(`the conversation is ${main}px wide on a 390px screen`);
				if (await page.locator(sel.rail).isVisible().catch(() => false)) fail("a side panel takes width on a phone");
				if ((await page.locator(sel.resize).count()) && (await page.locator(sel.resize).isVisible())) fail("there is a drag edge on a phone");
				await page.getByRole("button", { name: /Show sessions|Sessions/ }).first().tap();
				const drawer = page.locator('[data-testid="session-drawer"]');
				await drawer.waitFor();
				const over = await drawer.evaluate((el) => getComputedStyle(el).position === "fixed");
				if (!over) fail("the panel does not open over the conversation");
				if ((await panelWidth(page, "main")) !== main) fail("opening the panel resized the conversation");
				await page.keyboard.press("Escape");
				await drawer.waitFor({ state: "detached", timeout: 3000 }).catch(() => fail("the panel does not close"));
				return "the sessions sheet opens over the conversation from a button and closes again; no drag edge";
			}),

		// ─── 9. Signing in and out ──────────────────────────────────────────
		"A signed-out visitor sees one way in": SIGN_IN_NA,
		"The API refuses a caller who is not signed in": SIGN_IN_NA,
		"The first sign-in asks only what it needs, then lands somewhere useful": SIGN_IN_NA,
		"A cancelled or failed sign-in says why": SIGN_IN_NA,
		"Who I am is always one click away": SIGN_IN_NA,
		"Signing out is one visible control": SIGN_IN_NA,
		"After signing out, nothing private is reachable": SIGN_IN_NA,

		// ─── 10. The page works for everyone ────────────────────────────────
		"The transcript is a polite live region": async ({ page, base }) => {
			await openFresh(page, base);
			const region = await page.$eval(sel.list, (el) => {
				const live = el.closest('[role="log"], [aria-live="polite"]');
				if (!live) return null;
				const labelled = live.getAttribute("aria-labelledby");
				const heading = (labelled && document.getElementById(labelled)) || live.querySelector("h1, h2, h3") || live.parentElement.querySelector("h1, h2, h3");
				return { role: live.getAttribute("role"), live: live.getAttribute("aria-live"), heading: heading?.textContent ?? null };
			});
			if (!region) fail("the transcript is not a log or polite live region");
			if (!region.heading) fail("the transcript region has no heading");
			return `role=${region.role} aria-live=${region.live}, heading "${region.heading}"`;
		},

		"Motion respects the system setting": async ({ page, base }) => {
			await page.emulateMedia({ reducedMotion: "reduce" });
			await openSeeded(page, base, seed({ turns: 30 }));
			await connect(page);
			await send(page, "[[sleep 2]] [[tool spin 2]] [[lines 4]]");
			await page.waitForTimeout(1500);
			const moving = await page.evaluate((list) => {
				const out = [];
				for (const el of document.querySelectorAll("*")) {
					const cs = getComputedStyle(el);
					const anim = cs.animationName !== "none" && parseFloat(cs.animationDuration) > 0.01 && cs.animationPlayState === "running";
					if (anim) out.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 30)} (${cs.animationName})`);
				}
				if (getComputedStyle(document.querySelector(list)).scrollBehavior === "smooth") out.push("transcript smooth-scrolls");
				return out;
			}, sel.list);
			await waitTurnDone(page);
			if (moving.length) fail(`still animating: ${moving.slice(0, 4).join(", ")}`);
			return "with reduced motion on, nothing animated during a turn";
		},

		"Every icon button has a name": async ({ page, base }) => {
			await openSeeded(page, base, seed({ turns: 4 }));
			await connect(page);
			await send(page, "[[think hm]] [[tool grep 1]] [[code]]");
			await waitTurnDone(page);
			await page.locator(sel.message).last().hover();
			const nameless = await page.evaluate(() =>
				[...document.querySelectorAll('button, [role="button"]')]
					.filter((b) => b.getClientRects().length && !b.innerText.trim())
					.filter((b) => !(b.getAttribute("aria-label") || b.getAttribute("aria-labelledby") || b.getAttribute("title") || b.querySelector(".sr-only")?.textContent.trim()))
					.map((b) => b.outerHTML.slice(0, 90)),
			);
			if (nameless.length) fail(`${nameless.length} icon buttons have no name: ${nameless[0]}`);
			const total = await page.evaluate(() => [...document.querySelectorAll("button")].filter((b) => b.getClientRects().length && !b.innerText.trim()).length);
			return `${total} icon-only buttons, all named`;
		},

		"No text sits on top of other text": async ({ page, base }) => {
			const report = [];
			const run = async (p, label) => {
				await fresh(base);
				await open(p, base);
				let hits = await textOverlaps(p);
				if (hits.length) fail(`${label}, idle: ${hits.slice(0, 2).join("; ")}`);
				await connect(p);
				await p.fill(sel.composer, "");
				await p.click(sel.composer);
				await p.keyboard.insertText("[[sleep 2]] [[tool scan 2]] [[lines 6]]");
				// On a touch device Enter is a newline, not a send — tap Send.
				if (p.viewportSize()?.width && p.viewportSize().width <= 640) await p.tap(sel.send);
				else await p.keyboard.press("Enter");
				await p.waitForSelector(sel.toolRow, { timeout: 15000 });
				hits = await textOverlaps(p);
				if (hits.length) fail(`${label}, during a turn: ${hits.slice(0, 2).join("; ")}`);
				await waitTurnDone(p);
				report.push(label);
			};
			await run(page, "desktop");
			// The desktop page is still in a call; the daemon serves one peer at a
			// time, and the desktop tab's auto-reconnect would steal the pipeline
			// from the phone. End its call first (test isolation, not app behavior).
			await page.getByRole("button", { name: /^End/ }).first().click().catch(() => {});
			await page.waitForFunction((v) => !/Voice status: (IDLE|LISTENING|THINKING|SPEAKING)/.test(document.querySelector(v)?.getAttribute("aria-label") ?? ""), sel.voice, { timeout: 10000 }).catch(() => {});
			await phone(page, (p) => run(p, "phone"));
			return `no overlapping text on ${report.join(" and ")}, idle and mid-turn`;
		},

		// ─── 11. The first screen invites a first message ───────────────────
		"The empty state offers starters": async ({ page, base }) => {
			await openFresh(page, base);
			const empty = await page.waitForSelector(sel.empty, { timeout: 5000 });
			const question = await empty.$eval("h1, h2", (el) => el.innerText).catch(() => "");
			if (!/\?$/.test(question.trim())) fail(`the empty state heading is "${question}"`);
			const starters = await page.$$(sel.starter);
			if (starters.length < 3 || starters.length > 5) fail(`${starters.length} starters`);
			const text = await starters[0].innerText();
			await starters[0].click();
			await page.waitForFunction(({ m, t }) => [...document.querySelectorAll(m)].some((e) => e.innerText.includes(t)), { m: sel.message, t: text.trim().slice(0, 20) }, { timeout: 30000 }).catch(() => fail("clicking a starter did not send it"));
			await waitTurnDone(page);
			return `"${question}" with ${starters.length} starters; one click sent "${text.trim()}"`;
		},
	},
};
