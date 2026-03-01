# Claude Code Integration with Talky

This guide shows how to set up Claude Code to work with Talky's voice capabilities through the Talky MCP server.

## Architecture

```
Claude Code ──MCP over HTTP──► Talky MCP Server ──IPC──► Voice Pipeline (WebRTC)
                                                    ▲
                                            Browser connects for audio
```

## Setup Steps

### 1. Install Claude Code

```bash
# Install Claude Code CLI
curl -fsSL https://claude.ai/install.sh | bash
# or
npm install -g @anthropic-ai/claude-code

# Verify installation
claude --version
```

### 2. Start Talky MCP Server

```bash
# Start the MCP server (runs in background)
talky mcp

# Or let talky claude start it automatically
talky claude
```

### 3. Connect Claude to Talky MCP Server

```bash
# Connect Claude to Talky MCP server
claude mcp add --transport http talky http://localhost:9090/mcp

# Verify connection
claude mcp list
```

### 4. Install Talky Skill

The Talky skill provides voice conversation capabilities:

```bash
# Skill should be at ~/.claude/skills/talky/SKILL.md
# Created automatically by talky claude command
```

## Usage

### Starting a Voice Conversation

In Claude Code, simply say:

```
I want to have a voice conversation
```

This will:
1. Start the voice session using the Talky skill
2. Open a browser window for WebRTC audio connection
3. Allow you to talk with Claude through your microphone

### Voice Tools Available

Once connected, you can use these voice tools:

- `voice_speak(text)` - Speak text to the user
- `voice_listen()` - Listen for user speech and return transcribed text  
- `voice_stop()` - Stop the voice session

### Example Conversation

```
User: I want to have a voice conversation

Claude: 🎤 Starting voice session...
• Connect via browser (WebRTC)
• First connection may take a moment for model downloads
• Check terminal for errors if audio doesn't work

voice_speak("Hey! I'm Claude. What can I help you with today?")
voice_listen()

User: [speaks] "Can you help me debug this Python code?"

Claude: voice_speak("I'd be happy to help you debug your Python code!")
voice_listen()
```

## Configuration

### MCP Server Settings

The Talky MCP server runs on:
- **Port**: 9090
- **Endpoint**: `/mcp`
- **Transport**: HTTP

### Voice Profile

You can specify a voice profile:

```bash
# Use custom voice profile
talky claude --voice-profile custom-profile

# Or configure in ~/.talky/talky-profiles.yaml
claude:
  backend: "mcp"
  app: "claude"
  voice_profile: "custom-profile"
```

### Working Directory

Specify the working directory for Claude:

```bash
talky claude --dir /path/to/project
```

## Troubleshooting

### Voice Agent Process Stopped

If you see "Voice agent process has stopped":

1. Check if MCP server is running: `lsof -i :9090`
2. Check if WebRTC server is running: `lsof -i :7860`
3. Restart MCP server: `talky mcp`
4. Try again: `talky claude`

### MCP Connection Issues

If Claude can't connect to the MCP server:

1. Verify server is running: `curl http://localhost:9090/mcp`
2. Check MCP configuration: `claude mcp list`
3. Remove and re-add: `claude mcp remove talky && claude mcp add --transport http talky http://localhost:9090/mcp`

### Audio Not Working

If you can't hear audio or microphone isn't working:

1. Check browser permissions for microphone
2. Ensure WebRTC server is running on port 7860
3. Check terminal for error messages from MCP server
4. Try refreshing the browser window

### Skill Not Found

If the `/talky` skill isn't available:

1. Verify skill exists: `ls ~/.claude/skills/talky/`
2. Restart Claude Code
3. Check skill loading: `claude /skills`

## Advanced Usage

### Custom Voice Prompts

You can modify the Talky skill at `~/.claude/skills/talky/SKILL.md` to customize:
- Voice conversation flow
- Progress update frequency
- Response style for voice interactions

### Multiple Projects

Each project can have its own MCP configuration. The Talky MCP server will work across all projects once connected.

### Manual MCP Server Management

If you prefer to manage the MCP server separately:

```bash
# Start server manually
talky mcp &

# Connect Claude
claude mcp add --transport http talky http://localhost:9090/mcp

# Use Claude normally
claude

# Stop server when done
pkill -f "talky mcp"
```

## Architecture Details

### Components

1. **Claude Code**: AI coding assistant with MCP support
2. **Talky MCP Server**: Exposes voice tools via MCP protocol
3. **Voice Pipeline**: WebRTC-based audio processing (TTS/STT)
4. **Browser**: WebRTC client for audio I/O

### Data Flow

1. User speaks → Browser captures audio → WebRTC → Voice Pipeline
2. Voice Pipeline transcribes → MCP Server → Claude Code
3. Claude Code responds → MCP Server → Voice Pipeline
4. Voice Pipeline synthesizes → WebRTC → Browser plays audio

### Security

- MCP server runs locally (localhost:9090)
- WebRTC connection is peer-to-peer
- No audio data leaves your local network
- MCP tools require explicit permission in Claude Code

## Related Documentation

- [Talky Architecture](../architecture.md)
- [MCP Server Documentation](../mcp-server.md)
- [Pi Integration](./pi.md)
- [Voice Configuration](../voice-configuration.md)
