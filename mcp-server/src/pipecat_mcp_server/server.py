#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Pipecat MCP Server for voice I/O.

This server exposes voice tools via the MCP protocol, enabling any MCP client
to have voice conversations with users through a Pipecat pipeline.

Tools:
    start: Initialize and start the voice agent.
    listen: Wait for user speech and return transcribed text.
    speak: Speak text to the user via text-to-speech.
    stop: Gracefully shut down the voice pipeline.
"""

import asyncio
import os
import signal
import sys
from contextlib import asynccontextmanager

from loguru import logger
from mcp.server.fastmcp import FastMCP

from pipecat_mcp_server.agent_ipc import send_command, start_pipecat_process, stop_pipecat_process

logger.remove()
logger.add(sys.stderr, level="INFO")

# Create MCP server
# Host is configurable via MCP_HOST environment variable, defaults to localhost for security
mcp_host = os.getenv("MCP_HOST", "localhost")
mcp_port = int(os.getenv("MCP_PORT", "9090"))
mcp = FastMCP(name="pipecat-mcp-server", host=mcp_host, port=mcp_port)


@mcp.tool()
async def start() -> dict:
    """Start a new Pipecat Voice Agent.

    Once the voice agent has started you can continuously use the listen() and
    speak() tools to talk to the user.

    Returns connection information including the WebRTC browser URL.
    """
    start_pipecat_process()
    
    # Wait for Pipecat to be fully ready with proper async checking
    await _wait_for_pipecat_ready()
    
    # Start Vite client asynchronously
    from .agent_ipc import _start_vite_client
    await _start_vite_client()
    
    # Wait for Vite client to be ready
    await _wait_for_vite_ready()
    
    return {
        "success": True,
        "vite_url": "http://localhost:5173",
        "webrtc_url": "http://localhost:7860",
        "message": "Voice agent started. Connect via Vite client at localhost:5173."
    }


async def _wait_for_pipecat_ready(timeout: int = 30) -> bool:
    """Wait for Pipecat server to be ready with proper timeout."""
    import asyncio
    import socket
    
    start_time = asyncio.get_event_loop().time()
    while (asyncio.get_event_loop().time() - start_time) < timeout:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(('localhost', 7860))
            sock.close()
            
            if result == 0:
                logger.info("Pipecat server is ready on port 7860")
                return True
        except Exception:
            pass
        
        await asyncio.sleep(0.5)  # Check every 500ms
    
    logger.warning("Pipecat server did not become ready within timeout")
    return False


async def _wait_for_vite_ready(timeout: int = 15) -> bool:
    """Wait for Vite client to be ready with proper timeout."""
    import asyncio
    import socket
    
    start_time = asyncio.get_event_loop().time()
    while (asyncio.get_event_loop().time() - start_time) < timeout:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(('localhost', 5173))
            sock.close()
            
            if result == 0:
                logger.info("Vite client is ready on port 5173")
                return True
        except Exception:
            pass
        
        await asyncio.sleep(0.5)  # Check every 500ms
    
    logger.warning("Vite client did not become ready within timeout")
    return False


@mcp.tool()
async def listen() -> str:
    """Listen for user speech and return the transcribed text."""
    result = await send_command("listen")
    return result["text"]


@mcp.tool()
async def speak(text: str) -> bool:
    """Speak the given text to the user using text-to-speech.

    Returns true if the agent spoke the text, false otherwise.
    """
    await send_command("speak", text=text)
    return True




@mcp.tool()
async def stop() -> bool:
    """Stop the voice pipeline and clean up resources.

    Call this when the voice conversation is complete to gracefully
    shut down the voice agent.

    Returns true if the agent was stopped successfully, false otherwise.
    """
    await send_command("stop")
    return True


def signal_handler(signum, frame):
    """Handle SIGTERM and SIGINT signals."""
    logger.info(f"Received signal {signum}, cleaning up...")
    stop_pipecat_process()
    sys.exit(0)


def main():
    """Run the MCP server."""
    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        logger.info("Ctrl-C detected, exiting!")
    finally:
        stop_pipecat_process()


if __name__ == "__main__":
    main()
