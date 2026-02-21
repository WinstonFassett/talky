# Logging Guide

## Overview

This guide covers logging across the entire Talky voice bot system, including the main application, MCP server, and various integrations. Logging has been inconsistent during development, so this document establishes clear patterns and debugging approaches.

## Current Logging State

### What Actually Works Right Now

#### Environment Variable Control
```bash
# This works across ALL components
PIPECAT_LOG_LEVEL=DEBUG talky openclaw
PIPECAT_LOG_LEVEL=INFO talky moltis
PIPECAT_LOG_LEVEL=ERROR talky pi
```

#### Log Levels That Work
- **DEBUG**: Verbose output from Pipecat framework
- **INFO**: Default level, shows basic operation
- **ERROR**: Only error messages
- **WARNING**: Not consistently implemented
- **CRITICAL**: Not consistently implemented

#### What Gets Logged
- **Pipecat framework**: Audio processing, transport, frame handling
- **LLM backends**: Connection status, request/response flow
- **MCP server**: Tool calls, IPC communication
- **Voice services**: STT/TTS processing

### Logging Problems We've Encountered

#### Inconsistent Log Levels
- Some components only respond to `PIPECAT_LOG_LEVEL`
- Others use their own log level systems
- MCP server has separate logging configuration
- LLM backends have different logging patterns

#### Loguru Configuration Issues
- Multiple loggers competing for stdout
- Some components remove all loggers (`logger.remove()`)
- Inconsistent format across components
- Color codes lost in some contexts

#### Missing Important Information
- No clear indication of which backend is active
- Limited visibility into MCP tool execution
- Poor error context for connection issues
- No structured logging for debugging

## Component-by-Component Logging

### Main Talky Application

#### Current Implementation
```python
# In server/main.py - command line argument handling
if args.minimal:
    from logging_config import setup_minimal_logging
    setup_minimal_logging()
elif args.quiet:
    from logging_config import configure_logging  # MISSING FUNCTION!
    configure_logging()
elif args.debug:
    from logging_config import setup_debug_logging
    setup_debug_logging()
elif args.log_level:
    from logging_config import configure_logging  # MISSING FUNCTION!
    configure_logging()
else:
    from logging_config import setup_essential_logging
    setup_essential_logging()
```

#### What Actually Exists
```python
# In server/logging_config.py
def setup_essential_logging()      # Default - WARNING and above, filtered
def setup_minimal_logging()        # ERROR only  
def setup_debug_logging()          # DEBUG - everything (noisy)
# MISSING: configure_logging()     # Referenced but doesn't exist!
```

#### What Works
```bash
# These work through command line arguments
talky --debug                    # Uses setup_debug_logging
talky --minimal                  # Uses setup_minimal_logging
talky --quiet                    # Tries configure_logging (BROKEN)
talky --log-level INFO          # Tries configure_logging (BROKEN)

# Default uses setup_essential_logging
talky openclaw                   # Uses setup_essential_logging
```

#### Problems
- **Missing function**: `configure_logging()` doesn't exist but is referenced
- **Inconsistent argument handling**: Some args work, others crash
- **No environment variable support**: Command line only

### MCP Server

#### Current Implementation
```python
# In mcp-server/src/pipecat_mcp_server/server.py
logger.remove()
logger.add(sys.stderr, level="INFO")
```

#### What Works
- Hardcoded to INFO level
- Shows MCP tool calls and responses
- IPC communication logging

#### Problems
- Ignores `PIPECAT_LOG_LEVEL` environment variable
- Hardcoded logger removal breaks other logging
- No way to increase verbosity for debugging

### LLM Backends

#### OpenClaw Backend
```python
# Uses loguru with environment variable support
from loguru import logger
# Responds to PIPECAT_LOG_LEVEL
logger.info(f"📤 Sending to OpenClaw: {request_data}")
```

#### Moltis Backend
```python
# Similar to OpenClaw
from loguru import logger
# Responds to PIPECAT_LOG_LEVEL
logger.info(f"🔌 Connecting to Moltis at {gateway_url}")
```

#### Pi Backend
```python
# Uses loguru
from loguru import logger
# Responds to PIPECAT_LOG_LEVEL
logger.info(f"🚀 Starting pi subprocess: {self.pi_binary}")
```

## Debugging Scenarios

### Scenario 1: "Nothing Happens When I Start"

#### What to Check
```bash
# Enable full debug logging
PIPECAT_LOG_LEVEL=DEBUG talky openclaw

# Look for these specific messages:
# - "🔌 Connecting to OpenClaw"
# - "✅ Connected to OpenClaw" 
# - "📤 Sending to OpenClaw"
# - WebSocket connection errors
```

#### Common Issues
- OpenClaw gateway not running
- Wrong gateway URL
- Authentication token issues
- Network connectivity problems

### Scenario 2: "I Speak But Nothing Happens"

#### What to Check
```bash
PIPECAT_LOG_LEVEL=DEBUG talky moltis

# Look for:
# - VAD (Voice Activity Detection) events
# - STT (Speech-to-Text) processing
# - LLM request/response flow
# - TTS (Text-to-Speech) initiation
```

#### Common Issues
- Microphone permissions
- STT service API key problems
- Audio transport issues
- VAD not detecting speech

### Scenario 3: "MCP Tools Don't Work"

#### What to Check
```bash
# MCP server has hardcoded INFO level
# Need to check server logs directly
pipecat-mcp-server

# Look for:
# - Tool registration
# - IPC communication
# - Voice agent process status
```

#### Common Issues
- MCP server not running
- Voice agent process crashed
- IPC communication broken
- WebRTC connection issues

