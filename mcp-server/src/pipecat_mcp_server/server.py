#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Pipecat MCP Server for voice I/O.

This server exposes voice tools via the MCP protocol, enabling any MCP client
to interact with the user by voice.

Local audio tools (daemon-backed, no browser):
    say_local_audio: Speak text through local speakers.
    ask_local_audio: Speak text, then listen for a spoken response.

Conversation tools (browser pipeline, WebRTC):
    start_convo: Start a full voice conversation with browser UI.
    convo_speak: Speak text within an active conversation.
    convo_listen: Listen for user speech within an active conversation.
    end_convo: End the voice conversation and clean up.
"""

import asyncio
import os
import signal
import sys
import webbrowser
from pathlib import Path

from loguru import logger
from mcp.server.fastmcp import FastMCP

from pipecat_mcp_server.agent_ipc import send_command, start_pipecat_process, stop_pipecat_process
from pipecat_mcp_server.daemon_bridge import ask as daemon_ask
from pipecat_mcp_server.daemon_bridge import say as daemon_say

logger.remove()
logger.add(sys.stderr, level="INFO")

# Create MCP server
# Host is configurable via MCP_HOST environment variable, defaults to localhost for security
mcp_host = os.getenv("MCP_HOST", "localhost")
mcp_port = int(os.getenv("MCP_PORT", "9090"))
mcp = FastMCP(name="pipecat-mcp-server", host=mcp_host, port=mcp_port)


# ──────────────────────────────────────────────────────────────────────────────
# Local audio tools (daemon-backed, no browser needed)
# ──────────────────────────────────────────────────────────────────────────────


@mcp.tool()
async def say_local_audio(text: str) -> dict:
    """Speak text through the user's local speakers. No browser needed.

    Uses the voice daemon for instant TTS playback via local audio output.
    The daemon auto-starts if not already running.

    Args:
        text: The text to speak aloud.

    Returns:
        Dict with success status and audio info.

    """
    return await daemon_say(text)


@mcp.tool()
async def ask_local_audio(text: str, silence_timeout: float = 10.0) -> dict:
    """Speak text through local speakers, then listen for the user's spoken response.

    Uses local audio (speakers + microphone) via the voice daemon. No browser needed.
    The daemon auto-starts if not already running. Returns the transcribed response.
    Turn detection handles knowing when the user is done talking — no hard time limit.

    Args:
        text: The text to speak before listening.
        silence_timeout: Seconds of no speech at all before giving up (default: 10).

    Returns:
        Dict with success status and transcript of user's response.

    """
    return await daemon_ask(text, silence_timeout=silence_timeout)


# ──────────────────────────────────────────────────────────────────────────────
# Conversation tools (browser pipeline, WebRTC)
# ──────────────────────────────────────────────────────────────────────────────


@mcp.tool()
async def start_convo(auto_open: bool = True) -> dict:
    """Start a full voice conversation with browser UI and WebRTC audio.

    Launches a Pipecat voice pipeline. The frontend is served on the same
    port as the MCP server, with WebRTC signaling proxied through.

    Once started, use convo_speak() and convo_listen() to interact.

    Args:
        auto_open: Automatically open the browser to the WebRTC client (default: True).

    Returns connection information including the browser URL.

    """
    start_pipecat_process()

    # Wait for Pipecat to be fully ready with proper async checking
    await _wait_for_pipecat_ready()

    scheme = "https" if os.getenv("MCP_SSL_CERTFILE") else "http"
    client_url = f"{scheme}://{mcp_host}:{mcp_port}?autoconnect=true"

    if auto_open:
        webbrowser.open(client_url)

    return {
        "success": True,
        "client_url": client_url,
        "message": f"Voice conversation started. Browser opened to {client_url}."
    }


async def _wait_for_pipecat_ready(timeout: int = 30) -> bool:
    """Wait for Pipecat server to be ready with proper timeout."""
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

        await asyncio.sleep(0.5)

    logger.warning("Pipecat server did not become ready within timeout")
    return False



@mcp.tool()
async def convo_speak(text: str) -> bool:
    """Speak text within an active browser conversation.

    Requires start_convo() to have been called first.

    Args:
        text: The text to speak.

    Returns:
        True if the agent spoke the text, false otherwise.

    """
    await send_command("speak", text=text)
    return True


@mcp.tool()
async def convo_listen() -> dict:
    """Listen for user speech within an active browser conversation.

    Blocks until the user speaks, then returns all buffered speech.
    Requires start_convo() to have been called first.

    Returns:
        Dict with 'text' (combined transcription) and 'segments' (list of
        utterances with timestamps for gap/silence awareness).

    """
    return await send_command("listen")


@mcp.tool()
async def end_convo() -> bool:
    """End the active browser voice conversation and clean up resources.

    Shuts down the Pipecat pipeline.

    Returns:
        True if the conversation was ended successfully.

    """
    await send_command("stop")
    return True


def signal_handler(signum, frame):
    """Handle SIGTERM and SIGINT signals."""
    logger.info(f"Received signal {signum}, cleaning up...")
    stop_pipecat_process()
    sys.exit(0)


def _build_app():
    """Build the unified Starlette app with MCP, proxy, and static files.

    Route layout:
        POST /start          → proxy → Pipecat :7860 (session init)
        POST/PATCH /api/offer → proxy → Pipecat :7860 (WebRTC signaling)
        ALL  /mcp            → FastMCP (MCP protocol, streamable-http)
        GET  /*              → static files (client/dist/, production only)
    """
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles

    from starlette.staticfiles import StaticFiles

    from pipecat_mcp_server.proxy import proxy_routes

    # Build the MCP Starlette app — it has a single Route("/mcp", ...) and a
    # lifespan that manages the StreamableHTTP session manager.
    # We inject our proxy routes and static files directly into its route list
    # so everything shares one Starlette app (and its lifespan).
    mcp_starlette = mcp.streamable_http_app()

    # Determine client dist directory
    client_dist = Path(__file__).parent.parent.parent.parent / "client" / "dist"

    # Prepend proxy routes (checked before /mcp)
    for route in reversed(proxy_routes):
        mcp_starlette.router.routes.insert(0, route)

    # Append static files as catch-all (checked last)
    dev_mode = os.getenv("TALKY_DEV", "").strip() not in ("", "0")
    if not dev_mode and client_dist.is_dir():
        logger.info(f"Serving frontend from {client_dist}")
        mcp_starlette.router.routes.append(
            Mount("/", app=StaticFiles(directory=str(client_dist), html=True)),
        )
    elif dev_mode:
        logger.info("Dev mode: skipping static frontend (use Vite at :5173 for HMR)")
    else:
        logger.warning(f"No built frontend at {client_dist} — run 'npm run build' in client/")

    return mcp_starlette


def main():
    """Run the MCP server."""
    import uvicorn

    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    app = _build_app()

    ssl_certfile = os.getenv("MCP_SSL_CERTFILE")
    ssl_keyfile = os.getenv("MCP_SSL_KEYFILE")

    uvicorn_kwargs = {
        "host": mcp_host,
        "port": mcp_port,
        "log_level": "info",
    }

    if ssl_certfile and ssl_keyfile:
        uvicorn_kwargs["ssl_certfile"] = ssl_certfile
        uvicorn_kwargs["ssl_keyfile"] = ssl_keyfile
        logger.info(f"SSL enabled: cert={ssl_certfile}")

    logger.info(f"Starting unified server on {mcp_host}:{mcp_port}")

    try:
        uvicorn.run(app, **uvicorn_kwargs)
    except KeyboardInterrupt:
        logger.info("Ctrl-C detected, exiting!")
    finally:
        stop_pipecat_process()


if __name__ == "__main__":
    main()
