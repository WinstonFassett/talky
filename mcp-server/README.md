# Talky Daemon (FastMCP + WebRTC)

The talky daemon on `:9090` is the unified server: embedded WebRTC handler, static client UI (`client/dist/`), HTTP control plane, FastMCP SSE mount, and the in-process voice pipeline. MCP tools are one feature of the daemon, not the daemon.

## Features

- **In-process voice pipeline** — `Mic → VAD → STT → LLMSwitcher → TTS → Speaker` on uvicorn's event loop (58db).
- **LLMSwitcher** — one persistent pipeline with `MCPDriverLLMService` + configured backends as peers. Switching is a single `ManuallySwitchServiceFrame`; transport stays connected (ea77 / c3a1).
- **Voice profile switching** — dynamic TTS provider/voice swap via RTVI.
- **Room persistence** — pipeline survives browser disconnect / reconnect (3f12 phase 2); `join_convo` / `request_leave` for explicit agent membership (3f12 phase 1, 0b80).
- **Profile switching across the CLI + browser boundary** — `talky openclaw` from the terminal swaps the active LLM in a live browser session.

## Run

```bash
talky daemon              # ensure the daemon is running
talky kill                # reclaim :9090
talky daemon --force      # take over :9090 if another daemon is running
```

Any daemon-dependent CLI (`talky profile`, `talky openclaw`, etc.) auto-spawns the daemon if it isn't already running (9d02 / `ensure_mcp_daemon()`).

## MCP Tools

| Tool | Description |
|------|-------------|
| `start_convo()` | Start a voice conversation session |
| `convo_speak(text)` | Inject assistant text into the conversation |
| `convo_listen()` | Wait for user speech, return transcript |
| `join_convo(agent_id)` | Claim the room as a driver before speaking / listening |
| `request_leave(agent_id, grace_seconds=4)` | Polite exit: signoff cue + grace window; returns `user_interrupted: true` if the user speaks up during the window (ticket 0b80) |
| `say_local_audio(text)` / `ask_local_audio(text)` | Local-audio walkie-talkie path (routes to the separate voice daemon) |

There is intentionally no agent-facing tool to tear down the pipeline. Use `talky kill` from the CLI or close the browser tab if a full reset is needed.

## Claude Desktop config

`~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "talky": {
      "command": "npx",
      "args": ["-y", "@pyroprompts/mcp-stdio-to-streamable-http-adapter"],
      "env": { "URI": "http://localhost:9090/mcp" }
    }
  }
}
```

The daemon auto-spawns on first CLI invocation; you don't need to start it manually before launching Claude Desktop.

## Usage

1. First `talky daemon` or daemon-dependent command ensures the daemon is running on :9090.
2. Browser opens at `http://localhost:9090` (served by the daemon itself).
3. Connect mic, drive the conversation from either the browser UI or from an MCP tool caller.
4. `talky kill` reclaims the port.

## Architecture

The daemon uses `LLMSwitcher` holding `MCPDriverLLMService` + configured LLM backends. Profile switch is a single `ManuallySwitchServiceFrame` — no pipeline rebuild, no peer disconnect. TTS profile switch uses a parallel `VoiceProfileSwitcher`. All TTS services are bootstrapped at startup since `ServiceSwitcher` doesn't support dynamic service addition.

## Configuration

Voice profiles are defined in `~/.talky/talky-profiles.yaml`:

```yaml
voice_profiles:
  default:
    description: "Default voice profile"
    stt_provider: "whisper_local"
    stt_model: "base"
    tts_provider: "kokoro"
    tts_voice: "default"

  openai:
    description: "OpenAI voice"
    stt_provider: "openai"
    stt_model: "whisper-1"
    tts_provider: "openai"
    tts_voice: "alloy"
```
