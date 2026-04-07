# CLAUDE.md

## Commands

```bash
uv tool install --editable .          # install/update tool
uv sync                               # dev only (ruff/pyright)
talky --profile moltis [--debug]
talky mcp
uv run ruff check . && uv run pyright
```

## Architecture

`Mic → VAD → STT → LLM → TTS → Speaker`

- `server/` — Standalone bot (WebRTC), voice daemon (local audio)
- `mcp-server/` — MCP server with local audio tools + browser conversation tools
- `skills/talky-skill/` — Agent skill for voice prompt mode + voice conversation

Config: `~/.talky/*.yaml` + `credentials/*.json`. No .env.

LLM backends in `server/backends/` extend Pipecat's `LLMService`.

### Voice daemon

`server/voice_daemon.py` — always-on daemon for local audio TTS+STT. Auto-starts on first `talky say` or `talky ask`. Communicates via unix socket at `/tmp/talky_voice_daemon.sock`.

### MCP server

`talky mcp` runs on port 9090. **Must be restarted after code changes** — the pipecat child (port 7860) inherits code from server start time. Restart: `talky kill && talky mcp`. `talky kill` reclaims 9090 and 7860 via the `~/.talky/run/pipecat.pid` file (pgid-accurate) with an lsof-by-port fallback.

**Do not** use `pkill -f "talky mcp"` — it only matches the parent and leaves the pipecat child orphaned on 7860 (see 727e). **Do not** run via `uv run talky mcp` either — the `uv run` wrapper inserts an intermediate process that breaks process-group signal delivery. Run `talky mcp` directly.

If another `talky mcp` is already running on 9090, the new one will refuse to start with a clear error. To take over: `talky mcp --force` (or `TALKY_MCP_FORCE=1 talky mcp`).

Ports: MCP 9090, Pipecat WebRTC 7860 (internal, proxied through 9090). Vite 5173 is only spawned by the standalone `talky <profile>` path, not by `talky mcp` (the static client is served from `client/dist/`).

## Debugging

- Voice daemon: run with `--foreground` to see logs
- MCP server: `talky mcp > /tmp/talky_mcp.log 2>&1 &` to capture logs (run directly — not via `uv run`)
- Check logs first before forming hypotheses

## Conventions

- uv only (no pip/venv)
- YAML config, JSON credentials
- `AGENTS.md` → this file
