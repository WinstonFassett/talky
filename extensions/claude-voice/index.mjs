/**
 * Claude Voice Extension — Claude Agent SDK ↔ talky daemon /ws/agent bridge.
 *
 * Parity with `extensions/pi-voice/extension.ts` but for Claude Code via the
 * Agent SDK (no `pi -e <file>` mechanism exists for Claude). Same wire
 * protocol on the websocket side, so the daemon doesn't care which agent is
 * driving.
 *
 * Daemon → us:
 *   {type:"ready"}                       handshake after accept
 *   {type:"greet", instruction:"..."}    agent should greet in its own words
 *   {type:"stt", text:"..."}             user speech transcript
 *   {type:"abort"}                       VAD barge-in, abort current turn
 *
 * Us → daemon:
 *   {type:"tts_start"}                   agent response starting
 *   {type:"tts", text:"..."}             response token delta
 *   {type:"tts_end"}                     agent response complete
 *   {type:"tool_start", text:"..."}      tool call started
 *   {type:"tool_end",   text:"..."}      tool call finished
 */

import { query } from "@anthropic-ai/claude-agent-sdk";

const WS_URL = process.env.TALKY_AGENT_WS_URL || "ws://localhost:9090/ws/agent";
const RECONNECT_DELAY_MS = 1000;
const MAX_RECONNECT_DELAY_MS = 15000;

/**
 * AsyncIterable-backed queue we hand to `query()` as its prompt stream.
 * Pushing a message kicks off a new assistant turn.
 */
function makeInputQueue() {
	const pending = [];
	const waiters = [];
	let closed = false;

	function push(text) {
		const msg = {
			type: "user",
			message: { role: "user", content: text },
			parent_tool_use_id: null,
			session_id: "talky",
		};
		if (waiters.length > 0) {
			waiters.shift()(msg);
		} else {
			pending.push(msg);
		}
	}

	function close() {
		closed = true;
		while (waiters.length > 0) {
			waiters.shift()(null);
		}
	}

	const iter = {
		[Symbol.asyncIterator]() {
			return {
				async next() {
					if (pending.length > 0) {
						return { value: pending.shift(), done: false };
					}
					if (closed) return { value: undefined, done: true };
					return new Promise((resolve) => {
						waiters.push((msg) => {
							if (msg === null) resolve({ value: undefined, done: true });
							else resolve({ value: msg, done: false });
						});
					});
				},
			};
		},
	};

	return { push, close, iter };
}

let ws = null;
let queueHandle = null;
let queryHandle = null;
let turnHasText = false;
let reconnectDelay = RECONNECT_DELAY_MS;
let destroyed = false;

function send(msg) {
	if (ws && ws.readyState === 1 /* OPEN */) {
		ws.send(JSON.stringify(msg));
	}
}

function emitTtsDelta(delta) {
	if (!delta) return;
	if (!turnHasText) {
		send({ type: "tts_start" });
		turnHasText = true;
	}
	send({ type: "tts", text: delta });
}

function finishTurn() {
	if (turnHasText) {
		send({ type: "tts_end" });
		turnHasText = false;
	}
}

// Tool-use bookkeeping so tool_end can name the tool by id (Claude's tool_use
// blocks carry an id; the matching tool_result block back-references it).
const toolNamesById = new Map();

function emitEvent(kind, text, payload) {
	const msg = { type: "event", kind };
	if (text) msg.text = text;
	if (payload !== undefined) msg.payload = payload;
	send(msg);
}

function previewToolInput(name, input) {
	if (!input || typeof input !== "object") return "";
	if (typeof input.path === "string") return `: ${input.path}`;
	if (typeof input.command === "string") {
		const cmd = input.command;
		return `: ${cmd.slice(0, 60)}${cmd.length > 60 ? "…" : ""}`;
	}
	if (typeof input.pattern === "string") return `: ${input.pattern}`;
	if (typeof input.file_path === "string") return `: ${input.file_path}`;
	return "";
}

/**
 * Pump SDK messages → ws frames. Runs for the lifetime of the session;
 * each new user input pushed onto the queue produces an assistant turn
 * whose stream events flow through here.
 */
