# MCP Server

Voice tools for Claude Desktop and other MCP clients.

## Install

```bash
cd mcp-server && uv tool install -e .
pipecat-mcp-server  # runs on :9090
```

## Tools

| Tool | Description |
|------|-------------|
| `start()` | Launch voice bot, returns browser URL |
| `stop()` | Stop voice bot |
| `speak(text)` | TTS to user |
| `listen()` | Wait for speech, return text |

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

1. `start()` → opens `localhost:7860/client`
2. Connect browser, allow mic
3. `speak("Hello")` / `listen()` loop
4. `stop()` when done
