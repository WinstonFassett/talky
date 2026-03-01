# Pipeline Comparison: Server vs MCP Server

## Main Server Pipeline

```python
pipeline = Pipeline([
    transport.input(),
    stt,
    user_aggregator,
    llm,                    # ← FULL LLM INTEGRATION
    tts_switcher,           # ← ServiceSwitcher for dynamic TTS
    transport.output(),
    assistant_aggregator,
])
```

## MCP Server Pipeline

```python
pipeline = Pipeline([
    transport.input(),
    screen_capture,         # ← Screen capture branch
    ParallelPipeline(
        [stt, user_aggregator, tts],  # ← Direct TTS, no switching
        [vision],
    ),
    assistant_aggregator,
    transport.output(),
])
```

## Detailed Comparison

### Pipeline Structure

| Component | Main Server | MCP Server | Compatibility |
|-----------|-------------|------------|----------------|
| **Transport** | WebRTC/Daily/WebSocket | WebRTC/Daily/WebSocket | ✅ Compatible |
| **STT** | `create_stt_service_from_config()` | `create_stt_service_from_config()` | ✅ Compatible |
| **User Aggregator** | `LLMUserAggregator` | `LLMUserAggregator` | ✅ Compatible |
| **LLM** | **Full LLM backend** | **NO LLM** | ❌ Incompatible |
| **TTS** | `ServiceSwitcher` (dynamic) | Direct TTS service (static) | ❌ Incompatible |
| **Screen Capture** | Not in main pipeline | Parallel branch | ❌ Different |
| **Vision** | Not in main pipeline | Parallel branch | ❌ Different |
| **Assistant Aggregator** | `LLMAssistantAggregator` | `LLMAssistantAggregator` | ✅ Compatible |

### Key Differences

#### 1. LLM Integration
**Main Server**:
```python
llm = llm_service_class(**backend_config)
# Full conversation pipeline with context management
```

**MCP Server**:
```python
# NO LLM in pipeline
# Claude handles LLM logic via MCP tools
```

#### 2. TTS Switching
**Main Server**:
```python
voice_switcher = VoiceProfileSwitcher(profile_name, pm, None)
tts_switcher = voice_switcher.get_service_switcher()
# Dynamic TTS switching via ServiceSwitcher
```

**MCP Server**:
```python
tts = create_tts_service_from_config(profile.tts_provider, voice_id=profile.tts_voice)
# Static TTS service, no switching capability
```

#### 3. Pipeline Architecture
**Main Server**: Linear pipeline
```
input → stt → user_aggregator → llm → tts_switcher → output → assistant_aggregator
```

**MCP Server**: Parallel pipeline
```
input → screen_capture → ParallelPipeline([stt → user_aggregator → tts], [vision]) → assistant_aggregator → output
```

#### 4. Event Handlers
**Main Server**:
```python
@task.rtvi.event_handler("on_client_ready")
@task.rtvi.event_handler("on_client_message")  # Voice switching
```

**MCP Server**:
```python
@transport.event_handler("on_client_connected")
@transport.event_handler("on_client_disconnected")
# NO RTVI event handlers
```

## Overlap Analysis

### ✅ Compatible Components
1. **Transport Layer** - Both use same WebRTC/Daily/WebSocket transports
2. **STT Service** - Both use same `create_stt_service_from_config()`
3. **User Aggregator** - Both use `LLMUserAggregator` with VAD
4. **Assistant Aggregator** - Both use `LLMAssistantAggregator`

### ❌ Incompatible Components

#### 1. LLM Integration
- **Main Server**: Full LLM backend integration in pipeline
- **MCP Server**: No LLM in pipeline (Claude handles externally)
- **Impact**: Completely different conversation flow

#### 2. TTS Switching
- **Main Server**: Dynamic switching via ServiceSwitcher
- **MCP Server**: Static single TTS service
- **Impact**: No voice profile switching capability

#### 3. Pipeline Structure
- **Main Server**: Linear flow designed for continuous conversation
- **MCP Server**: Parallel flow designed for tool-based interaction
- **Impact**: Different processing patterns

#### 4. Event Handling
- **Main Server**: RTVI event handlers for voice switching
- **MCP Server**: Basic transport event handlers
- **Impact**: No voice switching capability

## Compatibility Assessment

### What Can Be Shared?
1. **VoiceProfileSwitcher class** - Can be reused
2. **TTS service creation** - Same factory functions
3. **Profile management** - Same profile manager
4. **RTVI message handling** - Same event handler patterns

### What Cannot Be Shared?
1. **Pipeline construction** - Different structures
2. **LLM integration** - Different approaches
3. **TTS switching mechanism** - Different implementations
4. **Session management** - Different patterns

## Hybrid Approach Options

### Option 1: Keep Separate, Share Components
```python
# Main server keeps its pipeline
# MCP server keeps its pipeline
# Both share VoiceProfileSwitcher and utilities
```

### Option 2: MCP Server Adopts Main Server Pattern
```python
# MCP server adds LLM to pipeline
# MCP server adds ServiceSwitcher
# MCP server adds RTVI event handlers
# Result: MCP server becomes main server + MCP layer
```

### Option 3: Unified Pipeline with Conditional Logic
```python
# Single pipeline that can operate in two modes
# Mode 1: With LLM (main server)
# Mode 2: Without LLM (MCP server)
# Complex conditional logic throughout
```

## Recommendation

**Option 1** is most practical:
- Keep pipelines separate (they serve different purposes)
- Share VoiceProfileSwitcher and related components
- Add voice switching capability to MCP server
- Maintain architectural clarity

**Option 2** is possible but complex:
- MCP server would need full LLM integration
- Different session management patterns
- Potential conflicts between continuous and tool-based interactions

## Implementation Strategy

### For MCP Server Voice Switching:
1. **Add VoiceProfileSwitcher** to MCP agent
2. **Replace direct TTS** with ServiceSwitcher
3. **Add RTVI event handlers** for voice switching
4. **Keep pipeline structure** otherwise unchanged

### Shared Components:
1. `server/features/voice_switcher.py` → Import in MCP server
2. `shared/profile_manager.py` → Already shared
3. `shared/service_factory.py` → Already shared

### MCP Server Changes:
```python
# Replace static TTS with dynamic switching
voice_switcher = VoiceProfileSwitcher(profile_name, pm, task=None)
tts_switcher = voice_switcher.get_service_switcher()

# Add RTVI event handlers
@task.rtvi.event_handler("on_client_message")
async def handle_voice_message(rtvi, msg):
    await voice_switcher.handle_message(rtvi, msg)
```

## Conclusion

The pipelines are **fundamentally different** and serve different purposes:
- **Main server**: Continuous conversation with full LLM integration
- **MCP server**: Tool-based interaction with external LLM

The best approach is to **keep pipelines separate** but **share voice switching components**. This provides voice switching capability to MCP server without breaking its fundamental architecture.
