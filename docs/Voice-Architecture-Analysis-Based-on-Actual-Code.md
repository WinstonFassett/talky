# Voice Architecture Analysis - Based on Actual Code Reading

## Current Architecture (Main Branch)

### Main Server Architecture

**File**: `server/bot.py`

**Pipeline Flow**:
```
Browser Client → WebRTC → RTVI Frames → Server Pipeline → LLM → TTS Switcher → Audio Output
```

**Key Components**:

1. **VoiceProfileSwitcher** (`server/features/voice_switcher.py`):
   - Creates ServiceSwitcher with ALL available TTS services at startup
   - Uses `_bootstrap_tts_services()` to create services for all providers with valid credentials
   - Handles RTVI client messages for voice profile switching
   - Uses `ManuallySwitchServiceFrame` to switch TTS providers dynamically

2. **Pipeline Construction** (`server/bot.py` lines 125-135):
```python
pipeline = Pipeline([
    transport.input(),
    stt,
    user_aggregator,
    llm,
    tts_switcher,  # ServiceSwitcher, not direct TTS
    transport.output(),
    assistant_aggregator,
])
```

3. **RTVI Event Handlers** (`server/bot.py` lines 145-159):
   - `on_client_ready`: Adds system messages and greeting
   - `on_client_message`: Delegates to VoiceProfileSwitcher.handle_message()

4. **Voice Profile Switching Flow**:
   - Client sends RTVI message: `getVoiceProfiles`, `getCurrentVoiceProfile`, `setVoiceProfile`
   - VoiceProfileSwitcher handles validation and switching
   - Cross-provider switch: Uses `ManuallySwitchServiceFrame` to switch ServiceSwitcher
   - Same-provider switch: Uses `tts_service.set_voice()` method

### MCP Server Architecture

**File**: `mcp-server/src/pipecat_mcp_server/agent.py`

**Pipeline Flow**:
```
Claude → MCP Tools → Server Pipeline → Single TTS Service → Audio Output
```

**Key Components**:

1. **Simple TTS Service Creation** (`agent.py` lines 305-320):
```python
def _create_voice_services(self) -> tuple[STTService, TTSService]:
    pm = get_profile_manager()
    profile_name = pm.get_default_voice_profile()
    profile = pm.get_voice_profile(profile_name)
    
    if profile:
        stt = create_stt_service_from_config(profile.stt_provider, model=profile.stt_model)
        tts = create_tts_service_from_config(profile.tts_provider, voice_id=profile.tts_voice)
    else:
        stt = create_stt_service_from_config("whisper_local")
        tts = create_tts_service_from_config("kokoro")
    
    return stt, tts
```

2. **Pipeline Construction** (`agent.py` lines 145-158):
```python
pipeline = Pipeline([
    self._transport.input(),
    self._screen_capture,
    ParallelPipeline(
        [stt, user_aggregator, tts],  # Direct TTS, no ServiceSwitcher
        [self._vision],
    ),
    assistant_aggregator,
    self._transport.output(),
])
```

3. **MCP Tools** (`server.py`):
   - `listen()`: Get speech transcription
   - `speak(text)`: Speak text using current TTS service
   - `list_windows()`: Screen capture functionality
   - `screen_capture()`: Start screen capture
   - `capture_screenshot()`: Take screenshot
   - `stop()`: Stop voice session

4. **Command Processing** (`bot.py` lines 60-86):
   - Simple command-response pattern
   - No voice profile switching capability
   - Single TTS service for entire session

## Architectural Differences

### Main Server
- **Dynamic Switching**: ServiceSwitcher allows runtime TTS provider changes
- **RTVI Protocol**: Browser communicates via RTVI frames
- **Full Pipeline**: STT → LLM → TTS with switching capability
- **Eager Bootstrapping**: All TTS services created at startup
- **Client-Driven**: Voice profile changes initiated by browser client

### MCP Server  
- **Static Service**: Single TTS service for entire session
- **MCP Protocol**: Claude communicates via MCP tools
- **Audio Pipeline**: STT → TTS (no LLM, no switching)
- **Lazy Creation**: TTS service created from default profile only
- **Server-Driven**: Voice profile changes would require server restart

## What's Missing in MCP Server

### Voice Profile Management
The MCP server currently has NO voice profile switching capability. To add it:

1. **MCP Tools Needed**:
```python
@mcp.tool()
async def get_voice_profiles() -> list[dict]:
    """Get available voice profiles."""
    
@mcp.tool()
async def get_current_voice_profile() -> dict:
    """Get current voice profile."""
    
@mcp.tool()
async def set_voice_profile(profile_name: str) -> dict:
    """Set voice profile (requires restart/recreate)."""
```

2. **Agent Methods Needed**:
```python
def set_voice_profile(self, profile_name: str) -> Dict[str, Any]:
    """Set voice profile - recreate TTS service."""
    
def get_voice_profiles(self) -> Dict[str, str]:
    """Get available voice profiles."""
    
def get_current_voice_profile(self) -> Dict[str, Any]:
    """Get current voice profile info."""
```

3. **Bot Command Handlers Needed**:
```python
elif cmd == "get_voice_profiles":
    profiles = agent.get_voice_profiles()
    await send_response({"profiles": profiles})
elif cmd == "get_current_voice_profile":
    profile = agent.get_current_voice_profile()
    await send_response({"profile": profile})
elif cmd == "set_voice_profile":
    result = agent.set_voice_profile(request["profile_name"])
    await send_response(result)
```

## Implementation Strategy

### Minimal Changes Only

**What NOT to Change**:
- Main server VoiceProfileSwitcher (it works perfectly)
- Main server pipeline (it works perfectly)
- Main server RTVI handling (it works perfectly)

**What TO Change** (MCP Server Only):

1. **Add Profile Management to Agent**:
```python
def set_voice_profile(self, profile_name: str) -> Dict[str, Any]:
    """Set voice profile - recreate TTS service."""
    pm = get_profile_manager()
    profile = pm.get_voice_profile(profile_name)
    
    if not profile:
        return {"status": "error", "message": f"Profile not found: {profile_name}"}
    
    # Recreate TTS service with new profile
    new_tts = create_tts_service_from_config(profile.tts_provider, voice_id=profile.tts_voice)
    
    # Replace in pipeline (this is the tricky part)
    # May need pipeline restart or service injection
    
    return {"status": "success", "profile": profile_name}
```

2. **Add MCP Tools**:
```python
@mcp.tool()
async def get_voice_profiles() -> list[dict]:
    """Get available voice profiles."""
    result = await send_command("get_voice_profiles")
    return result.get("profiles", [])

@mcp.tool()
async def set_voice_profile(profile_name: str) -> dict:
    """Set voice profile."""
    result = await send_command("set_voice_profile", profile_name=profile_name)
    return result
```

### Technical Challenges

**Pipeline Service Replacement**:
- Pipecat pipelines don't support dynamic service replacement
- May need to restart the entire pipeline for profile changes
- Or implement service injection mechanism

**State Management**:
- Need to track current profile in agent
- Handle profile validation and error cases
- Manage service recreation without breaking audio flow

## Conclusion

The current architecture is sound and working:
- Main server: Complex, dynamic switching via RTVI
- MCP server: Simple, static service via MCP tools

The correct approach is to add MINIMAL voice profile management to the MCP server without touching the main server. No shared modules, no grand unification, just simple MCP tools and agent methods.

The main server's VoiceProfileSwitcher is a sophisticated piece of code that handles dynamic switching correctly. The MCP server needs a much simpler approach suitable for its static pipeline nature.
