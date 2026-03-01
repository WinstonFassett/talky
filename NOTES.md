# Dev Notes

## `talky claude` command — pre-approved voice tools ✅ IMPLEMENTED

### Goal
Add a `talky claude` CLI subcommand that launches Claude Code with all Talky MCP tools pre-approved, eliminating the per-call permission prompts during voice sessions.

### Implementation ✅

**MCP Server Name**: `pipecat-mcp-server` (from FastMCP initialization)

**Pre-approved Tools**:
```bash
claude --allowedTools "mcp__pipecat-mcp-server__start,mcp__pipecat-mcp-server__speak,mcp__pipecat-mcp-server__listen,mcp__pipecat-mcp-server__stop,mcp__pipecat-mcp-server__list_windows,mcp__pipecat-mcp-server__screen_capture,mcp__pipecat-mcp-server__capture_screenshot"
```

**Implementation Details**:
1. ✅ Added `claude` subcommand to Talky CLI in `talky_cli.py`
2. ✅ `_launch_claude()` launches Claude with pre-approved tools
3. ✅ Automatic skill installation via `_ensure_claude_skill_installed()`
4. ✅ MCP server auto-start via `MCPServerManager.ensure_running()`
5. ✅ Updated documentation with pre-approved tools note

### Usage
```bash
# One command handles everything
talky claude

# Then in Claude:
"I want to have a voice conversation"
```

**Result**: No permission prompts during voice sessions!
