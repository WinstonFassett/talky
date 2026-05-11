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

/**
 * Pump SDK messages → ws frames. Runs for the lifetime of the session;
 * each new user input pushed onto the queue produces an assistant turn
 * whose stream events flow through here.
 */
async function pumpQuery() {
	for await (const msg of queryHandle) {
		// Streaming token deltas — drive TTS as soon as text arrives.
		if (msg.type === "stream_event") {
			const ev = msg.event;
			if (ev?.type === "content_block_delta" && ev.delta?.type === "text_delta") {
				emitTtsDelta(ev.delta.text);
			}
			continue;
		}

		// Final assistant message — finalize the TTS turn and surface
		// any tool-call breadcrumbs. We don't re-emit the assembled text
		// because stream_event already streamed it.
		if (msg.type === "assistant") {
			const blocks = msg.message?.content || [];
			let sawTool = false;
			for (const block of blocks) {
				if (block.type === "tool_use") {
					sawTool = true;
					const name = block.name || "?";
					const input = block.input || {};
					let hint = "";
					if (typeof input.path === "string") hint = `: ${input.path}`;
					else if (typeof input.command === "string") {
						const cmd = input.command;
						hint = `: ${cmd.slice(0, 60)}${cmd.length > 60 ? "…" : ""}`;
					} else if (typeof input.pattern === "string") hint = `: ${input.pattern}`;
					send({ type: "tool_start", text: `▶ ${name}${hint}` });
				}
			}
			// Only close out the TTS turn if this assistant message
			// wasn't a pure tool_use turn (tool_use turns are followed
			// by tool_result and another assistant message — keep TTS
			// open across that boundary).
			if (!sawTool) finishTurn();
			continue;
		}

		// Tool-result coming back from the SDK runtime.
		if (msg.type === "user" && msg.tool_use_result) {
			const r = msg.tool_use_result;
			let name = "?";
			let suffix = "";
			if (Array.isArray(msg.message?.content)) {
				for (const block of msg.message.content) {
					if (block.type === "tool_result") {
						// Best-effort line count for parity with pi-voice.
						const txt = Array.isArray(block.content)
							? block.content
									.filter((c) => c.type === "text")
									.map((c) => c.text)
									.join("\n")
							: typeof block.content === "string"
								? block.content
								: "";
						const lines = txt ? txt.split("\n").length : 0;
						if (lines > 0) suffix = ` (${lines} lines)`;
						if (block.is_error) {
							send({ type: "tool_end", text: `✗ ${name}` });
							continue;
						}
					}
				}
			}
			send({ type: "tool_end", text: `✓ ${name}${suffix}` });
			continue;
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
