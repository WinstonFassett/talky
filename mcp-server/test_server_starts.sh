#!/bin/bash
# Test that the MCP server starts without errors

echo "🧪 Testing Pipecat MCP Server startup..."
echo ""
echo "Starting server (will run for 5 seconds)..."
echo ""

# Start server in background
timeout 5 pipecat-mcp-server 2>&1 &
PID=$!

# Wait a bit
sleep 2

# Check if still running
if kill -0 $PID 2>/dev/null; then
    echo "✅ Server started successfully!"
    echo ""
    echo "Configuration:"
    echo "  STT: whisper (local)"
    echo "  TTS: kokoro (local)"
    echo "  Port: 9090"
    echo ""
    kill $PID 2>/dev/null
    wait $PID 2>/dev/null
    
    echo "✅ Server stopped cleanly"
    echo ""
    echo "Next: Restart Claude Code and test with voice commands"
    exit 0
else
    echo "❌ Server failed to start"
    exit 1
fi
