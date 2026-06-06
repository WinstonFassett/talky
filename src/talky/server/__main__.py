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

from talky.server.bridge import ask as daemon_ask
from talky.server.bridge import say as daemon_say
from talky.server.channel import VoiceChannel

logger.remove()
logger.add(sys.stderr, level="INFO")

# Network config precedence (highest → lowest):
#   1. TALKY_HOST / TALKY_PORT / TALKY_LOOPBACK_PORT / TALKY_HTTPS_CERT / TALKY_HTTPS_KEY env vars
#   2. ~/.talky/settings.yaml  network: { host, port, loopback_port, https: { cert, key } }
#   3. Defaults: host="localhost", port=random-free, loopback_port=random-free (only bound when TLS is on), no TLS
#
# Random-by-default: when no port is configured, the daemon picks a free
# ephemeral port and writes it to ~/.talky/run/talky-daemon.port so
# desktop shells / openers can discover where to connect. Set
# network.port (or TALKY_PORT) to pin a stable port when LAN/mobile
# access matters.
#
# DEPRECATED keys (hard break — daemon refuses to start):
#   network.https_port → use network.port
#   network.http_port  → use network.loopback_port
#   MCP_HOST / MCP_PORT / MCP_SSL_* env vars → drop the MCP_ prefix


def _reject_deprecated_keys(net: dict) -> None:
    """Hard-break on old key names. Tell the user exactly how to migrate."""
    if not isinstance(net, dict):
        return
    bad = []
    if "https_port" in net:
        bad.append(("https_port", "port"))
    if "http_port" in net:
        bad.append(("http_port", "loopback_port"))
    if bad:
        lines = [f"  network.{old}  →  network.{new}" for old, new in bad]
        logger.error(
            "Deprecated network keys in ~/.talky/settings.yaml — rename:\n"
            + "\n".join(lines)
        )
        sys.exit(2)


def _reject_deprecated_env() -> None:
    """Hard-break on MCP_* env vars."""
    deprecated = {
        "MCP_HOST": "TALKY_HOST",
        "MCP_PORT": "TALKY_PORT",
        "MCP_SSL_CERTFILE": "TALKY_HTTPS_CERT",
        "MCP_SSL_KEYFILE": "TALKY_HTTPS_KEY",
        "TALKY_HTTP_PORT": "TALKY_LOOPBACK_PORT",
    }
    bad = [(old, new) for old, new in deprecated.items() if os.getenv(old)]
    if bad:
        lines = [f"  {old}  →  {new}" for old, new in bad]
        logger.error(
            "Deprecated env vars set — rename:\n" + "\n".join(lines)
        )
        sys.exit(2)


