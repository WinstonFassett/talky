# CLAUDE.md

## Commands

```bash
uv tool install --editable .          # install/update tool
uv sync                               # dev only (ruff/pyright)
talky mcp                             # start the daemon (listens on 9090)
talky <profile>                       # e.g. `talky openclaw` — switches the daemon's active LLM if daemon is running, otherwise runs a standalone instance
talky profile [<name>]                # explicit daemon profile control (list / switch)
talky kill                            # reclaim ports 9090 / 7860 / 5173
uv run ruff check . && uv run pyright
```

## Architecture

Two paths with different ownership shapes:

- **Daemon (MCP)** — `talky mcp` runs a unified process on `:9090` that embeds the WebRTC handler, serves `client/dist/`, hosts FastMCP tools, and owns the in-process voice pipeline. The pipeline includes an `LLMSwitcher` whose slot contains `MCPDriverLLMService` (null passthrough — the default) plus every configured LLM backend. Switch via `talky profile openclaw` or the shortcut `talky openclaw`. This is the 58db / ea77 / c3a1 architecture.
- **Standalone** — `TALKY_FORCE_STANDALONE=1 talky openclaw` runs `server/bot.py` with the LLM baked into the pipeline, no MCP, no daemon. Mostly a fallback for offline dev — under normal use the daemon path is preferred.

Pipeline shape (daemon path): `Mic → VAD → STT → LLMSwitcher → TTS → Speaker`. The switcher routes frames to whichever LLM is active. `MCPDriverLLMService` consumes `LLMContextFrame` by pushing the latest user message onto the daemon's speech queue (read by `convo_listen`) and passes injected `LLMTextFrame`s through to TTS. Real LLMs (openclaw, moltis, etc.) run inference against their own remote sessions.

- `server/` — Standalone bot (WebRTC), voice daemon (local audio `talky say` / `talky ask`)
- `mcp-server/` — MCP server, in-process voice channel (`channel.py`), FastMCP tools, embedded WebRTC handler
- `skills/talky-skill/` — Agent skill for voice prompt mode + voice conversation

Config: `~/.talky/*.yaml` + `credentials/*.json`. No .env.

LLM backends in `server/backends/` extend Pipecat's `LLMService`.

### Voice daemon

`server/voice_daemon.py` — always-on daemon for local audio TTS+STT. Auto-starts on first `talky say` or `talky ask`. Communicates via unix socket at `/tmp/talky_voice_daemon.sock`.

### MCP server / daemon

`talky mcp` runs a unified process on port 9090. Under 58db the voice pipeline is **in-process** — there is no pipecat child on 7860 any more when running via the daemon. Restart to pick up code changes: `talky kill && talky mcp`. `talky kill` reclaims 9090 (plus 7860 / 5173 if any legacy standalones are still around) via PID-by-port.

**Do not** use `pkill -f "talky mcp"` — it only matches the parent and can orphan children if any standalones are running (see 727e). **Do not** run via `uv run talky mcp` either — the `uv run` wrapper inserts an intermediate process that breaks process-group signal delivery. Run `talky mcp` directly.

If another `talky mcp` is already running on 9090, the new one refuses to start with a clear error. To take over: `talky mcp --force` (or `TALKY_MCP_FORCE=1 talky mcp`).

### Profile switching

When the daemon is running and a browser peer is connected:

```bash
talky profile                  # list available profiles, show active
talky profile openclaw         # switch to openclaw
talky openclaw                 # shortcut: same as `talky profile openclaw` when daemon is up
talky profile __mcp__          # switch to the null MCP passthrough (so an MCP agent can drive via convo_speak)
talky __mcp__                  # shortcut
```

Switching uses `ManuallySwitchServiceFrame` — the transport stays connected, only the active LLM changes. No new pipeline, no peer disconnect. When the daemon is NOT running, `talky openclaw` falls back to the legacy standalone path (spawns its own pipecat) unless `TALKY_FORCE_STANDALONE=1` is set explicitly.

Ports: MCP 9090 (daemon, everything). 7860 / 5173 are only used by legacy standalone runs — avoid.

## Debugging

- Voice daemon: run with `--foreground` to see logs
- MCP server: `talky mcp > /tmp/talky_mcp.log 2>&1 &` to capture logs (run directly — not via `uv run`)
- Check logs first before forming hypotheses

## Conventions

- uv only (no pip/venv)
- YAML config, JSON credentials
- `AGENTS.md` → this file
