/**
 * Talky Voice Extension for Pi Coding Agent
 *
 * Adds voice conversation capabilities to Pi via the Talky MCP server.
 * Registers voice tools, a /voice command, and natural language triggers.
 *
 * Architecture:
 *   Pi Extension ──MCP over HTTP──► Talky MCP Server ──IPC──► Voice Pipeline (WebRTC)
 *                                                                    ▲
 *                                                              Browser connects
 *
 * Installation:
 *   ln -s /path/to/talky/pi-extension ~/.pi/agent/extensions/talky
 *   # or: pi -e /path/to/talky/pi-extension/index.ts
 *
 * The extension:
 *   - Starts Talky MCP server if not running
 *   - Opens browser to WebRTC endpoint for audio
 *   - Registers voice_speak, voice_listen, voice_stop tools
 *   - Injects voice conversation system prompt
 *   - Shows voice status in footer
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const TALKY_MCP_PORT = parseInt(process.env.TALKY_MCP_PORT || "9090", 10);
const TALKY_MCP_URL = `http://localhost:${TALKY_MCP_PORT}/mcp`;
const TALKY_WEBRTC_PORT = parseInt(process.env.TALKY_WEBRTC_PORT || "7860", 10);
const TALKY_WEBRTC_URL = `http://localhost:${TALKY_WEBRTC_PORT}`;

// ---------------------------------------------------------------------------
// Minimal MCP Streamable-HTTP Client
// ---------------------------------------------------------------------------

class MCPClient {
	private url: string;
	private sessionId: string | null = null;
	private nextId = 1;
	private connected = false;

	constructor(url: string) {
		this.url = url;
	}

	/** Check if the MCP server is reachable */
	async isServerUp(): Promise<boolean> {
		try {
			// GET on the MCP endpoint returns 405 or similar if server is up
			// We just need a TCP connection to succeed
			await fetch(this.url, {
				method: "GET",
				headers: { Accept: "text/event-stream" },
				signal: AbortSignal.timeout(3000),
			});
			return true;
		} catch {
			return false;
		}
	}

	/** Perform MCP initialize handshake */
	async connect(): Promise<void> {
		if (this.connected) return;

		// Step 1: Initialize
		await this.request("initialize", {
			protocolVersion: "2025-03-26",
			capabilities: {},
			clientInfo: { name: "pi-talky", version: "1.0.0" },
		});

		// Step 2: Send initialized notification
		await this.notify("notifications/initialized");

		this.connected = true;
	}

	/** Call an MCP tool and return the result */
	async callTool(name: string, args: Record<string, unknown> = {}): Promise<string> {
		if (!this.connected) {
			await this.connect();
		}

		const result = await this.request("tools/call", {
			name,
			arguments: args,
		});

		// MCP tool results have content array
		if (result && Array.isArray(result.content)) {
			return result.content
				.filter((c: any) => c.type === "text")
				.map((c: any) => c.text)
				.join("");
		}

		return String(result);
	}

	/** Send a JSON-RPC request and return the result */
	private async request(method: string, params: Record<string, unknown>): Promise<any> {
		const id = this.nextId++;
		const body = { jsonrpc: "2.0", id, method, params };

		const headers: Record<string, string> = {
			"Content-Type": "application/json",
			Accept: "application/json, text/event-stream",
		};
		if (this.sessionId) {
			headers["mcp-session-id"] = this.sessionId;
		}

		const response = await fetch(this.url, {
			method: "POST",
			headers,
			body: JSON.stringify(body),
			// listen() can block for minutes waiting for speech
			signal: AbortSignal.timeout(10 * 60 * 1000),
		});

		// Extract session ID
		const sid = response.headers.get("mcp-session-id");
		if (sid) this.sessionId = sid;

		const contentType = response.headers.get("content-type") || "";

		if (contentType.includes("text/event-stream")) {
			return this.parseSSEResponse(response);
		}

		const data = (await response.json()) as any;
		if (data.error) {
			throw new Error(data.error.message || JSON.stringify(data.error));
		}
		return data.result;
	}

	/** Parse an SSE response stream to extract the JSON-RPC result */
	private async parseSSEResponse(response: Response): Promise<any> {
		const text = await response.text();
		let lastResult: any = null;
		
		for (const line of text.split("\n")) {
			if (!line.startsWith("data: ")) continue;
			const raw = line.slice(6).trim();
			if (!raw) continue;
			try {
				const msg = JSON.parse(raw);
				if (msg.error) throw new Error(msg.error.message || JSON.stringify(msg.error));
				if (msg.result !== undefined) lastResult = msg.result;
			} catch (e) {
				if (e instanceof SyntaxError) continue;
				throw e;
			}
		}
		
		if (lastResult !== null) return lastResult;
		throw new Error("No result found in SSE response");
	}

	/** Send a JSON-RPC notification (no response expected) */
	private async notify(method: string, params?: Record<string, unknown>): Promise<void> {
		const body: Record<string, unknown> = { jsonrpc: "2.0", method };
		if (params) body.params = params;

		const headers: Record<string, string> = {
			"Content-Type": "application/json",
			Accept: "application/json, text/event-stream",
		};
		if (this.sessionId) {
			headers["mcp-session-id"] = this.sessionId;
		}

		await fetch(this.url, {
			method: "POST",
			headers,
			body: JSON.stringify(body),
		});
	}

	disconnect(): void {
		this.connected = false;
		this.sessionId = null;
	}
}