async function pumpQuery() {
	for await (const msg of queryHandle) {
		// Streaming deltas — drive TTS (text) or thinking surface.
		if (msg.type === "stream_event") {
			const ev = msg.event;
			const delta = ev?.delta;
			if (ev?.type === "content_block_delta" && delta) {
				if (delta.type === "text_delta") {
					emitTtsDelta(delta.text);
				} else if (delta.type === "thinking_delta" && delta.thinking) {
					process.stderr.write(`[claude-voice] thinking_delta — emitting event (${delta.thinking.length} chars)\n`);
					emitEvent("thinking", delta.thinking);
				} else {
					process.stderr.write(`[claude-voice] stream_event delta type: ${delta?.type ?? '(none)'}, ev type: ${ev?.type}\n`);
				}
			}
			continue;
		}

		// System init / compaction / status notices.
		if (msg.type === "system") {
			if (msg.subtype === "init") {
				const info = {
					session_id: msg.session_id,
					model: msg.model,
					cwd: msg.cwd,
					tools: msg.tools,
				};
				emitEvent("info", `Claude session ${msg.model || ""}`.trim(), info);
			} else if (msg.subtype === "compact_boundary") {
				emitEvent("info", "context compacted", msg.compact_metadata);
			}
			continue;
		}

		// Rate-limit notices and other status events.
		if (msg.type === "rate_limit_event") {
			const status = msg.rate_limit_info?.status;
			if (status === "rejected" || status === "allowed_warning") {
				emitEvent("error", `rate limit (${status})`, msg.rate_limit_info);
			}
			continue;
		}
		if (msg.type === "status") {
			emitEvent("info", msg.text || "status", msg);
			continue;
		}

		// Final assistant message — surface tool calls + finalize TTS turn.
		if (msg.type === "assistant") {
			if (msg.error) {
				emitEvent("error", `assistant error: ${msg.error}`, { error: msg.error });
			}
			const blocks = msg.message?.content || [];
			let sawTool = false;
			for (const block of blocks) {
				if (block.type === "tool_use") {
					sawTool = true;
					const name = block.name || "?";
					if (block.id) toolNamesById.set(block.id, { name, input: block.input });
					emitEvent("tool_start", name, { tool_use_id: block.id, input: block.input });
				}
			}
			// Pure tool_use turn? Keep TTS open across the boundary
			// (tool_result + assistant text turn follow).
			if (!sawTool) finishTurn();
			continue;
		}

		// Tool-result coming back from the SDK runtime.
		if (msg.type === "user") {
			const blocks = Array.isArray(msg.message?.content) ? msg.message.content : [];
			let emitted = false;
			for (const block of blocks) {
				if (block.type !== "tool_result") continue;
				emitted = true;
				const entry = toolNamesById.get(block.tool_use_id) || { name: "?" };
				const name = entry.name || "?";
				const txt = Array.isArray(block.content)
					? block.content
							.filter((c) => c.type === "text")
							.map((c) => c.text)
							.join("\n")
					: typeof block.content === "string"
						? block.content
						: "";
				const lines = txt ? txt.split("\n").length : 0;
				emitEvent("tool_end", name, {
					tool_use_id: block.tool_use_id,
					is_error: block.is_error || false,
					result_lines: lines,
				});
				if (block.tool_use_id) toolNamesById.delete(block.tool_use_id);
			}
			if (emitted) continue;
		}

		// Turn boundary: result message marks end of one full
		// user→assistant cycle. Make sure TTS is closed.
		if (msg.type === "result") {
			finishTurn();
		}
	}
	finishTurn();
}

function connect() {
	// Node 22+ has global WebSocket. For older runtimes, use 'ws' package
	// (we don't depend on it explicitly — most users will be on >=22).
	if (typeof WebSocket === "undefined") {
		console.error("Global WebSocket not available — requires Node 22+ or polyfill");
		process.exit(1);
	}

	ws = new WebSocket(WS_URL);

	ws.addEventListener("open", () => {
		process.stderr.write(`[claude-voice] connected to ${WS_URL}\n`);
		reconnectDelay = RECONNECT_DELAY_MS;
		// Identify which talky profile launched us so the daemon can switch
		// to the right profile (not just any profile using agent-ext backend).
		const profile = process.env.TALKY_PROFILE;
		if (profile) ws.send(JSON.stringify({ type: "hello", profile }));
		// Build a fresh SDK query per connection. Tearing down + recreating
		// is the simplest way to deal with the "browser disconnected, pipeline
		// rebuilt, reconnect" case: there's no shared state across sessions.
		queueHandle = makeInputQueue();
		queryHandle = query({
			prompt: queueHandle.iter,
			options: {
				includePartialMessages: true,
				permissionMode: "acceptEdits",
			},
		});
		pumpQuery().catch((e) => {
			process.stderr.write(`[claude-voice] pump error: ${e?.stack || e}\n`);
		});
	});

	ws.addEventListener("message", (event) => {
		let msg;
		try {
			msg = JSON.parse(event.data);
		} catch {
			return;
		}

		switch (msg.type) {
			case "ready":
				process.stderr.write("[claude-voice] daemon ready\n");
				break;
			case "greet":
				if (msg.instruction && queueHandle) queueHandle.push(msg.instruction);
				break;
			case "stt":
				if (msg.text && queueHandle) queueHandle.push(msg.text);
				break;
			case "abort":
				if (queryHandle) {
					queryHandle.interrupt().catch(() => {});
				}
				break;
			default:
				break;
		}
	});

	ws.addEventListener("close", () => {
		try {
			queueHandle?.close();
		} catch {
			/* ignore */
		}
		queueHandle = null;
		try {
			queryHandle?.interrupt?.();
		} catch {
			/* ignore */
		}
		queryHandle = null;
		finishTurn();
		if (destroyed) {
			process.exit(0);
		}
		process.stderr.write(
			`[claude-voice] websocket closed — reconnecting in ${reconnectDelay}ms\n`,
		);
		setTimeout(() => {
			reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY_MS);
			connect();
		}, reconnectDelay);
	});

	ws.addEventListener("error", (e) => {
		process.stderr.write(`[claude-voice] websocket error: ${e?.message || e}\n`);
	});
}

process.on("SIGINT", () => {
	destroyed = true;
	try {
		ws?.close();
	} catch {
		/* ignore */
	}
	process.exit(0);
});

connect();
