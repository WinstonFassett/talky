/**
 * Pi Voice Extension
 *
 * Bridges a running Pi terminal session to the talky daemon's voice pipeline.
 * When loaded, connects to ws://localhost:9090/ws/pi and:
 *   - Receives STT text from the daemon → injects as user messages into Pi
 *   - Streams Pi's response tokens → daemon TTS
 *   - Responds to abort signals from the daemon → ctx.abort()
 *
 * Usage (user-initiated): load this extension in any Pi session
 *   pi -e ~/.pi/agent/extensions/pi-voice.ts
 *   or drop in ~/.pi/agent/extensions/pi-voice/extension.ts
 *
 * Usage (talky-initiated): talky starts Pi with this extension and an opener
 *   pi -e <path>/extension.ts --prompt "..."
 *
 * Requires: talky daemon running on localhost:9090, Node 18+ (global WebSocket).
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

const DAEMON_WS_URL = "ws://localhost:9090/ws/pi";
const RECONNECT_DELAY_MS = 2000;
const MAX_RECONNECT_DELAY_MS = 30000;

export default function (pi: ExtensionAPI) {
	let ws: WebSocket | null = null;
	let reconnectDelay = RECONNECT_DELAY_MS;
	let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
	let destroyed = false;

	// Abort function captured from the current agent turn's context.
	// Set on agent_start, cleared on agent_end.
	let currentAbort: (() => void) | null = null;

	// TTS framing state: did we send tts_start for this turn yet?
	let turnHasText = false;

	function send(msg: object): void {
		if (ws?.readyState === WebSocket.OPEN) {
			ws.send(JSON.stringify(msg));
		}
	}

	function setStatus(text: string | undefined): void {
		try {
			pi.ui?.setStatus("voice", text);
		} catch {
			// ui may be unavailable in headless/RPC modes
		}
	}

	function connect(): void {
		if (destroyed) return;

		ws = new WebSocket(DAEMON_WS_URL);

		ws.onopen = () => {
			reconnectDelay = RECONNECT_DELAY_MS;
			setStatus("🎙 voice: connected");
		};

		ws.onmessage = (event: MessageEvent) => {
			let msg: { type: string; text?: string };
			try {
				msg = JSON.parse(event.data as string);
			} catch {
				return;
			}

			if (msg.type === "ready") {
				setStatus("🎙 voice: active");
			} else if (msg.type === "stt") {
				// User speech transcript from daemon — inject as a Pi user message.
				const text = msg.text?.trim();
				if (text) {
					if (currentAbort) {
						// Mid-turn: steer to interrupt the running turn.
						pi.sendUserMessage(text, { deliverAs: "steer" });
					} else {
						pi.sendUserMessage(text);
					}
				}
			} else if (msg.type === "abort") {
				// VAD barge-in: cancel Pi's current turn.
				currentAbort?.();
			}
		};

		ws.onclose = () => {
			ws = null;
			setStatus(undefined);
			if (destroyed) return;
			reconnectTimer = setTimeout(() => {
				reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY_MS);
				connect();
			}, reconnectDelay);
		};

		ws.onerror = () => {
			// onclose fires next — handled there
		};
	}

	// ── Pi event hooks ────────────────────────────────────────────────────────

	pi.on("agent_start", (_event, ctx) => {
		currentAbort = () => ctx.abort();
		turnHasText = false;
	});

	pi.on("message_update", (event) => {
		const evt = (event as any).assistantMessageEvent;
		if (evt?.type !== "text_delta") return;
		const delta: string = evt.delta ?? "";
		if (!delta) return;

		if (!turnHasText) {
			send({ type: "tts_start" });
			turnHasText = true;
		}
		send({ type: "tts", text: delta });
	});

	pi.on("tool_execution_start", (event) => {
		const e = event as any;
		const name: string = e.toolName ?? "?";
		const args: Record<string, unknown> = e.args ?? {};
		let hint = "";
		if (typeof args.path === "string") hint = `: ${args.path}`;
		else if (typeof args.command === "string") hint = `: ${args.command.slice(0, 60)}${args.command.length > 60 ? "…" : ""}`;
		else if (typeof args.pattern === "string") hint = `: ${args.pattern}`;
		send({ type: "tool_start", text: `▶ ${name}${hint}` });
	});

	pi.on("tool_execution_end", (event) => {
		const e = event as any;
		const name: string = e.toolName ?? "?";
		if (e.isError) {
			send({ type: "tool_end", text: `✗ ${name}` });
			return;
		}
		const content: Array<{ type: string; text?: string }> = e.result?.content ?? [];
		const textContent = content.find((c) => c.type === "text")?.text ?? "";
		const lines = textContent ? textContent.split("\n").length : 0;
		const suffix = lines > 0 ? ` (${lines} lines)` : "";
		send({ type: "tool_end", text: `✓ ${name}${suffix}` });
	});

	pi.on("agent_end", () => {
		if (turnHasText) {
			send({ type: "tts_end" });
		}
		turnHasText = false;
		currentAbort = null;
	});

	// ── Slash command for manual activation check ─────────────────────────────

	pi.registerCommand("voice", {
		description: "Show talky voice connection status",
		handler: (_args, ctx) => {
			const state = ws?.readyState;
			const label =
				state === WebSocket.OPEN
					? "connected (active)"
					: state === WebSocket.CONNECTING
						? "connecting…"
						: "disconnected (retrying)";
			ctx.ui.notify(`Talky voice: ${label}`, "info");
		},
	});

	// ── Session shutdown ──────────────────────────────────────────────────────

	pi.on("session_shutdown", () => {
		destroyed = true;
		if (reconnectTimer !== null) clearTimeout(reconnectTimer);
		ws?.close();
	});

	// Kick off the initial connection.
	connect();
}
