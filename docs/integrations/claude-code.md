# Claude Code Integration with Talky

This guide shows how to set up Claude Code to work with Talky's voice capabilities through the talky daemon.

## Architecture

```
Claude Code ──MCP over HTTP──► Talky Daemon (:9090) ──► Voice Pipeline (WebRTC, in-process)
                                        ▲
                                Browser connects for audio at http://localhost:9090
```

The talky daemon is a single process: WebRTC, voice pipeline, browser UI, FastMCP tools — all on :9090. One port, one process.

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

### 2. Start the Talky Daemon

```bash
# Start the daemon (listens on :9090)
talky daemon

# Or let talky claude start it automatically
talky claude
```

### 3. Connect Claude to the Talky Daemon

```bash
# Connect Claude to Talky MCP server
claude mcp add --transport http talky http://localhost:9090/mcp

# Verify connection
claude mcp list
```

### 4. Install Talky Skill

The Talky skill provides voice conversation capabilities. The canonical skill lives at `skills/talky-skill/` in this repo. Install it into Claude Code using `npx skills`:

```bash
npx skills install ./skills/talky-skill
```

Or symlink it manually if you prefer local edits to apply immediately:

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/talky-skill" ~/.claude/skills/talky
```

## Usage

### Quick Start (One Command)

```bash
# This handles MCP server + Claude. Install the skill once via `npx skills` (see step 4 above).
talky claude
```

Then in Claude, say:
```
I want to have a voice conversation
```

**Pre-approved Tools**: The `talky claude` command automatically pre-approves all Talky voice tools, eliminating permission prompts during voice sessions.

### Manual Steps

If you prefer manual setup:

1. **Start MCP server**: `talky daemon`
2. **Install skill**: `npx skills install ./skills/talky-skill` (one-time)
3. **Connect Claude**: `claude mcp add --transport http talky http://localhost:9090/mcp`
4. **Run Claude**: `claude`
5. **Start voice**: "I want to have a voice conversation"

### What Happens

When you start a voice conversation:

1. The Talky skill initializes the voice session
2. A browser window opens for WebRTC audio connection
3. You can talk with Claude through your microphone
4. Claude responds using text-to-speech

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

### Daemon settings

The talky daemon runs on:
- **Port**: 9090
- **MCP endpoint**: `/mcp`
- **Browser UI**: `/` (served from `client/dist/`)
- **Transport**: HTTP (streamable)

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

1. Check if the talky daemon is running: `lsof -i :9090`
2. Restart the daemon: `talky kill && talky daemon`
3. Try again: `talky claude`

### MCP Connection Issues

If Claude can't connect to the daemon:

1. Verify the daemon is running: `curl http://localhost:9090/mcp`
2. Check MCP configuration: `claude mcp list`
3. Remove and re-add: `claude mcp remove talky && claude mcp add --transport http talky http://localhost:9090/mcp`

### Audio Not Working

If you can't hear audio or the microphone isn't working:

1. Check browser permissions for microphone
2. Ensure the talky daemon is running on port 9090
3. Tail `~/.talky/run/mcp-daemon.log` for errors
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

Each project can have its own MCP configuration. The talky daemon will work across all projects once connected.

### Manual daemon management

If you prefer to manage the daemon separately:

```bash
# Start server manually
talky daemon &

# Connect Claude
claude mcp add --transport http talky http://localhost:9090/mcp

# Use Claude normally
claude

# Stop the daemon when done
talky kill
```

## Architecture Details

### Components

1. **Claude Code**: AI coding assistant with MCP support
2. **Talky Daemon**: Single process on :9090 — MCP tools, voice pipeline, WebRTC, browser UI
3. **Browser**: WebRTC client for audio I/O

### Data Flow

1. User speaks → Browser captures audio → WebRTC → in-process voice pipeline
2. Pipeline transcribes → FastMCP tool result → Claude Code
3. Claude Code responds → `convo_speak` → pipeline → TTS
4. TTS → WebRTC → Browser plays audio

### Security

- Daemon runs locally (localhost:9090)
- WebRTC connection is peer-to-peer within your machine
- No audio data leaves your local network
- MCP tools require explicit permission in Claude Code

## Related Documentation

- [Talky Architecture](../architecture.md)
- [MCP Server Documentation](../mcp-server.md)
- [Pi Integration](./pi.md)
- [Voice Configuration](../voice-configuration.md)
