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
    talk_local_audio: Alias for ask_local_audio.

Conversation tools (browser pipeline, WebRTC, in-process):
    start_convo: Open the browser to the voice UI.
    convo_speak: Speak text within an active conversation.
    convo_listen: Listen for user speech within an active conversation.
    join_convo: Check in to the conversation (returns channel status).
    request_leave: Polite exit with signoff cue + grace window.

Architecture (ticket 58db — "hot voice channel"):
    The voice pipeline is in-process. `SmallWebRTCRequestHandler` is
    mounted directly on this Starlette app — no child pipecat, no
    reverse proxy. Services are pre-warmed in the lifespan startup
    hook; a fresh pipeline is built per browser connection using those
    pre-warmed configs. See `channel.py`.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import uuid
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from loguru import logger
from mcp.server.fastmcp import FastMCP

from pipecat_mcp_server.channel import VoiceChannel
from pipecat_mcp_server.daemon_bridge import ask as daemon_ask
from pipecat_mcp_server.daemon_bridge import say as daemon_say

logger.remove()
logger.add(sys.stderr, level="INFO")

# Create MCP server
# Host is configurable via MCP_HOST environment variable, defaults to localhost for security
mcp_host = os.getenv("MCP_HOST", "localhost")
mcp_port = int(os.getenv("MCP_PORT", "9090"))
mcp = FastMCP(name="pipecat-mcp-server", host=mcp_host, port=mcp_port)

def _read_idle_ttl_seconds() -> Optional[float]:
    """Load the idle-room TTL (ticket 0c5d).

    Precedence:
      1. ``TALKY_ROOM_IDLE_TTL_SECS`` env var (float or ``"infinity"``).
      2. ``room.idle_ttl_seconds`` in ``~/.talky/settings.yaml``
         (float or ``"infinity"``).
      3. None (infinity / preserve legacy behavior).

    Returns ``None`` for infinity, or a positive float in seconds.
    """
    raw: Optional[str] = os.getenv("TALKY_ROOM_IDLE_TTL_SECS")
    if raw is None:
        try:
            from shared.profile_manager import get_profile_manager

            settings = getattr(get_profile_manager(), "settings", {}) or {}
            yaml_raw = settings.get("room", {}).get("idle_ttl_seconds")
            if yaml_raw is not None:
                raw = str(yaml_raw)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Could not read room.idle_ttl_seconds from settings: {e}")
            raw = None

    if raw is None:
        return None
    if str(raw).strip().lower() in ("infinity", "inf", "none", ""):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(f"Invalid TALKY_ROOM_IDLE_TTL_SECS / room.idle_ttl_seconds={raw!r}; using infinity")
        return None
    if value <= 0:
        logger.warning(f"Non-positive room idle TTL {value!r}; using infinity")
        return None
    return value


_idle_ttl_seconds = _read_idle_ttl_seconds()


def _read_request_leave_grace_seconds() -> float:
    """Load the request_leave grace window (ticket 0b80 follow-up rip).

    Precedence:
      1. ``TALKY_REQUEST_LEAVE_GRACE_SECS`` env var (float).
      2. ``room.request_leave_grace_seconds`` in ``~/.talky/settings.yaml``.
      3. ``4.0`` (default).

    Returns a non-negative float in seconds. Negative values are
    clamped to 0 (disables the ceremony entirely).
    """
    default = 4.0
    raw: Optional[str] = os.getenv("TALKY_REQUEST_LEAVE_GRACE_SECS")
    if raw is None:
        try:
            from shared.profile_manager import get_profile_manager

            settings = getattr(get_profile_manager(), "settings", {}) or {}
            yaml_raw = settings.get("room", {}).get("request_leave_grace_seconds")
            if yaml_raw is not None:
                raw = str(yaml_raw)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Could not read room.request_leave_grace_seconds from settings: {e}")
            raw = None

    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            f"Invalid TALKY_REQUEST_LEAVE_GRACE_SECS / room.request_leave_grace_seconds={raw!r}; "
            f"using default {default}s"
        )
        return default
    if value < 0:
        logger.warning(f"Negative request_leave grace {value!r}; clamping to 0")
        return 0.0
    return value


_request_leave_grace_seconds = _read_request_leave_grace_seconds()

