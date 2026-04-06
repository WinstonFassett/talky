"""Spike: Can we embed SmallWebRTCRequestHandler directly in our own Starlette app?

Tests:
1. Does WebRTC signaling work without the Pipecat runner?
2. Can bot() run in-process as an asyncio task?
3. Can we pre-warm services and reuse them across connections?

Run: cd /Users/winston/dev/personal/talky && uv run python spikes/embedded_webrtc_spike.py
Then open http://localhost:9091/client in browser to test

The spike creates a minimal pipeline (STT → TTS echo) with pre-warmed services.
"""

import asyncio
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

logger.remove()
logger.add(sys.stderr, level="DEBUG")


# ─── Pre-warm services ───────────────────────────────────────────────────────

_stt = None
_voice_switcher = None
_warmup_time = None


def warmup_services():
    """Pre-warm STT and TTS services at startup (sync, called from lifespan)."""
    global _stt, _voice_switcher, _warmup_time

    from shared.profile_manager import get_profile_manager
    from shared.service_factory import create_stt_service_from_config
    from server.features.voice_switcher import VoiceProfileSwitcher

    t0 = time.monotonic()

    pm = get_profile_manager()
    vp_name = pm.get_default_voice_profile()
    vp = pm.get_voice_profile(vp_name)

    logger.info(f"Pre-warming services for voice profile: {vp.name}")

    _stt = create_stt_service_from_config(vp.stt_provider, model=vp.stt_model)
    logger.info(f"STT ready: {type(_stt).__name__}")

    _voice_switcher = VoiceProfileSwitcher(vp_name, pm, None)
    logger.info(f"TTS ready via VoiceProfileSwitcher")

    _warmup_time = time.monotonic() - t0
    logger.info(f"Warmup took {_warmup_time:.2f}s")


# ─── In-process bot using pre-warmed services ────────────────────────────────

_active_tasks: set = set()
_connection_count = 0


async def run_bot_inprocess(connection):
    """Run a full talky bot pipeline in-process with pre-warmed services.

    This is the production bot (server/bot.py:run_bot) but calling it inline
    to test service reuse. If services can't be reused, we'll see errors here.
    """
    global _connection_count
    _connection_count += 1
    conn_id = _connection_count

    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.frames.frames import LLMRunFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
    from shared.profile_manager import get_profile_manager

    t0 = time.monotonic()

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )

    # ─── Create FRESH services per connection (reuse breaks lifecycle) ───
    # Pre-warming value: models/configs already cached in memory by Python,
    # so re-creation is fast (~ms) even though we make new instances.
    from shared.service_factory import create_stt_service_from_config
    from server.features.voice_switcher import VoiceProfileSwitcher

    pm = get_profile_manager()
    vp_name = pm.get_default_voice_profile()
    vp = pm.get_voice_profile(vp_name)

    stt = create_stt_service_from_config(vp.stt_provider, model=vp.stt_model)
    voice_switcher = VoiceProfileSwitcher(vp_name, pm, None)
    tts_switcher = voice_switcher.get_service_switcher()

    # ─── Create LLM ───
    llm_backend_name = pm.get_default_llm_backend()
    llm_backend = pm.get_llm_backend(llm_backend_name)

    import importlib
    module_path = ".".join(llm_backend.service_class.split(".")[:-1])
    class_name = llm_backend.service_class.split(".")[-1]
    if not module_path.startswith("server.") and not module_path.startswith("."):
        module_path = f"server.{module_path}"
    llm_service_module = importlib.import_module(module_path)
    llm_service_class = getattr(llm_service_module, class_name)
    llm = llm_service_class(**llm_backend.config)

    # ─── Build pipeline ───
    context = LLMContext([])
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_agg,
        llm,
        tts_switcher,
        transport.output(),
        assistant_agg,
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=None,
        cancel_on_idle_timeout=False,
    )

    voice_switcher.set_task(task)

    @task.rtvi.event_handler("on_client_message")
    async def on_client_message(rtvi, msg):
        if msg.type in ["getVoiceProfiles", "getCurrentVoiceProfile", "setVoiceProfile"]:
            await voice_switcher.handle_message(rtvi, msg)
        else:
            await rtvi.send_error_response(msg, f"Unknown message type: {msg.type}")

    @task.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        from server.config.voice_prompts import VOICE_PROMPT
        context.messages.append({"role": "system", "content": VOICE_PROMPT})
        context.messages.append({
            "role": "user",
            "content": "[TALKY VOICE STT]: Hello! Continue any existing conversation, otherwise greet me.",
        })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        elapsed = time.monotonic() - t0
        logger.info(f"[conn#{conn_id}] Client connected! Pipeline setup took {elapsed:.3f}s")

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        logger.info(f"[conn#{conn_id}] Client disconnected")
        # DON'T cancel — test if pipeline can survive
        # For now, cancel so we can test sequential reuse
        await task.cancel()

    logger.info(f"[conn#{conn_id}] Pipeline built in {time.monotonic() - t0:.3f}s, running...")

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)

    logger.info(f"[conn#{conn_id}] Bot finished")


