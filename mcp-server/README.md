# MCP Server

Voice tools for Claude Desktop and other MCP clients with dynamic voice profile switching.

## Features

- **Voice I/O**: Speech-to-text and text-to-speech via MCP tools
- **Dynamic Voice Switching**: Change TTS providers and voices on-the-fly
- **WebRTC Audio**: Low-latency audio connection via browser
- **Multiple TTS Providers**: Support for OpenAI, ElevenLabs, Kokoro, and more

## Install

```bash
cd mcp-server && uv tool install -e .
pipecat-mcp-server  # runs on :9090
```

## Tools

| Tool | Description |
|------|-------------|
| `start()` | Launch voice bot, returns browser URLs |
| `stop()` | Stop voice bot |
| `speak(text)` | TTS to user |
| `listen()` | Wait for speech, return text |

## Voice Profile Switching

The MCP server supports dynamic voice profile switching through RTVI messages:

- **getVoiceProfiles**: List all available voice profiles
- **getCurrentVoiceProfile**: Get current voice profile
- **setVoiceProfile**: Switch to a new voice profile

Voice profiles are configured in `~/.talky/talky-profiles.yaml`.

## Claude Desktop Config

`~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "pipecat": {
      "command": "npx",
      "args": ["-y", "@pyroprompts/mcp-stdio-to-streamable-http-adapter"],
      "env": { "URI": "http://localhost:9090/mcp" }
    }
  }
}
```

Run `pipecat-mcp-server` first, then restart Claude Desktop.

## Usage

1. `start()` → opens `localhost:5173` (Vite client) and `localhost:7860` (WebRTC)
2. Connect browser, allow microphone
3. `speak("Hello")` / `listen()` loop for conversation
4. `stop()` when done

## Architecture

The MCP server uses the same VoiceProfileSwitcher as the main server:

1. **Bootstraps all available TTS services** on startup
2. **Uses ServiceSwitcher** for dynamic TTS provider switching  
3. **Handles RTVI messages** from browser client for voice control

Note: All TTS services are created at startup since ServiceSwitcher doesn't support dynamic service addition.

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

## Development

The MCP server shares the VoiceProfileSwitcher with the main server:

- **Both servers**: Bootstrap all TTS services at startup
- **Voice switching**: Works between pre-bootstrapped services only
- **Limitation**: Cannot dynamically load new TTS providers at runtime

This design ensures reliable voice switching while maintaining consistency between servers.
