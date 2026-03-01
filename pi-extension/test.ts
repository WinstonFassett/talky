/**
 * Integration tests for the Talky Pi Extension.
 *
 * Spins up a mock MCP server that implements the streamable-http protocol,
 * then verifies the MCPClient can connect, call tools, and handle responses.
 *
 * Run: npx tsx test.ts
 */

import { createServer, type Server, type IncomingMessage, type ServerResponse } from "node:http";

// ─── Test harness ────────────────────────────────────────────────────────────

let passed = 0;
let failed = 0;

async function test(name: string, fn: () => Promise<void>) {
	try {
		await fn();
		console.log(`  ✅ ${name}`);
		passed++;
	} catch (error) {
		console.log(`  ❌ ${name}`);
		console.log(`     ${error}`);
		failed++;
	}
}

function assert(cond: boolean, msg: string) {
	if (!cond) throw new Error(`Assertion failed: ${msg}`);
}

function assertEqual<T>(actual: T, expected: T, msg: string) {
	if (actual !== expected) throw new Error(`${msg}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}

// ─── Mock MCP Server ─────────────────────────────────────────────────────────

interface MockState {
	initialized: boolean;
	voiceStarted: boolean;
	lastSpokenText: string;
	speechQueue: string[];
	sessionId: string;
}

function createMockMCPServer(port: number): Promise<{ server: Server; state: MockState }> {
	const state: MockState = {
		initialized: false,
		voiceStarted: false,
		lastSpokenText: "",
		speechQueue: [],
		sessionId: "test-session-" + Date.now(),
	};

	return new Promise((resolve) => {
		const server = createServer((req: IncomingMessage, res: ServerResponse) => {
			// Handle GET (SSE stream endpoint — just return 200 for health check)
			if (req.method === "GET") {
				res.writeHead(200, { "Content-Type": "text/event-stream" });
				res.end();
				return;
			}

			if (req.method !== "POST") {
				res.writeHead(405);
				res.end();
				return;
			}

			let body = "";
			req.on("data", (chunk) => (body += chunk));
			req.on("end", () => {
				try {
					const msg = JSON.parse(body);
					const jsonrpc = "2.0";

					// Handle notifications (no id field)
					if (!msg.id) {
						if (msg.method === "notifications/initialized") {
							state.initialized = true;
						}
						res.writeHead(202);
						res.end();
						return;
					}

					// Handle requests
					res.setHeader("Content-Type", "application/json");
					res.setHeader("mcp-session-id", state.sessionId);

					if (msg.method === "initialize") {
						res.writeHead(200);
						res.end(
							JSON.stringify({
								jsonrpc,
								id: msg.id,
								result: {
									protocolVersion: "2025-03-26",
									capabilities: { tools: {} },
									serverInfo: { name: "talky-mock", version: "1.0.0" },
								},
							}),
						);
						return;
					}

					if (msg.method === "tools/call") {
						const toolName = msg.params?.name;
						const toolArgs = msg.params?.arguments || {};

						let content: { type: string; text: string }[];

						switch (toolName) {
							case "start":
								state.voiceStarted = true;
								content = [{ type: "text", text: "true" }];
								break;
							case "speak":
								if (!state.voiceStarted) {
									content = [{ type: "text", text: "Error: voice not started" }];
									break;
								}
								state.lastSpokenText = toolArgs.text || "";
								content = [{ type: "text", text: "true" }];
								break;
							case "listen":
								if (!state.voiceStarted) {
									content = [{ type: "text", text: "Error: voice not started" }];
									break;
								}
								const speech = state.speechQueue.shift() || "mock user speech";
								content = [{ type: "text", text: speech }];
								break;
							case "stop":
								state.voiceStarted = false;
								content = [{ type: "text", text: "true" }];
								break;
							default:
								res.writeHead(200);
								res.end(
									JSON.stringify({
										jsonrpc,
										id: msg.id,
										error: { code: -32601, message: `Unknown tool: ${toolName}` },
									}),
								);
								return;
						}

						res.writeHead(200);
						res.end(JSON.stringify({ jsonrpc, id: msg.id, result: { content } }));
						return;
					}

					// Unknown method
					res.writeHead(200);
					res.end(
						JSON.stringify({
							jsonrpc,
							id: msg.id,
							error: { code: -32601, message: `Unknown method: ${msg.method}` },
						}),
					);
				} catch (e) {
					res.writeHead(400);
					res.end(JSON.stringify({ error: "Bad request" }));
				}
			});
		});

		server.listen(port, () => resolve({ server, state }));
	});
}

// ─── Mock MCP Server with SSE responses ──────────────────────────────────────

function createMockMCPServerSSE(port: number): Promise<{ server: Server; state: MockState }> {
	const state: MockState = {
		initialized: false,
		voiceStarted: false,
		lastSpokenText: "",
		speechQueue: [],
		sessionId: "test-sse-session-" + Date.now(),
	};

	return new Promise((resolve) => {
		const server = createServer((req: IncomingMessage, res: ServerResponse) => {
			if (req.method === "GET") {
				res.writeHead(200, { "Content-Type": "text/event-stream" });
				res.end();
				return;
			}

			if (req.method !== "POST") {
				res.writeHead(405);
				res.end();
				return;
			}

			let body = "";
			req.on("data", (chunk) => (body += chunk));
			req.on("end", () => {
				try {
					const msg = JSON.parse(body);
					const jsonrpc = "2.0";

					if (!msg.id) {
						if (msg.method === "notifications/initialized") state.initialized = true;
						res.writeHead(202);
						res.end();
						return;
					}

					// Respond with SSE format
					res.setHeader("Content-Type", "text/event-stream");
					res.setHeader("mcp-session-id", state.sessionId);
					res.writeHead(200);

					let result: any;

					if (msg.method === "initialize") {
						result = {
							protocolVersion: "2025-03-26",
							capabilities: { tools: {} },
							serverInfo: { name: "talky-mock-sse", version: "1.0.0" },
						};
					} else if (msg.method === "tools/call") {
						const toolName = msg.params?.name;
						const toolArgs = msg.params?.arguments || {};

						switch (toolName) {
							case "start":
								state.voiceStarted = true;
								result = { content: [{ type: "text", text: "true" }] };
								break;
							case "listen":
								const speech = state.speechQueue.shift() || "sse mock speech";
								result = { content: [{ type: "text", text: speech }] };
								break;
							default:
								result = { content: [{ type: "text", text: "ok" }] };
						}
					}

					// Write SSE event
					const sseData = JSON.stringify({ jsonrpc, id: msg.id, result });
					res.write(`event: message\ndata: ${sseData}\n\n`);
					res.end();
				} catch {
					res.writeHead(400);
					res.end();
				}
			});
		});

		server.listen(port, () => resolve({ server, state }));
	});
}

// ─── Import MCPClient (extract from index.ts for testing) ────────────────────

// Rather than importing from index.ts (which requires Pi types), we duplicate
// the MCPClient class here for testing. In production, it lives in index.ts.

class MCPClient {
	private url: string;
	private sessionId: string | null = null;
	private nextId = 1;
	private connected = false;

	constructor(url: string) {
		this.url = url;
	}

	async isServerUp(): Promise<boolean> {
		try {
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

	async connect(): Promise<void> {
		if (this.connected) return;
		await this.request("initialize", {
			protocolVersion: "2025-03-26",
			capabilities: {},
			clientInfo: { name: "pi-talky-test", version: "1.0.0" },
		});
		await this.notify("notifications/initialized");
		this.connected = true;
	}

	async callTool(name: string, args: Record<string, unknown> = {}): Promise<string> {
		if (!this.connected) await this.connect();
		const result = await this.request("tools/call", { name, arguments: args });
		if (result && Array.isArray(result.content)) {
			return result.content
				.filter((c: any) => c.type === "text")
				.map((c: any) => c.text)
				.join("");
		}
		return String(result);
	}

	private async request(method: string, params: Record<string, unknown>): Promise<any> {
		const id = this.nextId++;
		const headers: Record<string, string> = {
			"Content-Type": "application/json",
			Accept: "application/json, text/event-stream",
		};
		if (this.sessionId) headers["mcp-session-id"] = this.sessionId;

		const response = await fetch(this.url, {
			method: "POST",
			headers,
			body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
			signal: AbortSignal.timeout(10000),
		});

		const sid = response.headers.get("mcp-session-id");
		if (sid) this.sessionId = sid;

		const contentType = response.headers.get("content-type") || "";
		if (contentType.includes("text/event-stream")) {
			return this.parseSSEResponse(response);
		}

		const data = (await response.json()) as any;
		if (data.error) throw new Error(data.error.message || JSON.stringify(data.error));
		return data.result;
	}

	private async parseSSEResponse(response: Response): Promise<any> {
		const text = await response.text();
		for (const line of text.split("\n")) {
			if (!line.startsWith("data: ")) continue;
			const raw = line.slice(6).trim();
			if (!raw) continue;
			try {
				const msg = JSON.parse(raw);
				if (msg.error) throw new Error(msg.error.message);
				if (msg.result !== undefined) return msg.result;
			} catch (e) {
				if (e instanceof SyntaxError) continue;
				throw e;
			}
		}
		throw new Error("No result found in SSE response");
	}

	private async notify(method: string, params?: Record<string, unknown>): Promise<void> {
		const body: Record<string, unknown> = { jsonrpc: "2.0", method };
		if (params) body.params = params;
		const headers: Record<string, string> = {
			"Content-Type": "application/json",
			Accept: "application/json, text/event-stream",
		};
		if (this.sessionId) headers["mcp-session-id"] = this.sessionId;
		await fetch(this.url, { method: "POST", headers, body: JSON.stringify(body) });
	}

	disconnect(): void {
		this.connected = false;
		this.sessionId = null;
	}
}

// ─── Tests ───────────────────────────────────────────────────────────────────

async function runTests() {
	console.log("\n🧪 Talky Pi Extension — Integration Tests\n");

	// ── JSON response tests ──────────────────────────────────────────────

	console.log("  JSON Response Mode:");
	const JSON_PORT = 19090;
	const { server: jsonServer, state: jsonState } = await createMockMCPServer(JSON_PORT);

	try {
		const client = new MCPClient(`http://localhost:${JSON_PORT}/mcp`);

		await test("Server health check", async () => {
			const up = await client.isServerUp();
			assert(up, "Server should be reachable");
		});

		await test("MCP initialize handshake", async () => {
			await client.connect();
			assert(jsonState.initialized, "Server should have received initialized notification");
		});

		await test("Call start tool", async () => {
			const result = await client.callTool("start");
			assertEqual(result, "true", "start should return true");
			assert(jsonState.voiceStarted, "Voice should be started");
		});

		await test("Call speak tool", async () => {
			const result = await client.callTool("speak", { text: "Hello from test" });
			assertEqual(result, "true", "speak should return true");
			assertEqual(jsonState.lastSpokenText, "Hello from test", "Should store spoken text");
		});

		await test("Call listen tool", async () => {
			jsonState.speechQueue.push("user said hello");
			const result = await client.callTool("listen");
			assertEqual(result, "user said hello", "Should return queued speech");
		});

		await test("Call listen tool (default)", async () => {
			const result = await client.callTool("listen");
			assertEqual(result, "mock user speech", "Should return default speech");
		});

		await test("Call stop tool", async () => {
			const result = await client.callTool("stop");
			assertEqual(result, "true", "stop should return true");
			assert(!jsonState.voiceStarted, "Voice should be stopped");
		});

		await test("Call unknown tool returns error", async () => {
			try {
				await client.callTool("nonexistent");
				assert(false, "Should have thrown");
			} catch (e: any) {
				assert(e.message.includes("Unknown tool"), `Error should mention unknown tool: ${e.message}`);
			}
		});

		await test("Full conversation flow", async () => {
			// Start
			await client.callTool("start");
			assert(jsonState.voiceStarted, "Should be started");

			// Speak greeting
			await client.callTool("speak", { text: "Hi there!" });
			assertEqual(jsonState.lastSpokenText, "Hi there!", "Should speak greeting");

			// Listen
			jsonState.speechQueue.push("Help me with code");
			const heard = await client.callTool("listen");
			assertEqual(heard, "Help me with code", "Should hear user");

			// Speak response
			await client.callTool("speak", { text: "Sure, I can help." });

			// Stop
			await client.callTool("stop");
			assert(!jsonState.voiceStarted, "Should be stopped");
		});

		await test("Reconnect after disconnect", async () => {
			client.disconnect();
			// Should auto-reconnect on next call
			const result = await client.callTool("start");
			assertEqual(result, "true", "Should reconnect and call tool");
			assert(jsonState.initialized, "Should re-initialize");
			await client.callTool("stop");
		});
	} finally {
		jsonServer.close();
	}

	// ── SSE response tests ───────────────────────────────────────────────

	console.log("\n  SSE Response Mode:");
	const SSE_PORT = 19091;
	const { server: sseServer, state: sseState } = await createMockMCPServerSSE(SSE_PORT);

	try {
		const client = new MCPClient(`http://localhost:${SSE_PORT}/mcp`);

		await test("SSE: initialize handshake", async () => {
			await client.connect();
			assert(sseState.initialized, "Server should have received initialized notification");
		});

		await test("SSE: call start tool", async () => {
			const result = await client.callTool("start");
			assertEqual(result, "true", "start should return true via SSE");
			assert(sseState.voiceStarted, "Voice should be started");
		});

		await test("SSE: call listen tool", async () => {
			sseState.speechQueue.push("sse hello");
			const result = await client.callTool("listen");
			assertEqual(result, "sse hello", "Should return speech via SSE");
		});

		await test("SSE: call listen default", async () => {
			const result = await client.callTool("listen");
			assertEqual(result, "sse mock speech", "Should return default via SSE");
		});
	} finally {
		sseServer.close();
	}

	// ── Summary ──────────────────────────────────────────────────────────

	console.log(`\n  ${passed} passed, ${failed} failed\n`);
	process.exit(failed > 0 ? 1 : 0);
}

runTests().catch((e) => {
	console.error("Test runner failed:", e);
	process.exit(1);
});