# ─── Starlette app ────────────────────────────────────────────────────────────


def build_app():
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    from pipecat.transports.smallwebrtc.request_handler import (
        IceCandidate,
        SmallWebRTCPatchRequest,
        SmallWebRTCRequest,
        SmallWebRTCRequestHandler,
    )
    from starlette.staticfiles import StaticFiles

    import uuid

    webrtc_handler = SmallWebRTCRequestHandler()
    active_sessions: dict = {}

    async def handle_start(request: Request):
        """Mimic Pipecat Cloud's /start endpoint — returns session_id + ICE config."""
        try:
            request_data = await request.json()
        except Exception:
            request_data = {}

        session_id = str(uuid.uuid4())
        active_sessions[session_id] = request_data.get("body", {})

        result = {"sessionId": session_id}
        if request_data.get("enableDefaultIceServers"):
            result["iceConfig"] = {
                "iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]
            }

        logger.info(f"Session created: {session_id}")
        return JSONResponse(result)

    async def handle_offer(request: Request):
        body = await request.json()
        webrtc_request = SmallWebRTCRequest.from_dict(body)

        async def on_connection(connection):
            task = asyncio.create_task(run_bot_inprocess(connection))
            _active_tasks.add(task)
            task.add_done_callback(_active_tasks.discard)
            logger.info(f"Bot task spawned for {connection.pc_id}")

        answer = await webrtc_handler.handle_web_request(webrtc_request, on_connection)
        if answer:
            return JSONResponse(answer)
        return JSONResponse({"error": "No answer"}, status_code=500)

    async def handle_session_offer(request: Request):
        """Handle /sessions/{session_id}/api/offer — route to main handler."""
        session_id = request.path_params["session_id"]
        if session_id not in active_sessions:
            return JSONResponse({"error": "Invalid session"}, status_code=404)
        # Delegate to the regular offer handler
        return await handle_offer(request)

    async def handle_ice(request: Request):
        body = await request.json()
        patch = SmallWebRTCPatchRequest(
            pc_id=body["pc_id"],
            candidates=[IceCandidate(**c) for c in body.get("candidates", [])],
        )
        await webrtc_handler.handle_patch_request(patch)
        return JSONResponse({"status": "ok"})

    async def handle_session_ice(request: Request):
        """Handle /sessions/{session_id}/api/offer PATCH."""
        session_id = request.path_params["session_id"]
        if session_id not in active_sessions:
            return JSONResponse({"error": "Invalid session"}, status_code=404)
        return await handle_ice(request)

    async def handle_status(request: Request):
        return JSONResponse({
            "status": "ok",
            "connections": len(webrtc_handler._pcs_map),
            "bot_tasks": len(_active_tasks),
            "total_connections": _connection_count,
            "warmup_time_s": _warmup_time,
            "stt_type": type(_stt).__name__ if _stt else None,
        })

    # Determine client dist path
    client_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), "client", "dist")

    routes = [
        Route("/start", handle_start, methods=["POST"]),
        Route("/api/offer", handle_offer, methods=["POST"]),
        Route("/api/offer", handle_ice, methods=["PATCH"]),
        Route("/sessions/{session_id}/api/offer", handle_session_offer, methods=["POST"]),
        Route("/sessions/{session_id}/api/offer", handle_session_ice, methods=["PATCH"]),
        Route("/status", handle_status, methods=["GET"]),
    ]

    # Serve the built frontend
    if os.path.isdir(client_dist):
        routes.append(Mount("/", app=StaticFiles(directory=client_dist, html=True)))
        logger.info(f"Serving frontend from {client_dist}")

    async def lifespan(app):
        warmup_services()
        logger.info("Server ready — open http://localhost:9091")
        yield
        logger.info("Shutting down...")
        for task in _active_tasks:
            task.cancel()
        await webrtc_handler.close()

    return Starlette(routes=routes, lifespan=lifespan)


if __name__ == "__main__":
    import uvicorn

    app = build_app()
    print("\n  🎤 Embedded WebRTC Spike")
    print("  Open http://localhost:9091 in browser\n")
    uvicorn.run(app, host="localhost", port=9091, log_level="info")