// ---------------------------------------------------------------------------
// Voice System Prompt
// ---------------------------------------------------------------------------

const VOICE_SYSTEM_PROMPT = `

## Voice Conversation Mode

You are in a live voice conversation. Follow these rules strictly:

### Tools
- voice_speak(text) — Speak to the user via TTS. Keep to 1-2 short sentences.
- voice_listen() — Wait for the user to speak. Returns their transcribed speech.
- voice_stop() — End the voice session. Only call after saying goodbye.

### Conversation Flow
1. When the user asks you to do a task:
   - Acknowledge with voice_speak() (do NOT call voice_listen() yet)
   - Do the work (edit files, run commands, etc.)
   - Call voice_speak() frequently for progress updates — after each significant step
   - Report the result with voice_speak()
   - Only THEN call voice_listen()
2. For simple questions: voice_speak() your answer, then voice_listen()
3. Before destructive changes: voice_speak() to ask confirmation, then voice_listen()
4. To end: say goodbye with voice_speak(), then voice_stop()

### Key Principle
voice_listen() means "I'm done talking and ready for the user." Never call it while you still have work to do or updates to give. Never let more than a few tool calls go by in silence.

### Style
- Brevity is critical. 1-2 sentences max per voice_speak().
- Be conversational and natural.
- If unsure whether the user wants to stop, keep listening.
`;

// ---------------------------------------------------------------------------
// Extension Entry Point
// ---------------------------------------------------------------------------

