#!/usr/bin/env python3
"""Test script to verify MCP server starts and responds to basic commands.

Run this before testing with Claude Code to ensure everything works.
"""

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_mcp_server():
    """Test the MCP server by calling its tools."""
    print("🧪 Testing Pipecat MCP Server...")
    print()

    # Start the MCP server as subprocess
    server_params = StdioServerParameters(command="pipecat-mcp-server", args=[], env=None)

    print("📡 Connecting to MCP server...")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize
            await session.initialize()
            print("✅ Connected!")
            print()

            # List available tools
            print("📋 Available tools:")
            tools = await session.list_tools()
            for tool in tools.tools:
                print(f"  - {tool.name}: {tool.description}")
            print()

            # Test: Start the voice agent
            print("🎤 Testing start()...")
            try:
                result = await session.call_tool("start", {})
                print(f"✅ start() result: {result}")
            except Exception as e:
                print(f"❌ start() failed: {e}")
                return False

            print()
            print("✅ Basic MCP server test passed!")
            print()
            print("Next steps:")
            print("1. Restart Claude Code to load the new MCP server")
            print("2. In Claude, say: 'Let's have a voice conversation'")
            print("3. Claude should call the start() tool")
            print("4. Connect to the transport URL (Daily/WebRTC)")
            print("5. Test listen() and speak() tools")

            return True


if __name__ == "__main__":
    success = asyncio.run(test_mcp_server())
    sys.exit(0 if success else 1)
