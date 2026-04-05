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

`talky mcp` runs on port 9090. **Must be restarted after code changes** — the child process (Pipecat on 7860) inherits code from server start time. Restart: `pkill -f "talky mcp"`, then `uv run talky mcp`.

Ports: MCP 9090, Pipecat WebRTC 7860, Vite 5173. Stale processes can block ports — check with `lsof -ti:PORT`.

## Debugging

- Voice daemon: run with `--foreground` to see logs
- MCP server: `uv run talky mcp > /tmp/talky_mcp.log 2>&1` to capture logs
- Check logs first before forming hypotheses

## Conventions

- uv only (no pip/venv)
- YAML config, JSON credentials
- `AGENTS.md` → this file