# The single in-process voice channel. Created eagerly so the MCP tools can
# reference it during module import, but warmup is deferred to the Starlette
# lifespan startup hook (see _build_app).
voice_channel = VoiceChannel(
    idle_ttl_seconds=_idle_ttl_seconds,
    request_leave_grace_seconds=_request_leave_grace_seconds,
)
if _idle_ttl_seconds is not None:
    logger.info(f"VoiceChannel: idle TTL set to {_idle_ttl_seconds}s (empty rooms will auto-tear-down)")
logger.info(f"VoiceChannel: request_leave grace window = {_request_leave_grace_seconds}s")


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


@mcp.tool()
async def talk_local_audio(text: str, silence_timeout: float = 10.0) -> dict:
    """Alias for ask_local_audio. Prefer this verb when the user says "talk to me",
    "let's talk", or similar. Same behavior as ask_local_audio: speak text, then
    listen for a spoken reply via the voice daemon.

    Exists because the natural verb for the user is often "talk" rather than "ask";
    "say" is reserved for fire-and-forget status updates where no reply is expected.

    Args:
        text: The text to speak before listening.
        silence_timeout: Seconds of no speech at all before giving up (default: 10).

    Returns:
        Dict with success status and transcript of user's response.

    """
    return await daemon_ask(text, silence_timeout=silence_timeout)


# ──────────────────────────────────────────────────────────────────────────────
# Conversation tools (browser pipeline, WebRTC, in-process — ticket 58db)
# ──────────────────────────────────────────────────────────────────────────────


@mcp.tool()
async def start_convo(auto_open: bool = True) -> dict:
    """Open a voice conversation with the browser UI.

    Under the 58db "hot voice channel" architecture, the voice pipeline is
    always ready on the MCP server side — this tool just points the browser
    at the UI, which then establishes a WebRTC peer connection. The pipeline
    is built lazily when the browser actually connects (see
    `channel.VoiceChannel.attach`).

    Args:
        auto_open: Automatically open the browser (default: True).

    Returns:
        Connection information including the browser URL.

    """
    scheme = "https" if os.getenv("MCP_SSL_CERTFILE") else "http"
    client_url = f"{scheme}://{mcp_host}:{mcp_port}?autoconnect=true"

    if auto_open:
        webbrowser.open(client_url)

    return {
        "success": True,
        "client_url": client_url,
        "message": f"Voice conversation ready. Browser opened to {client_url}.",
    }


@mcp.tool()
async def convo_speak(text: str) -> bool:
    """Speak text within an active browser conversation.

    Requires a WebRTC peer to be connected (i.e. the browser UI must be open
    and connected). Raises on "not live".

    Args:
        text: The text to speak.

    Returns:
        True on success.

    """
    await voice_channel.speak(text)
    return True


@mcp.tool()
async def convo_listen() -> dict:
    """Listen for user speech within an active browser conversation.

    Blocks until the user speaks, then returns all buffered utterances.
    Returns a dict with 'text' (combined transcription) and 'segments'
    (list of utterances with timestamps).
    """
    return await voice_channel.listen()


@mcp.tool()
async def join_convo() -> dict:
    """Check in to the voice conversation.

    Returns the channel status dict so the caller can verify the
    pipeline is live and inspect the active profile. This is a
    lightweight "I'm here" ritual with no state mutation — call it
    before driving the conversation so you know what you're walking
    into.
    """
    return voice_channel.join_convo()


@mcp.tool()
async def request_leave() -> dict:
    """Politely announce intent to leave the convo (ticket 0b80).

    Plays the active profile's signoff phrase (if configured) followed
    by a descending-beep audio cue, then waits for the configured grace
    window for the user to object. If the user speaks within the window,
    the return dict carries ``user_interrupted: True`` plus the
    transcribed text — the agent should resume the conversation and
    **not** leave.

    The grace window is set by the user via the
    ``TALKY_REQUEST_LEAVE_GRACE_SECS`` env var or
    ``room.request_leave_grace_seconds`` in ``~/.talky/settings.yaml``
    (default 4.0s). Agents do not control it.

    This replaces the old ``leave_convo`` and ``end_convo`` MCP tools.
    Agents no longer have a path to tear down the whole pipeline
    unilaterally; use the CLI (``talky kill``) or close the browser tab
    for that.

    Returns a dict with at least ``left`` (bool) and ``user_interrupted``
    (bool). On an interrupted leave, also includes ``text``.
    """
    return await voice_channel.request_leave()


# ──────────────────────────────────────────────────────────────────────────────
# Signal / port management (ticket 727e)
# ──────────────────────────────────────────────────────────────────────────────


