# MCP Server Dual Protocol Architecture

## Overview

The MCP server needs to support TWO protocols simultaneously on the same Pipecat pipeline:

1. **MCP Protocol** - For Claude/AI agents
2. **Voice Protocol** - For browser voice client

## Protocol Layers

### Layer 1: MCP Protocol (Port 9090)
**Purpose**: Claude and other AI agents connect via MCP tools

**Current Implementation**:
```python
@mcp.tool()
async def listen() -> str:
    """Listen for user speech and return the transcribed text."""
    
@mcp.tool()
async def speak(text: str) -> bool:
    """Speak the given text to the user using text-to-speech."""
    
@mcp.tool()
async def list_windows() -> list[dict]:
    """List all open windows for screen capture."""
    
@mcp.tool()
async def screen_capture(window_id: int | None = None) -> int | None:
    """Start screen capture to a window."""
    
@mcp.tool()
async def capture_screenshot() -> str:
    """Take a screenshot."""
```

**Flow**: Claude → MCP tools → IPC → Voice Agent → Audio

### Layer 2: Voice Protocol (Port 7860)
**Purpose**: Browser voice client connects for real-time voice interaction

**Missing Implementation**: Needs to be added

**Required Features**:
```python
@task.rtvi.event_handler("on_client_message")
async def handle_voice_message(rtvi, msg):
    """Handle RTVI messages from browser voice client."""
    if msg.type == "getVoiceProfiles":
        # Return available voice profiles
    elif msg.type == "getCurrentVoiceProfile":
        # Return current voice profile
    elif msg.type == "setVoiceProfile":
        # Dynamically switch TTS service using VoiceProfileSwitcher
```

**Flow**: Browser → RTVI frames → VoiceProfileSwitcher → Dynamic TTS switching

## Shared Pipeline Architecture

```
                    ┌─────────────────┐
                    │   MCP Server    │
                    │   (Port 9090)   │
                    └─────────┬───────┘
                              │
                    ┌─────────▼───────┐
                    │  Shared Pipeline │
                    │                 │
                    │  ┌─────────────┐│
                    │  │VoiceProfile ││
                    │  │  Switcher   ││
                    │  └─────────────┘│
                    │                 │
                    │  ┌─────────────┐│
                    │  │   TTS       ││
                    │  │  Switcher   ││
                    │  └─────────────┘│
                    └─────────┬───────┘
                              │
                    ┌─────────▼───────┐
                    │ WebRTC Transport │
                    │   (Port 7860)   │
                    └─────────┬───────┘
                              │
                    ┌─────────▼───────┐
                    │  Browser Client │
                    │   (Port 5173)   │
                    └─────────────────┘
```

## Implementation Requirements

### 1. Add Voice Protocol Support to MCP Server

**File**: `mcp-server/src/pipecat_mcp_server/agent.py`

**Changes Needed**:
```python
class PipecatMCPAgent:
    def __init__(self, transport, runner_args):
        # ... existing init ...
        
        # Add voice profile switcher (reuse from main server)
        from server.features.voice_switcher import VoiceProfileSwitcher
        from shared.profile_manager import get_profile_manager
        
        pm = get_profile_manager()
        profile_name = pm.get_default_voice_profile()
        self.voice_switcher = VoiceProfileSwitcher(profile_name, pm, task=None)
        
    async def start(self):
        # ... existing pipeline setup ...
        
        # Add RTVI event handlers for voice client
        @self._transport.event_handler("on_client_message")
        async def handle_client_message(transport, message):
            await self.voice_switcher.handle_message(rtvi, message)
        
        # Set task reference for voice switcher
        self.voice_switcher.set_task(self._pipeline_task)
```

### 2. Update Pipeline Construction

**Current MCP Pipeline**:
```python
pipeline = Pipeline([
    self._transport.input(),
    self._screen_capture,
    ParallelPipeline(
        [stt, user_aggregator, tts],  # Direct TTS
        [self._vision],
    ),
    assistant_aggregator,
    self._transport.output(),
])
```