### Logging Improvements Needed

### Immediate Fixes

#### 1. Fix Missing configure_logging Function
```python
# Add to server/logging_config.py
def configure_logging():
    """Standard logging - respects LOG_LEVEL environment variable."""
    import os
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    
    # Remove all loguru handlers
    logger.remove()
    
    # Add handler with specified level
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <cyan>{extra[absolute_path]}:{line}</cyan> - <level>{message}</level>",
        level=log_level,
        filter=lambda record: _pipecat_filter(record),
        colorize=True,
    )
```

#### 2. Standardize Environment Variable Usage
```python
# All components should respect this
import os
LOG_LEVEL = os.getenv("PIPECAT_LOG_LEVEL", "INFO")

# Instead of hardcoded levels
logger.add(sys.stderr, level=LOG_LEVEL)
```

#### 3. Stop Removing Loggers
```python
# BAD: This breaks other logging
logger.remove()
logger.add(sys.stderr, level="INFO")

# GOOD: Configure without removing
if not logger._core.handlers:
    logger.add(sys.stderr, level=LOG_LEVEL)
```

#### 4. Add Component Identification
```python
# Add component prefix to all messages
COMPONENT = "moltis-backend"
logger.info(f"[{COMPONENT}] 🔌 Connecting to Moltis")
```

### Medium-term Improvements

#### 1. Structured Logging
```python
# Add consistent context
logger.bind(
    component="openclaw",
    request_id=request_id,
    user_id=user_id
).info("Sending request to OpenClaw")
```

#### 2. Log Level Hierarchy
```python
# Define clear log level usage
logger.debug("Detailed frame-by-frame info")
logger.info("Important state changes")
logger.warning("Recoverable errors")
logger.error("Serious problems")
logger.critical("System failures")
```

#### 3. Performance Logging
```python
# Add timing information
import time
start = time.time()
# ... do something ...
logger.info(f"Operation completed in {time.time() - start:.2f}s")
```

## Current Workarounds

### For MCP Server Debugging
```bash
# Currently hardcoded to INFO, no easy way to debug
# Workaround: Check source code for debug prints
# Or modify the source temporarily

# In mcp-server/src/pipecat_mcp_server/server.py
# Change: logger.add(sys.stderr, level="INFO")
# To: logger.add(sys.stderr, level="DEBUG")
```

### For Application-Level Issues
```bash
# Some application logging doesn't respect PIPECAT_LOG_LEVEL
# Workaround: Check individual component logs

# Check specific backend logs
PIPECAT_LOG_LEVEL=DEBUG talky openclaw 2>&1 | grep "OpenClaw"
```

### For Voice Pipeline Issues
```bash
# Enable debug and filter for audio-related logs
PIPECAT_LOG_LEVEL=DEBUG talky moltis 2>&1 | grep -E "(VAD|STT|TTS|audio)"
```

## Development Logging Best Practices

### DO
- Use `PIPECAT_LOG_LEVEL` environment variable
- Include component identification in messages
- Use appropriate log levels (DEBUG for details, INFO for state changes)
- Add context (request IDs, timing, etc.)
- Use emoji for quick visual scanning (🔌, 📤, 📨, 🤖)

### DON'T
- Remove all loggers (`logger.remove()`)
- Hardcode log levels when environment variable exists
- Log sensitive information (API keys, personal data)
- Use print() statements instead of proper logging
- Log in tight loops without rate limiting

## Future Logging Strategy

### Phase 1: Standardization (Immediate)
- Make all components respect `PIPECAT_LOG_LEVEL`
- Stop removing loggers
- Add component identification
- Fix MCP server logging

### Phase 2: Structure (Short-term)
- Implement structured logging with context
- Add timing and performance metrics
- Create log filtering capabilities
- Add log rotation for long-running processes

### Phase 3: Observability (Medium-term)
- Add OpenTelemetry integration
- Implement log aggregation
- Create debugging dashboards
- Add alerting for critical issues

## Quick Reference

### Environment Variables
```bash
# Main logging control
PIPECAT_LOG_LEVEL=DEBUG    # Most verbose
PIPECAT_LOG_LEVEL=INFO     # Default
PIPECAT_LOG_LEVEL=WARNING  # Warnings and errors only
PIPECAT_LOG_LEVEL=ERROR    # Errors only

# Backend-specific (if implemented)
OPENCLAW_LOG_LEVEL=DEBUG
MOLTIS_LOG_LEVEL=DEBUG
PI_LOG_LEVEL=DEBUG
```

### Common Debugging Commands
```bash
# Full debug for OpenClaw
PIPECAT_LOG_LEVEL=DEBUG talky openclaw

# Check MCP server status
pipecat-mcp-server

# Filter for specific issues
PIPECAT_LOG_LEVEL=DEBUG talky moltis 2>&1 | grep -i error

# Timing information
PIPECAT_LOG_LEVEL=DEBUG talky pi 2>&1 | grep "completed in"
```

### What to Look For
- **Connection issues**: "🔌", "connecting", "websocket", "auth"
- **Request flow**: "📤", "📨", "request", "response"
- **Audio processing**: "VAD", "STT", "TTS", "audio"
- **Errors**: "ERROR", "failed", "exception", "timeout"

## Getting Help

When reporting issues, include:
1. Full command with environment variables
2. Complete log output (not just snippets)
3. What you expected to happen
4. What actually happened
5. System information (OS, Python version, etc.)

Example:
```bash
PIPECAT_LOG_LEVEL=DEBUG talky openclaw > debug.log 2>&1
# Then share debug.log
```
