# CLAUDE.md

## Commands

```bash
uv sync && uv tool install talky -e .
talky --profile moltis [--debug]
talky mcp
uv run ruff check . && uv run pyright
```

## Architecture

`Mic → VAD → STT → LLM → TTS → Speaker`

- `server/` — Standalone bot (WebRTC)
- `mcp-server/` — Claude Desktop (FastMCP)

Config: `~/.talky/*.yaml` + `credentials/*.json`. No .env.

LLM backends in `server/backends/` extend Pipecat's `LLMService`.

## Conventions

- uv only (no pip/venv)
- YAML config, JSON credentials
- `AGENTS.md` → this file