def signal_handler(signum, frame):
    """Handle SIGTERM and SIGINT signals.

    Note: this handler is typically replaced by uvicorn's own handlers once
    `uvicorn.run()` starts (via `Server.capture_signals`). The load-bearing
    cleanup path is the Starlette lifespan shutdown hook in `_build_app`,
    which runs inside the event loop before uvicorn releases ports.
    """
    logger.info(f"Received signal {signum}, exiting")
    sys.exit(0)


def _port_holder(port: int) -> Optional[int]:
    """Return the holder PID if a port is bound, else None."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    stdout = result.stdout.strip()
    if not stdout:
        return None
    first = stdout.split("\n")[0].strip()
    try:
        return int(first)
    except ValueError:
        return None


def _check_ports_or_exit():
    """Defense #4 (ticket 727e): refuse to start if port 9090 is held.

    `TALKY_DAEMON_FORCE=1` kills whoever's there and proceeds.
    (Legacy `TALKY_MCP_FORCE` is still honored as a fallback.)
    """
    force_env = os.getenv("TALKY_DAEMON_FORCE", "").strip() or os.getenv("TALKY_MCP_FORCE", "").strip()
    force = force_env not in ("", "0")
    holder = _port_holder(mcp_port)
    if holder is None:
        return

    if force:
        logger.warning(
            f"TALKY_DAEMON_FORCE: killing pid {holder} holding port {mcp_port}"
        )
        try:
            os.kill(holder, signal.SIGTERM)
            import time as _t

            _t.sleep(0.2)
            try:
                os.kill(holder, 0)
                os.kill(holder, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass
        except PermissionError as e:
            logger.error(f"Cannot kill pid {holder} on port {mcp_port}: {e}")
            sys.exit(2)
        return

    logger.error(
        f"Port {mcp_port} already held by pid {holder} — cannot start talky mcp."
    )
    logger.error("Fix: run `talky kill` to reclaim, then retry.")
    logger.error("Or: rerun with `talky mcp --force` to take over automatically.")
    sys.exit(2)


# ──────────────────────────────────────────────────────────────────────────────
# App construction
# ──────────────────────────────────────────────────────────────────────────────


def _build_webrtc_routes():
    """Build Starlette routes that embed `SmallWebRTCRequestHandler`.

    The handler is mounted directly on this Starlette app — no
    reverse-proxy, no child pipecat process.
    """
    from pipecat.transports.smallwebrtc.request_handler import (
        IceCandidate,
        SmallWebRTCPatchRequest,
        SmallWebRTCRequest,
        SmallWebRTCRequestHandler,
    )
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    webrtc_handler = SmallWebRTCRequestHandler()
    active_sessions: dict = {}

    async def handle_start(request: Request):
        """Mimic Pipecat Cloud's /start: return session_id + ICE config."""
        try:
            request_data = await request.json()
        except Exception:
            request_data = {}

        session_id = str(uuid.uuid4())
        active_sessions[session_id] = request_data.get("body", {})

        result: dict = {"sessionId": session_id}
        if request_data.get("enableDefaultIceServers"):
            result["iceConfig"] = {
                "iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]
            }

        logger.info(f"Voice session created: {session_id}")
        return JSONResponse(result)

    async def handle_offer(request: Request):
        """Handle a WebRTC SDP offer → build a pipeline on the channel."""
        body = await request.json()
        webrtc_request = SmallWebRTCRequest.from_dict(body)

        async def on_connection(connection):
            try:
                await voice_channel.attach(connection)
            except Exception as e:  # noqa: BLE001
                logger.error(f"VoiceChannel.attach failed: {e}")

        answer = await webrtc_handler.handle_web_request(webrtc_request, on_connection)
        if answer:
            return JSONResponse(answer)
        return JSONResponse({"error": "No WebRTC answer produced"}, status_code=500)

    async def handle_session_offer(request: Request):
        """Pipecat Cloud compat: /sessions/{session_id}/api/offer POST.

        Note: we deliberately do NOT require the session_id to be in
        active_sessions. The underlying SmallWebRTCRequestHandler tracks
        its own pc_id map, and the session_id is just a Pipecat Cloud
        compat token. Rejecting unknown session_ids causes false 404s
        when the browser retries a stale session from a previous mcp
        instance — which is normal on hot reload.
        """
        return await handle_offer(request)

    async def handle_ice(request: Request):
        """Handle a WebRTC ICE candidate patch."""
        body = await request.json()
        patch = SmallWebRTCPatchRequest(
            pc_id=body["pc_id"],
            candidates=[IceCandidate(**c) for c in body.get("candidates", [])],
        )
        await webrtc_handler.handle_patch_request(patch)
        return JSONResponse({"status": "ok"})

    async def handle_session_ice(request: Request):
        """Same rationale as handle_session_offer — don't gate on session_id."""
        return await handle_ice(request)

    async def handle_status(request: Request):  # noqa: ARG001
        return JSONResponse(
            {
                "status": "ok",
                "channel": voice_channel.status(),
                "connections": len(webrtc_handler._pcs_map),
            }
        )

    async def handle_get_profile(request: Request):  # noqa: ARG001
        """GET /api/profile — legacy compat for CLI (old shape)."""
        info = voice_channel.profiles_info()
        active = next((p["name"] for p in info if p["active"]), None)
        available = [p["name"] for p in info]
        return JSONResponse({
            "active": active,
            "available": available,
            "live": voice_channel.is_live(),
        })

    async def handle_set_profile(request: Request):
        """POST /api/profile — legacy compat, delegates to switch handler."""
        return await handle_switch_profile(request)

    async def handle_get_profiles(request: Request):  # noqa: ARG001
        """GET /api/profiles — list configured LLM profiles + active + live status.

        Works pre-connect (reads from config). Ticket 73b9.
        """
        return JSONResponse({
            "profiles": voice_channel.profiles_info(),
            "live": voice_channel.is_live(),
        })

    async def handle_switch_profile(request: Request):
        """POST /api/profiles/switch — switch active LLM profile.

        Accepts ``?profile=NAME`` or JSON ``{"profile": "NAME"}``.
        Ticket 73b9.
        """
        profile: Optional[str] = request.query_params.get("profile")
        if profile is None:
            try:
                body = await request.json()
                profile = body.get("profile") if isinstance(body, dict) else None
            except Exception:
                profile = None

        if not profile:
            return JSONResponse(
                {"error": "missing 'profile' — provide ?profile=NAME or JSON body"},
                status_code=400,
            )

        try:
            await voice_channel.switch_to_profile(profile)
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=404)

        return JSONResponse({
            "status": "ok",
            "active": profile,
        })

    async def handle_get_voices(request: Request):  # noqa: ARG001
        """GET /api/voices — list voice profiles + active. Ticket 2ed2."""
        return JSONResponse({
            "voices": voice_channel.voices_info(),
        })

    async def handle_switch_voice(request: Request):
        """POST /api/voices/switch — switch active voice profile. Ticket 2ed2."""
        profile: Optional[str] = request.query_params.get("profile")
        if profile is None:
            try:
                body = await request.json()
                profile = body.get("profile") if isinstance(body, dict) else None
            except Exception:
                profile = None

        if not profile:
            return JSONResponse(
                {"error": "missing 'profile' — provide ?profile=NAME or JSON body"},
                status_code=400,
            )

        try:
            await voice_channel.switch_voice(profile)
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=404)

        return JSONResponse({
            "status": "ok",
            "active": profile,
        })

    async def handle_events(request: Request):
        """GET /api/events — SSE stream of daemon state changes.

        Typed events: profileChanged, healthChanged.
        Single channel for all consumers. Ticket 73b9 / 2ed2.
        """
        from starlette.responses import StreamingResponse

        from pipecat_mcp_server.event_bus import event_bus

        async def event_generator():
            async with event_bus.subscribe() as queue:
                # Send initial state as a synthetic event so clients
                # don't need a separate REST call on connect.
                from pipecat_mcp_server.event_bus import Event

                init = Event(
                    type="init",
                    data={
                        "profiles": voice_channel.profiles_info(),
                        "voices": voice_channel.voices_info(),
                        "live": voice_channel.is_live(),
                    },
                )
                yield init.sse()

                while True:
                    try:
                        event = await queue.get()
                        yield event.sse()
                    except asyncio.CancelledError:
                        break

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    routes = [
        Route("/start", handle_start, methods=["POST"]),
        Route("/api/offer", handle_offer, methods=["POST"]),
        Route("/api/offer", handle_ice, methods=["PATCH"]),
        Route(
            "/sessions/{session_id}/api/offer",
            handle_session_offer,
            methods=["POST"],
        ),
        Route(
            "/sessions/{session_id}/api/offer",
            handle_session_ice,
            methods=["PATCH"],
        ),
        Route("/status", handle_status, methods=["GET"]),
        Route("/api/profiles", handle_get_profiles, methods=["GET"]),
        Route("/api/profiles/switch", handle_switch_profile, methods=["POST"]),
        Route("/api/voices", handle_get_voices, methods=["GET"]),
        Route("/api/voices/switch", handle_switch_voice, methods=["POST"]),
        Route("/api/events", handle_events, methods=["GET"]),
        # Legacy compat — old CLI may still hit /api/profile.
        Route("/api/profile", handle_get_profile, methods=["GET"]),
        Route("/api/profile", handle_set_profile, methods=["POST"]),
    ]
    return routes, webrtc_handler