**New MCP Pipeline**:
```python
pipeline = Pipeline([
    self._transport.input(),
    self._screen_capture,
    ParallelPipeline(
        [stt, user_aggregator, tts_switcher],  # Use VoiceProfileSwitcher
        [self._vision],
    ),
    assistant_aggregator,
    self._transport.output(),
])
```

### 3. Remove IPC Complexity

**Current Architecture**:
```
MCP Server → IPC Queue → Separate Process → Voice Agent
```

**New Architecture**:
```
MCP Server → Direct Method Calls → Voice Agent (same process)
```

**Benefits**:
- No IPC complexity
- Direct access to voice switcher
- Simpler debugging
- Better performance

### 4. Dual Protocol Handlers

**MCP Protocol Handler** (existing):
```python
@mcp.tool()
async def listen() -> str:
    result = await agent.listen()
    return result["text"]
```

**Voice Protocol Handler** (new):
```python
@task.rtvi.event_handler("on_client_message")
async def handle_voice_message(rtvi, msg):
    await agent.voice_switcher.handle_message(rtvi, msg)
```

## Protocol Interactions

### Claude Session (MCP Protocol)
1. Claude connects to MCP server on port 9090
2. Uses MCP tools: listen(), speak(), screen_capture()
3. Voice switching available via browser client (not Claude)

### Voice Session (Voice Protocol)
1. Browser connects to WebRTC transport on port 7860
2. Sends RTVI frames for voice profile switching
3. Dynamic TTS switching via VoiceProfileSwitcher
4. Claude can continue using MCP tools simultaneously

### Combined Session
1. Claude connected via MCP (port 9090)
2. Browser connected via WebRTC (port 7860)
3. Both operate on same pipeline
4. Voice switching controlled by browser client
5. Audio I/O handled by WebRTC transport

## Technical Considerations

### Task Reference Management
- VoiceProfileSwitcher needs PipelineTask reference for ManuallySwitchServiceFrame
- Must set task reference after pipeline task creation
- Handle task lifecycle properly

### Transport Sharing
- WebRTC transport handles both MCP and voice protocols
- RTVI event handlers must coexist with MCP tool handlers
- Proper message routing between protocols

### State Management
- VoiceProfileSwitcher maintains current voice state
- Both protocols share same TTS service state
- Thread-safe access to voice switcher

### Error Handling
- MCP tool errors shouldn't break voice protocol
- Voice protocol errors shouldn't break MCP tools
- Graceful degradation when one protocol fails

## Migration Strategy

### Phase 1: Remove IPC Complexity
- Move voice agent into MCP server process
- Replace IPC with direct method calls
- Maintain existing MCP tool functionality

### Phase 2: Add Voice Protocol Support
- Import VoiceProfileSwitcher from main server
- Add RTVI event handlers
- Update pipeline to use TTS switcher

### Phase 3: Testing & Integration
- Test MCP tools still work
- Test voice client can switch voices
- Test both protocols simultaneously

## Files to Modify

1. **mcp-server/src/pipecat_mcp_server/agent.py**
   - Add VoiceProfileSwitcher integration
   - Add RTVI event handlers
   - Update pipeline construction

2. **mcp-server/src/pipecat_mcp_server/bot.py**
   - Remove IPC command processing
   - Simplify to direct agent methods

3. **mcp-server/src/pipecat_mcp_server/server.py**
   - Keep MCP tools (no voice profile tools)
   - Remove IPC dependency

4. **mcp-server/src/pipecat_mcp_server/agent_ipc.py**
   - Delete (no longer needed)

## Benefits

1. **Unified Voice Switching**: Same mechanism in both servers
2. **Simplified Architecture**: No IPC complexity
3. **Better Performance**: Direct method calls
4. **Easier Debugging**: Single process
5. **Protocol Separation**: Clean separation of concerns
6. **Code Reuse**: Leverage existing VoiceProfileSwitcher

## Conclusion

The MCP server should support both MCP protocol (for Claude) and voice protocol (for browser client) on the same Pipecat pipeline. This provides unified voice switching capability while maintaining protocol separation.

The key insight is that these are TWO DIFFERENT PROTOCOLS serving TWO DIFFERENT PURPOSES, but they can share the same underlying voice switching infrastructure.