export default function (pi: ExtensionAPI) {
	const client = new MCPClient(TALKY_MCP_URL);
	let voiceActive = false;
	let talkyProcess: ReturnType<typeof import("node:child_process").spawn> | null = null;

	// -----------------------------------------------------------------------
	// Helpers
	// -----------------------------------------------------------------------

	async function ensureTalkyRunning(): Promise<void> {
		if (await client.isServerUp()) return;

		// Clean up any existing process
		if (talkyProcess) {
			try {
				talkyProcess.kill();
				talkyProcess = null;
			} catch {
				// Process already dead
				talkyProcess = null;
			}
		}

		// Start talky mcp server
		const { spawn } = await import("node:child_process");
		talkyProcess = spawn("talky", ["mcp"], {
			stdio: "ignore",
		});

		// Track process exit to clean up
		talkyProcess.on("exit", () => {
			talkyProcess = null;
		});
		talkyProcess.on("error", (err) => {
			console.error("Failed to start talky mcp server:", err);
			talkyProcess = null;
		});

		// Wait up to 30s for server to come up
		for (let i = 0; i < 30; i++) {
			await new Promise((r) => setTimeout(r, 1000));
			if (await client.isServerUp()) return;
			// Check if process died
			if (!talkyProcess) {
				throw new Error(
					"Talky MCP server process died during startup. Check logs for errors.",
				);
			}
		}

		throw new Error(
			"Talky MCP server failed to start. Ensure 'talky' is installed and in PATH.",
		);
	}

	function openBrowser(url: string): void {
		const { exec } = require("node:child_process") as typeof import("node:child_process");
		
		let command: string;
		if (process.platform === "darwin") {
			command = `open "${url}"`;
		} else if (process.platform === "linux") {
			command = `xdg-open "${url}"`;
		} else if (process.platform === "win32") {
			command = `start "" "${url}"`;
		} else {
			console.warn(`Unsupported platform: ${process.platform}`);
			return;
		}

		exec(command, (error: any) => {
			if (error) {
				console.error(`Failed to open browser: ${error.message}`);
			}
		});
	}

	async function startVoiceSession(ctx: { ui: any; hasUI?: boolean }): Promise<boolean> {
		if (voiceActive) return true;

		if (ctx.hasUI !== false) {
			ctx.ui.setStatus("talky", "🎤 Starting…");
		}

		try {
			await ensureTalkyRunning();
			await client.callTool("start");
			voiceActive = true;

			// Open browser to WebRTC endpoint
			openBrowser(TALKY_WEBRTC_URL);

			if (ctx.hasUI !== false) {
				ctx.ui.setStatus("talky", "🎤 Voice active");
			}
			return true;
		} catch (error) {
			if (ctx.hasUI !== false) {
				ctx.ui.setStatus("talky", undefined);
				ctx.ui.notify(`Failed to start voice: ${error}`, "error");
			}
			return false;
		}
	}

	async function stopVoiceSession(ctx?: { ui: any }): Promise<void> {
		if (!voiceActive) return;
		try {
			await client.callTool("stop");
		} catch {
			// ignore errors during stop
		}
		voiceActive = false;
		client.disconnect();
		if (ctx?.ui) {
			ctx.ui.setStatus("talky", undefined);
		}
	}

	// -----------------------------------------------------------------------
	// /voice command
	// -----------------------------------------------------------------------

	pi.registerCommand("voice", {
		description: "Start a voice conversation with Talky",
		handler: async (_args, ctx) => {
			if (voiceActive) {
				ctx.ui.notify("Voice already active. Say goodbye to stop.", "info");
				return;
			}

			ctx.ui.notify("Starting voice session…", "info");
			const ok = await startVoiceSession(ctx);
			if (!ok) return;

			ctx.ui.notify(
				"Voice ready! Connect in the browser window that just opened.",
				"info",
			);

			// Send a user message that triggers the greeting
			pi.sendUserMessage(
				"A voice conversation session has started. The user is connecting via their browser. " +
					"Greet them briefly with voice_speak(), then call voice_listen() to hear what they want.",
			);
		},
	});

	// -----------------------------------------------------------------------
	// Natural language trigger via input event
	// -----------------------------------------------------------------------

	pi.on("input", async (event, ctx) => {
		if (event.source === "extension") return { action: "continue" as const };
		if (voiceActive) return { action: "continue" as const };

		const text = event.text.toLowerCase();
		const triggers = [
			"voice conversation",
			"voice convo",
			"let's talk",
			"start voice",
			"voice chat",
			"talk to me",
			"i want to talk",
		];

		if (triggers.some((t) => text.includes(t))) {
			const ok = await startVoiceSession(ctx);
			if (!ok) return { action: "continue" as const };

			return {
				action: "transform" as const,
				text:
					"A voice conversation session has started. The user is connecting via their browser. " +
					"Greet them briefly with voice_speak(), then call voice_listen() to hear what they want.",
			};
		}

		return { action: "continue" as const };
	});

	// -----------------------------------------------------------------------
	// Inject voice system prompt when active
	// -----------------------------------------------------------------------

	pi.on("before_agent_start", async (event, _ctx) => {
		if (!voiceActive) return;
		return {
			systemPrompt: event.systemPrompt + VOICE_SYSTEM_PROMPT,
		};
	});

	// -----------------------------------------------------------------------
	// Tools
	// -----------------------------------------------------------------------

	pi.registerTool({
		name: "voice_speak",
		label: "Speak",
		description:
			"Speak text to the user via text-to-speech. Keep messages to 1-2 short sentences for voice.",
		parameters: Type.Object({
			text: Type.String({ description: "Text to speak to the user" }),
		}),
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			if (!voiceActive) {
				const ok = await startVoiceSession(ctx);
				if (!ok) {
					return {
						content: [{ type: "text" as const, text: "Voice not active. Use /voice to start." }],
						details: {},
						isError: true,
					};
				}
			}

			ctx.ui.setStatus("talky", "🔊 Speaking…");
			try {
				await client.callTool("speak", { text: params.text });
				ctx.ui.setStatus("talky", "🎤 Voice active");
				return {
					content: [{ type: "text" as const, text: `Spoke: "${params.text}"` }],
					details: { text: params.text },
				};
			} catch (error) {
				ctx.ui.setStatus("talky", "❌ Voice error");
				return {
					content: [{ type: "text" as const, text: `Failed to speak: ${error}` }],
					details: {},
					isError: true,
				};
			}
		},
	});

	pi.registerTool({
		name: "voice_listen",
		label: "Listen",
		description:
			"Wait for user speech and return the transcribed text. " +
			"Blocks until the user finishes speaking. " +
			"This means 'I am done and ready for the user to talk.'",
		parameters: Type.Object({}),
		async execute(_toolCallId, _params, _signal, _onUpdate, ctx) {
			if (!voiceActive) {
				return {
					content: [{ type: "text" as const, text: "Voice not active. Use /voice to start." }],
					details: {},
					isError: true,
				};
			}

			ctx.ui.setStatus("talky", "👂 Listening…");
			try {
				const text = await client.callTool("listen");
				ctx.ui.setStatus("talky", "🎤 Voice active");
				return {
					content: [{ type: "text" as const, text }],
					details: { text },
				};
			} catch (error) {
				ctx.ui.setStatus("talky", "❌ Voice error");
				return {
					content: [{ type: "text" as const, text: `Failed to listen: ${error}` }],
					details: {},
					isError: true,
				};
			}
		},
	});

	pi.registerTool({
		name: "voice_stop",
		label: "Stop Voice",
		description: "Stop the voice session. Call after saying goodbye.",
		parameters: Type.Object({}),
		async execute(_toolCallId, _params, _signal, _onUpdate, ctx) {
			await stopVoiceSession(ctx);
			return {
				content: [{ type: "text" as const, text: "Voice session ended." }],
				details: {},
			};
		},
	});

	// -----------------------------------------------------------------------
	// Cleanup
	// -----------------------------------------------------------------------

	pi.on("session_shutdown", async () => {
		await stopVoiceSession();
		if (talkyProcess) {
			talkyProcess.kill();
			talkyProcess = null;
		}
	});
}