def _free_port() -> int:
    """Pick a free ephemeral port from the OS."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _resolve_network_config() -> tuple[str, Optional[str], Optional[str], dict]:
    """Resolve (host, ssl_certfile, ssl_keyfile, raw_network_dict) from env + settings.yaml."""
    from talky.shared.profile_manager import get_profile_manager
    try:
        settings = get_profile_manager().settings or {}
    except Exception:
        settings = {}
    net = (settings.get("network") or {}) if isinstance(settings, dict) else {}
    _reject_deprecated_keys(net)
    _reject_deprecated_env()
    https = (net.get("https") or {}) if isinstance(net, dict) else {}

    host = os.getenv("TALKY_HOST") or net.get("host") or "localhost"
    cert = os.getenv("TALKY_HTTPS_CERT") or https.get("cert")
    key = os.getenv("TALKY_HTTPS_KEY") or https.get("key")
    # Expand ~ so users can write "~/.talky/ssl/..." in settings.yaml
    if cert:
        cert = os.path.expanduser(cert)
    if key:
        key = os.path.expanduser(key)
    return host, cert, key, net


def _resolve_port_from(env_name: str, settings_key: str, net: dict) -> int:
    """Resolve a port: env → settings.yaml → random free port.

    Returns a concrete integer port. When no override is set, picks a
    free ephemeral port so the daemon can run without colliding.
    """
    raw = os.getenv(env_name)
    if raw is None:
        p = net.get(settings_key)
        if p is not None:
            raw = str(p)
    if raw is None:
        return _free_port()
    try:
        port = int(raw)
        if not (1 <= port <= 65535):
            logger.error(f"Invalid {settings_key} '{raw}': must be between 1 and 65535")
            sys.exit(1)
        return port
    except ValueError:
        logger.error(f"Invalid {settings_key} '{raw}': not a valid integer")
        sys.exit(1)


mcp_host, _resolved_cert, _resolved_key, _net_dict = _resolve_network_config()
mcp_port = _resolve_port_from("TALKY_PORT", "port", _net_dict)


def _resolve_loopback_port() -> int:
    """Plain-HTTP loopback port (only bound when TLS is on)."""
    return _resolve_port_from("TALKY_LOOPBACK_PORT", "loopback_port", _net_dict)

mcp = FastMCP(name="pipecat-mcp-server", host=mcp_host, port=mcp_port)

# Ready file: written after uvicorn binds + lifespan completes. The CLI
# checks this instead of sniffing ports with lsof.
DAEMON_RUN_DIR = Path.home() / ".talky" / "run"
DAEMON_READY_PATH = DAEMON_RUN_DIR / "talky-daemon.ready"
# Port discovery file: shells / openers read this to learn the daemon's
# (random) port. Written after uvicorn binds; cleaned up on shutdown.
DAEMON_PORT_PATH = DAEMON_RUN_DIR / "talky-daemon.port"
DAEMON_LOOPBACK_PORT_PATH = DAEMON_RUN_DIR / "talky-daemon.loopback-port"

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
            from talky.shared.profile_manager import get_profile_manager

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
            from talky.shared.profile_manager import get_profile_manager

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
    scheme = "https" if _resolved_cert else "http"
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


@mcp.tool()
async def list_profiles() -> dict:
    """List available talky profiles with health and active status.

    Returns the same data as GET /api/profiles — profile names,
    descriptions, health status, and which is currently active.
    """
    return {
        "profiles": voice_channel.profiles_info(),
        "live": voice_channel.is_live(),
    }


@mcp.tool()
async def switch_profile(profile: str) -> dict:
    """Switch the active talky profile.

    Args:
        profile: The profile name to switch to (e.g. 'openclaw', 'moltis', '__mcp__').

    Returns:
        Dict with status and active profile name.
    """
    try:
        await voice_channel.switch_to_profile(profile)
    except (RuntimeError, ValueError) as e:
        return {"error": str(e)}
    return {"status": "ok", "active": profile}


@mcp.tool()
async def list_voices() -> dict:
    """List available voice profiles with active status.

    Returns the same data as GET /api/voices.
    """
    return {"voices": voice_channel.voices_info()}


@mcp.tool()
async def switch_voice(voice: str) -> dict:
    """Switch the active voice profile.

    Requires a live pipeline (browser connected).

    Args:
        voice: The voice profile name to switch to.

    Returns:
        Dict with status and active voice name.
    """
    try:
        await voice_channel.switch_voice(voice)
    except (RuntimeError, ValueError) as e:
        return {"error": str(e)}
    return {"status": "ok", "active": voice}


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


def _check_ports_or_exit():
    """Refuse to start if another daemon is already running.

    Checks the ready file first (authoritative), falls back to lsof.
    `TALKY_DAEMON_FORCE=1` kills whoever's there and proceeds.
    """
    import time as _t

    force_env = os.getenv("TALKY_DAEMON_FORCE", "").strip() or os.getenv("TALKY_FORCE", "").strip()
    force = force_env not in ("", "0")

    # Check ready file — the authoritative "daemon is running" signal.
    # Race defense: process could die between PID read and signal check,
    # so we retry briefly if signal 0 fails.
    holder_pid = None
    for _ in range(3):
        try:
            holder_pid = int(DAEMON_READY_PATH.read_text().strip())
            os.kill(holder_pid, 0)  # verify it's alive
            break  # Process confirmed alive
        except ProcessLookupError:
            # Process died between read and check — retry
            holder_pid = None
            _t.sleep(0.05)
            continue
        except (FileNotFoundError, ValueError):
            holder_pid = None
            break
    if holder_pid is None:
        # Stale ready file — clean it up.
        DAEMON_READY_PATH.unlink(missing_ok=True)

    # Fallback: check if something is actually LISTENING on either listener port.
    ports_to_check = [mcp_port]
    if _resolved_cert and _resolved_key:
        ports_to_check.append(_resolve_loopback_port())
    if holder_pid is None:
        for port in ports_to_check:
            try:
                result = subprocess.run(
                    ["lsof", "-ti", f":{port}", "-sTCP:LISTEN"],
                    capture_output=True, text=True, timeout=2.0,
                )
                pids = result.stdout.strip()
                if pids:
                    holder_pid = int(pids.split("\n")[0].strip())
                    break
            except (FileNotFoundError, subprocess.SubprocessError, ValueError):
                pass

    if holder_pid is None:
        return

    if force:
        logger.warning(
            f"TALKY_DAEMON_FORCE: killing pid {holder_pid} holding port {mcp_port}"
        )
        try:
            os.kill(holder_pid, signal.SIGTERM)
            _t.sleep(0.2)
            try:
                os.kill(holder_pid, 0)
                os.kill(holder_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass
        except PermissionError as e:
            logger.error(f"Cannot kill pid {holder_pid} on port {mcp_port}: {e}")
            sys.exit(2)
        DAEMON_READY_PATH.unlink(missing_ok=True)
        _t.sleep(0.3)
        return

    logger.error(
        f"Port {mcp_port} already held by pid {holder_pid} — cannot start talky daemon."
    )
    logger.error("Fix: run `talky kill` to reclaim, then retry.")
    logger.error("Or: rerun with `talky daemon --force` to take over automatically.")
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

    async def handle_set_resume(request: Request):
        """POST /api/resume — set a session ID to resume on the next pipeline start.

        Accepts ``?session_id=UUID`` or JSON ``{"session_id": "UUID"}``.
        Targets the currently active LLM backend if it supports set_resume().
        """
        body: dict = {}
        try:
            body = await request.json() or {}
        except Exception:
            pass
        session_id: Optional[str] = request.query_params.get("session_id") or body.get("session_id")
        cwd: Optional[str] = request.query_params.get("cwd") or body.get("cwd")

        if not session_id:
            return JSONResponse(
                {"error": "missing 'session_id' — provide ?session_id=UUID or JSON body"},
                status_code=400,
            )

        active = voice_channel._active_profile  # noqa: SLF001
        from talky.shared.profile_manager import get_profile_manager as _gpm
        _pm = _gpm()
        _tp = _pm.get_talky_profile(active) if active else None
        backend_name = (_tp.llm_backend if _tp and _tp.llm_backend else None) or active
        svc = voice_channel._llm_services.get(backend_name)  # noqa: SLF001
        if svc is None or not hasattr(svc, "set_resume"):
            return JSONResponse(
                {"error": f"active backend {backend_name!r} does not support resume"},
                status_code=409,
            )

        svc.set_resume(session_id)
        if cwd and hasattr(svc, "_cwd"):
            svc._cwd = cwd  # noqa: SLF001
        return JSONResponse({"status": "ok", "session_id": session_id, "backend": backend_name})

    async def handle_get_steer_mode(request: Request):  # noqa: ARG001
        """GET /api/steer-mode — return active steer mode for hermes backend."""
        from talky.backends.hermes import HermesLLMService
        for svc in voice_channel._llm_services.values():  # noqa: SLF001
            if isinstance(svc, HermesLLMService):
                return JSONResponse({"mode": svc.get_steer_mode()})
        return JSONResponse({"mode": "steer", "note": "hermes not active"})

    async def handle_set_steer_mode(request: Request):
        """POST /api/steer-mode — set steer mode on hermes backend."""
        body: dict = {}
        try:
            body = await request.json() or {}
        except Exception:
            pass
        mode = body.get("mode") or request.query_params.get("mode")
        if mode not in ("steer", "interrupt"):
            return JSONResponse(
                {"error": "mode must be 'steer' or 'interrupt'"},
                status_code=400,
            )

        from talky.backends.hermes import HermesLLMService
        from talky.server.event_bus import event_bus

        updated = False
        for svc in voice_channel._llm_services.values():  # noqa: SLF001
            if isinstance(svc, HermesLLMService):
                svc.set_steer_mode(mode)
                updated = True
                break

        if not updated:
            return JSONResponse({"error": "hermes backend not found"}, status_code=409)

        asyncio.create_task(event_bus.emit("steerModeChanged", {"mode": mode}))
        return JSONResponse({"status": "ok", "mode": mode})

    async def handle_permission_grant(request: Request):
        """POST /api/permission/grant — resolve a pending claude-bg permission prompt.

        Body: {"allow": true|false}
        """
        body: dict = {}
        try:
            body = await request.json() or {}
        except Exception:
            pass
        allow = bool(body.get("allow", False))

        # Find any backend with a pending permission. Both claude-code and opencode
        # expose ``resolve_permission(allow=...)`` — but claude-code's is sync and
        # opencode's is async. Handle both.
        from talky.backends.claude_code import ClaudeCodeLLMService

        try:
            from talky.backends.opencode import OpencodeLLMService  # type: ignore
        except ImportError:
            OpencodeLLMService = None  # type: ignore[assignment]

        resolved = False
        for svc in voice_channel._llm_services.values():  # noqa: SLF001
            if isinstance(svc, ClaudeCodeLLMService):
                if svc.resolve_permission(allow=allow):
                    resolved = True
                    break
            if OpencodeLLMService is not None and isinstance(svc, OpencodeLLMService):
                if await svc.resolve_permission(allow=allow):
                    resolved = True
                    break

        if not resolved:
            return JSONResponse({"error": "no pending permission request"}, status_code=409)
        return JSONResponse({"status": "ok", "allow": allow})

    async def handle_ready(request: Request):  # noqa: ARG001
        """GET /api/ready — current readiness state of the daemon.

        Returns ``{"ready": bool, "tasks": [...]}``. Clients that don't
        want to hold an SSE stream open can poll this.
        """
        from talky.server.readiness import readiness as _readiness
        return JSONResponse(
            {"ready": _readiness.is_ready(), "tasks": _readiness.open_tasks()}
        )

    async def handle_events(request: Request):
        """GET /api/events — SSE stream of daemon state changes.

        Typed events: profileChanged, healthChanged.
        Single channel for all consumers. Ticket 73b9 / 2ed2.
        """
        from starlette.responses import StreamingResponse

        from talky.server.event_bus import event_bus

        async def event_generator():
            async with event_bus.subscribe() as queue:
                # Send initial state as a synthetic event so clients
                # don't need a separate REST call on connect.
                from talky.backends.hermes import HermesLLMService
                from talky.server.event_bus import Event
                hermes_steer_mode = "steer"
                for _svc in voice_channel._llm_services.values():  # noqa: SLF001
                    if isinstance(_svc, HermesLLMService):
                        hermes_steer_mode = _svc.get_steer_mode()
                        break

                from talky.server.readiness import readiness as _readiness
                init = Event(
                    type="init",
                    data={
                        "profiles": voice_channel.profiles_info(),
                        "voices": voice_channel.voices_info(),
                        "live": voice_channel.is_live(),
                        "steerMode": hermes_steer_mode,
                        "ready": _readiness.is_ready(),
                        "readinessTasks": _readiness.open_tasks(),
                        # Backend Status per backend — see UBIQUITOUS_LANGUAGE.md.
                        # Empty before the first pipeline build; the picker
                        # uses this to gray out / annotate non-Ready backends.
                        "backendStatus": voice_channel.backend_status(),
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

    async def handle_agent_ws(websocket):
        """WebSocket endpoint for agent extensions (/ws/agent).

        Any agent CLI (Pi, Claude, etc.) that loads a talky extension connects
        here. The handler finds the AgentExtensionLLMService instance in the
        live pipeline (by type), switches to its profile, and bridges until
        the extension disconnects. On disconnect, reverts to __mcp__.
        """
        import json

        from starlette.websockets import WebSocketDisconnect, WebSocketState

        from talky.server.agent_ext import AgentExtensionLLMService

        await websocket.accept()

        # Find the AgentExtensionLLMService instance + its backend name
        # from the live pipeline. No hardcoded names.
        agent_ext = None
        agent_backend_name = None
        for name, svc in voice_channel._llm_services.items():
            if isinstance(svc, AgentExtensionLLMService):
                agent_ext = svc
                agent_backend_name = name
                break

        if agent_ext is None:
            await websocket.send_text(json.dumps({"type": "error", "message": "no agent-ext backend configured — is the browser connected?"}))
            await websocket.close()
            return

        # agent_backend_name is set in the loop above whenever agent_ext is
        # set; the early-return on agent_ext is None guarantees this.
        assert agent_backend_name is not None

        # Read optional hello frame the extension sends immediately on open
        # before the daemon sends ready. Contains {"type":"hello","profile":"..."}
        # so we can switch to the exact talky profile that launched this agent
        # (multiple profiles can share the same agent-ext backend, e.g. pi / claude).
        hello_profile: Optional[str] = None
        try:
            import asyncio
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
            first = json.loads(raw)
            if first.get("type") == "hello" and first.get("profile"):
                hello_profile = str(first["profile"])
        except Exception:
            pass  # No hello or timeout — fall back to lookup below

        # Prefer switching via a user-facing talky profile that uses this
        # backend so the picker shows the right label. Fall back to the
        # backend name if no talky profile points at it.
        from talky.shared.profile_manager import get_profile_manager
        pm = get_profile_manager()
        active_profile: str = agent_backend_name
        if hello_profile and pm.get_talky_profile(hello_profile) is not None:
            active_profile = hello_profile
        else:
            try:
                for tp_name in pm.list_talky_profiles().keys():
                    tp_obj = pm.get_talky_profile(tp_name)
                    if tp_obj and getattr(tp_obj, "llm_backend", None) == agent_backend_name:
                        active_profile = tp_name
                        break
            except Exception as e:
                logger.warning(f"/ws/agent: talky-profile lookup failed: {e}")

        logger.info(f"/ws/agent: extension connected → profile {active_profile!r} (backend {agent_backend_name!r})")

        try:
            # Agent owns its own greeting via the TTS stream — no system
            # announcement on the join. Ticket e540.
            await voice_channel.switch_to_profile(active_profile, announce=False)
        except Exception as e:
            logger.warning(f"/ws/agent: could not switch to {active_profile!r}: {e}")
            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
            await websocket.close()
            return

        # Resolve the greeting *instruction* (talky-profile → backend →
        # settings → built-in default). The agent generates its own
        # greeting words from this instruction. Returns None if greeting
        # is explicitly disabled for this profile.
        greeting_instruction: Optional[str] = None
        try:
            greeting_instruction = pm.resolve_greeting_instruction(active_profile)
            logger.info(
                f"/ws/agent: greeting instruction resolved for {active_profile!r}: "
                f"{greeting_instruction!r}"
            )
        except Exception as e:
            logger.warning(f"/ws/agent: greeting resolve failed: {e}")

        try:
            await agent_ext.handle_websocket(websocket, greeting_instruction=greeting_instruction)
        except WebSocketDisconnect:
            pass
        finally:
            logger.info(f"/ws/agent: extension disconnected — reverting to {voice_channel.MCP_DRIVER_PROFILE!r}")
            try:
                if websocket.client_state != WebSocketState.DISCONNECTED:
                    await websocket.close()
            except Exception:
                pass
            try:
                await voice_channel.switch_to_profile(voice_channel.MCP_DRIVER_PROFILE)
            except Exception:
                pass

    from starlette.routing import WebSocketRoute

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
        Route("/api/resume", handle_set_resume, methods=["POST"]),
        Route("/api/steer-mode", handle_get_steer_mode, methods=["GET"]),
        Route("/api/steer-mode", handle_set_steer_mode, methods=["POST"]),
        Route("/api/permission/grant", handle_permission_grant, methods=["POST"]),
        Route("/api/events", handle_events, methods=["GET"]),
        Route("/api/ready", handle_ready, methods=["GET"]),
        # Legacy compat — old CLI may still hit /api/profile.
        Route("/api/profile", handle_get_profile, methods=["GET"]),
        Route("/api/profile", handle_set_profile, methods=["POST"]),
        WebSocketRoute("/ws/agent", handle_agent_ws),
        WebSocketRoute("/ws/pi", handle_agent_ws),  # legacy compat
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

        # Generic pre-warm: fetch any HF-backed assets the active voice
        # profile needs, reporting progress through the readiness tracker.
        # Off-thread so the event loop stays responsive and SSE / /api/ready
        # subscribers can observe progress.
        try:
            from talky.server.prewarm import prewarm_hf_assets
            await asyncio.to_thread(prewarm_hf_assets)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Pre-warm failed: {e}")

        # Signal to the CLI that the daemon is ready to accept requests.
        # Gated on the readiness tracker so the ready-file only appears
        # once all registered startup work is actually done.
        from talky.server.readiness import readiness as _readiness
        await _readiness.wait_ready()
        DAEMON_RUN_DIR.mkdir(parents=True, exist_ok=True)
        DAEMON_READY_PATH.write_text(str(os.getpid()))
        logger.info(f"Daemon ready (pid={os.getpid()}, ready_file={DAEMON_READY_PATH})")

        async with _original_lifespan(app):
            try:
                yield
            finally:
                logger.info("Lifespan shutdown: tearing down voice channel")
                try:
                    DAEMON_READY_PATH.unlink(missing_ok=True)
                    DAEMON_PORT_PATH.unlink(missing_ok=True)
                    DAEMON_LOOPBACK_PORT_PATH.unlink(missing_ok=True)
                except Exception:
                    pass
                try:
                    await voice_channel.shutdown()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Voice channel shutdown raised: {e}")

    mcp_starlette.router.lifespan_context = _composed_lifespan

    # Embedded WebRTC routes — prepended so they're matched before /mcp.
    webrtc_routes, _handler = _build_webrtc_routes()
    for route in reversed(webrtc_routes):
        mcp_starlette.router.routes.insert(0, route)

    # Static frontend at the catch-all. Bundled into the wheel at
    # talky/_client_dist/ (in editable installs, it's a symlink to
    # client/dist/ at the repo root, so `npm run build` updates it live).
    client_dist = Path(__file__).parent.parent / "_client_dist"
    if not client_dist.is_dir():
        # Editable-install legacy path — for repos that haven't symlinked yet.
        legacy = Path(__file__).parent.parent.parent.parent / "client" / "dist"
        if legacy.is_dir():
            client_dist = legacy

    dev_mode = os.getenv("TALKY_DEV", "").strip() not in ("", "0")
    if not dev_mode and client_dist.is_dir() and (client_dist / "index.html").exists():
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

    ssl_certfile = _resolved_cert
    ssl_keyfile = _resolved_key
    ssl_enabled = bool(ssl_certfile and ssl_keyfile)

    primary_kwargs = {
        "host": mcp_host,
        "port": mcp_port,
        "log_level": "info",
    }
    if ssl_enabled:
        primary_kwargs["ssl_certfile"] = ssl_certfile
        primary_kwargs["ssl_keyfile"] = ssl_keyfile
        logger.info(f"SSL enabled on :{mcp_port} (cert={ssl_certfile})")

    scheme = "https" if ssl_enabled else "http"
    logger.info(f"Primary listener: {scheme}://{mcp_host}:{mcp_port}")

    # When SSL is on, also bind plain HTTP loopback-only so MCP clients that
    # don't speak self-signed HTTPS can connect. Set via
    # TALKY_LOOPBACK_PORT or settings.yaml network.loopback_port (default: random).
    http_port = _resolve_loopback_port() if ssl_enabled else None

    # Write port discovery files so shells / openers can find us.
    DAEMON_RUN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        DAEMON_PORT_PATH.write_text(str(mcp_port))
    except OSError as e:
        logger.warning(f"Could not write port discovery file {DAEMON_PORT_PATH}: {e}")
    if http_port is not None:
        try:
            DAEMON_LOOPBACK_PORT_PATH.write_text(str(http_port))
        except OSError as e:
            logger.warning(
                f"Could not write loopback-port discovery file "
                f"{DAEMON_LOOPBACK_PORT_PATH}: {e}"
            )

    try:
        if http_port is not None:
            secondary_kwargs = {
                "host": "127.0.0.1",
                "port": http_port,
                "log_level": "info",
                # Skip lifespan on the secondary — the MCP session manager
                # refuses to .run() twice on the same FastAPI app instance.
                # Primary owns the lifespan; the secondary just reuses
                # the already-mounted routes.
                "lifespan": "off",
            }
            logger.info(f"Secondary listener: http://127.0.0.1:{http_port} (loopback only)")
            import asyncio as _asyncio
            servers = [
                uvicorn.Server(uvicorn.Config(app, **primary_kwargs)),
                uvicorn.Server(uvicorn.Config(app, **secondary_kwargs)),
            ]
            # Disable signal handling on the secondary so the primary handles
            # SIGTERM/SIGINT for the whole process.
            servers[1].install_signal_handlers = lambda: None  # type: ignore[method-assign]

            async def _run_both():
                await _asyncio.gather(*(s.serve() for s in servers))

            _asyncio.run(_run_both())
        else:
            uvicorn.run(app, **primary_kwargs)
    except KeyboardInterrupt:
        logger.info("Ctrl-C detected, exiting!")
    # No finally cleanup needed — the lifespan handles it inside uvicorn's
    # graceful shutdown path.


if __name__ == "__main__":
    main()