def _build_app():
    """Build the unified Starlette app.

    Route layout:
        POST /start                    → WebRTC session init (embedded)
        POST/PATCH /api/offer          → WebRTC signaling (embedded)
        POST/PATCH /sessions/{id}/...  → WebRTC signaling (Pipecat Cloud compat)
        GET  /status                   → channel introspection
        ALL  /mcp                      → FastMCP protocol (streamable-http)
        GET  /*                        → static files (client/dist/)
    """
    from starlette.routing import Mount
    from starlette.staticfiles import StaticFiles

    # Build the MCP Starlette app — it has a single /mcp route and a lifespan
    # that manages the StreamableHTTP session manager. We compose around it.
    mcp_starlette = mcp.streamable_http_app()

    # 727e defense #3 + 58db lifespan: compose our warmup/shutdown into the
    # Starlette lifespan so it runs inside uvicorn's event loop, before ports
    # are released.
    _original_lifespan = mcp_starlette.router.lifespan_context

    @asynccontextmanager
    async def _composed_lifespan(app):
        # Pre-warm the voice channel synchronously in startup. Config-only,
        # fast (~tens of ms).
        try:
            voice_channel.warmup()
        except Exception as e:  # noqa: BLE001
            # Don't let a misconfigured voice profile block the MCP server
            # from starting — log and continue. Convo tools will fail with a
            # clear error if the channel isn't warm.
            logger.error(f"VoiceChannel warmup failed: {e}")

        # Start background health polling (ticket 73b9).
        voice_channel.start_health_polling(interval=30.0)

        async with _original_lifespan(app):
            try:
                yield
            finally:
                logger.info("Lifespan shutdown: tearing down voice channel")
                try:
                    await voice_channel.shutdown()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Voice channel shutdown raised: {e}")

    mcp_starlette.router.lifespan_context = _composed_lifespan

    # Embedded WebRTC routes — prepended so they're matched before /mcp.
    webrtc_routes, _handler = _build_webrtc_routes()
    for route in reversed(webrtc_routes):
        mcp_starlette.router.routes.insert(0, route)

    # Static frontend at the catch-all.
    client_dist = Path(__file__).parent.parent.parent.parent / "client" / "dist"
    dev_mode = os.getenv("TALKY_DEV", "").strip() not in ("", "0")
    if not dev_mode and client_dist.is_dir():
        logger.info(f"Serving frontend from {client_dist}")
        mcp_starlette.router.routes.append(
            Mount("/", app=StaticFiles(directory=str(client_dist), html=True)),
        )
    elif dev_mode:
        logger.info("Dev mode: skipping static frontend (run Vite dev server for HMR)")
    else:
        logger.warning(
            f"No built frontend at {client_dist} — run 'npm run build' in client/"
        )

    return mcp_starlette


def main():
    """Run the MCP server."""
    import uvicorn

    # 727e defense #4: refuse to start if 9090 is already held. Honors
    # TALKY_DAEMON_FORCE=1 (or legacy TALKY_MCP_FORCE=1) to reclaim.
    _check_ports_or_exit()

    # Best-effort handlers. uvicorn replaces these via Server.capture_signals
    # once it starts; the lifespan shutdown hook is the load-bearing cleanup.
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

    logger.info(f"Starting unified server on {mcp_host}:{mcp_port} (in-process voice)")

    try:
        uvicorn.run(app, **uvicorn_kwargs)
    except KeyboardInterrupt:
        logger.info("Ctrl-C detected, exiting!")
    # No finally cleanup needed — the lifespan handles it inside uvicorn's
    # graceful shutdown path.


if __name__ == "__main__":
    main()
