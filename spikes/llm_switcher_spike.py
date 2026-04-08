#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Spike: Does `LLMSwitcher` with a null/passthrough `MCPDriverLLMService` work?

This spike validates the design in ticket c3a1 / ea77 before we touch the
production `channel.py`. The goal is to answer: **can we hot-swap an LLM
service inside an `LLMSwitcher` where one of the services is a null
passthrough used for MCP-driven flows?**

Approach:
1. Build `MCPDriverLLMService` — inherits `LLMService`, consumes
   `LLMContextFrame` by pushing the latest user message to a queue,
   passes all other frames through unchanged.
2. Build a real `LLMService` to switch against. For portability, this
   spike uses `OpenClawLLMService` because that's what talky has
   configured and it matches the user's production backend. If openclaw
   credentials aren't available, we fall back to switching between two
   `MCPDriverLLMService` instances (which still proves the switcher
   mechanics, just doesn't prove the mixed-type routing).
3. Start a Starlette server on :9091 with the embedded WebRTC handler,
   reusing the pattern from `spikes/embedded_webrtc_spike.py`.
4. Expose HTTP endpoints that let us:
   - GET /status — channel state + queue depth + active service
   - POST /switch?target=mcp|openclaw — queue `ManuallySwitchServiceFrame`
   - POST /inject?text=... — queue LLMTextFrame (simulates convo_speak)
   - POST /pop — pop one item from the speech queue (simulates convo_listen)

Run: cd /Users/winston/dev/personal/talky && uv run python spikes/llm_switcher_spike.py
Then: open http://localhost:9091 in a browser, connect WebRTC, speak.

Spike steps (from ticket c3a1):

  1. Open browser + speak one phrase while in MCP-driver mode.
  2. curl http://localhost:9091/pop → should see the transcribed text.
  3. curl -X POST 'http://localhost:9091/inject?text=hello%20from%20test'
     → browser should hear "hello from test" via TTS.
  4. curl -X POST 'http://localhost:9091/switch?target=openclaw'
     → should log "switched to openclaw".
  5. Speak another phrase.
     → openclaw should respond via its own LLM path (if credentials available).
     → /pop should now return empty (openclaw is active, not MCPDriver).
  6. curl -X POST 'http://localhost:9091/switch?target=mcp'
     → switches back.
  7. curl -X POST 'http://localhost:9091/inject?text=back%20to%20mcp'
     → browser should hear "back to mcp" via TTS.

If all 7 steps pass, the design in c3a1 is validated and we implement it in
`channel.py`. If any step fails, the failure mode tells us what to adjust.
"""

import asyncio
import os
import sys
import time
from typing import Any, Optional

# Add project root to path so we can import server.* / shared.* modules.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

logger.remove()
logger.add(sys.stderr, level="INFO")


# ─── MCPDriverLLMService ─────────────────────────────────────────────────────

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    ManuallySwitchServiceFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService


class MCPDriverLLMService(LLMService):
    """Null/passthrough LLM. Consumes LLMContextFrame by pushing the latest
    user message onto a shared asyncio.Queue. Passes everything else through.

    See ticket c3a1 for the detailed design.
    """

    def __init__(self, name: str, user_speech_queue: asyncio.Queue, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._name = name
        self._user_speech_queue = user_speech_queue

    def __repr__(self) -> str:
        return f"MCPDriverLLMService({self._name})"

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        # Base class handles InterruptionFrame + LLMConfigureOutputFrame.
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMContextFrame):
            await self._handle_user_turn(frame.context)
            # Consumed — do not push further.
            return

        # Everything else (including LLMTextFrame injected from outside)
        # flows through unchanged.
        await self.push_frame(frame, direction)

    async def _handle_user_turn(self, context: Any) -> None:
        try:
            messages = context.get_messages()
        except Exception as e:
            logger.warning(f"{self!r}: could not get messages: {e}")
            return

        last_user_text: Optional[str] = None
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        last_user_text = item.get("text", "")
                        break
            else:
                last_user_text = content
            break

        if not last_user_text:
            logger.debug(f"{self!r}: no user message found in context")
            return

        logger.info(f"{self!r}: queuing user turn: {last_user_text[:80]!r}")
        await self._user_speech_queue.put({
            "text": last_user_text,
            "timestamp": time.time(),
        })


# ─── Spike state ─────────────────────────────────────────────────────────────

_speech_queue: asyncio.Queue = asyncio.Queue()
_active_tasks: set = set()
_mcp_driver: Optional[MCPDriverLLMService] = None
_openclaw_llm: Optional[LLMService] = None
_second_mcp_driver: Optional[MCPDriverLLMService] = None
_current_pipeline_task = None
_llm_switcher = None


# ─── Pipeline builder ────────────────────────────────────────────────────────


async def run_bot_with_switcher(connection) -> None:
    """Build and run the pipeline with an LLMSwitcher containing the MCP driver
    and either openclaw (if configured) or a second MCP driver (fallback).
    """
    global _mcp_driver, _openclaw_llm, _second_mcp_driver
    global _current_pipeline_task, _llm_switcher

    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.pipeline.llm_switcher import LLMSwitcher
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyManual
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
    from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
        SpeechTimeoutUserTurnStopStrategy,
    )
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    from shared.profile_manager import get_profile_manager
    from shared.service_factory import create_stt_service_from_config
    from server.features.voice_switcher import VoiceProfileSwitcher

    t0 = time.monotonic()

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    )

    pm = get_profile_manager()
    profile_name = pm.get_default_voice_profile()
    vp = pm.get_voice_profile(profile_name)
    if vp is None:
        raise RuntimeError(f"voice profile '{profile_name}' not found")

    stt = create_stt_service_from_config(vp.stt_provider, model=vp.stt_model)

    voice_switcher = VoiceProfileSwitcher(profile_name, pm, task=None)
    tts_switcher = voice_switcher.get_service_switcher()

    # Build the MCP driver (first in list → initial active service).
    _mcp_driver = MCPDriverLLMService(name="mcp-driver", user_speech_queue=_speech_queue)

    # Try to build a real openclaw LLM. If credentials missing, fall back.
    llms: list = [_mcp_driver]
    try:
        import importlib

        llm_backend_name = pm.get_default_llm_backend()
        llm_backend = pm.get_llm_backend(llm_backend_name)
        if llm_backend is None:
            raise RuntimeError(f"backend {llm_backend_name} missing")

        module_path = ".".join(llm_backend.service_class.split(".")[:-1])
        class_name = llm_backend.service_class.split(".")[-1]
        if not module_path.startswith("server.") and not module_path.startswith("."):
            module_path = f"server.{module_path}"
        llm_module = importlib.import_module(module_path)
        llm_service_class = getattr(llm_module, class_name)
        _openclaw_llm = llm_service_class(**llm_backend.config)
        llms.append(_openclaw_llm)
        logger.info(f"spike: using {llm_backend_name} as second LLM")
    except Exception as e:
        logger.warning(f"spike: couldn't build real LLM backend ({e}); falling back to second MCPDriver")
        _second_mcp_driver = MCPDriverLLMService(name="mcp-driver-b", user_speech_queue=_speech_queue)
        llms.append(_second_mcp_driver)

    _llm_switcher = LLMSwitcher(llms=llms, strategy_type=ServiceSwitcherStrategyManual)
    logger.info(f"spike: LLMSwitcher built with {len(llms)} LLMs, initial active = {_llm_switcher.active_llm}")

    context = LLMContext([])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=1.0)]
            ),
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_aggregator,
        _llm_switcher,
        tts_switcher,
        transport.output(),
        assistant_aggregator,
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=None,
        cancel_on_idle_timeout=False,
    )
    voice_switcher.set_task(task)
    _current_pipeline_task = task

    @task.rtvi.event_handler("on_client_ready")
    async def _(rtvi):  # noqa: ARG001
        logger.info("spike: RTVI client ready")

    @task.rtvi.event_handler("on_client_message")
    async def _(rtvi, msg):
        await voice_switcher.handle_message(rtvi, msg)

    @transport.event_handler("on_client_connected")
    async def _(transport, client):  # noqa: ANN001, ARG001
        elapsed = time.monotonic() - t0
        logger.info(f"spike: client connected ({elapsed:.2f}s to build pipeline)")

    @transport.event_handler("on_client_disconnected")
    async def _(transport, client):  # noqa: ANN001, ARG001
        logger.info("spike: client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)
    logger.info("spike: runner finished")


# ─── HTTP control endpoints ──────────────────────────────────────────────────


def build_app():
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles

    from pipecat.transports.smallwebrtc.request_handler import (
        IceCandidate,
        SmallWebRTCPatchRequest,
        SmallWebRTCRequest,
        SmallWebRTCRequestHandler,
    )

    webrtc_handler = SmallWebRTCRequestHandler()
    active_sessions: dict = {}

    async def handle_start(request: Request):
        import uuid
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        session_id = str(uuid.uuid4())
        active_sessions[session_id] = body.get("body", {})
        result = {"sessionId": session_id}
        if body.get("enableDefaultIceServers"):
            result["iceConfig"] = {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}
        return JSONResponse(result)

    async def handle_offer(request: Request):
        body = await request.json()
        webrtc_request = SmallWebRTCRequest.from_dict(body)

        async def on_connection(connection):
            task = asyncio.create_task(run_bot_with_switcher(connection))
            _active_tasks.add(task)
            task.add_done_callback(_active_tasks.discard)

        answer = await webrtc_handler.handle_web_request(webrtc_request, on_connection)
        if answer:
            return JSONResponse(answer)
        return JSONResponse({"error": "no webrtc answer"}, status_code=500)

    async def handle_session_offer(request: Request):
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
        return await handle_ice(request)

    async def handle_status(request: Request):  # noqa: ARG001
        active = None
        if _llm_switcher is not None and _llm_switcher.active_llm is not None:
            active = repr(_llm_switcher.active_llm)
        return JSONResponse({
            "status": "ok",
            "pipeline_running": _current_pipeline_task is not None,
            "active_llm": active,
            "speech_queue_depth": _speech_queue.qsize(),
            "peers": len(webrtc_handler._pcs_map),
        })

    async def handle_switch(request: Request):
        target = request.query_params.get("target")
        if target is None:
            return JSONResponse({"error": "missing ?target=mcp|openclaw"}, status_code=400)
        if _llm_switcher is None or _current_pipeline_task is None:
            return JSONResponse({"error": "pipeline not running"}, status_code=409)

        service = None
        if target == "mcp":
            service = _mcp_driver
        elif target in ("openclaw", "second"):
            service = _openclaw_llm or _second_mcp_driver
        else:
            return JSONResponse({"error": f"unknown target {target!r}"}, status_code=400)

        if service is None:
            return JSONResponse({"error": f"target {target!r} not available"}, status_code=409)

        await _current_pipeline_task.queue_frames([ManuallySwitchServiceFrame(service=service)])
        logger.info(f"spike: switch -> {service!r}")
        return JSONResponse({"status": "ok", "active_llm": repr(service)})

    async def handle_inject(request: Request):
        text = request.query_params.get("text", "hello from spike")
        if _current_pipeline_task is None:
            return JSONResponse({"error": "pipeline not running"}, status_code=409)
        await _current_pipeline_task.queue_frames([
            LLMFullResponseStartFrame(),
            LLMTextFrame(text=text),
            LLMFullResponseEndFrame(),
        ])
        logger.info(f"spike: injected LLMTextFrame: {text!r}")
        return JSONResponse({"status": "ok", "injected": text})

    async def handle_pop(request: Request):  # noqa: ARG001
        try:
            item = _speech_queue.get_nowait()
            return JSONResponse({"status": "ok", "item": item})
        except asyncio.QueueEmpty:
            return JSONResponse({"status": "empty"})

    routes = [
        Route("/start", handle_start, methods=["POST"]),
        Route("/api/offer", handle_offer, methods=["POST"]),
        Route("/api/offer", handle_ice, methods=["PATCH"]),
        Route("/sessions/{session_id}/api/offer", handle_session_offer, methods=["POST"]),
        Route("/sessions/{session_id}/api/offer", handle_session_ice, methods=["PATCH"]),
        Route("/status", handle_status, methods=["GET"]),
        Route("/switch", handle_switch, methods=["POST"]),
        Route("/inject", handle_inject, methods=["POST"]),
        Route("/pop", handle_pop, methods=["POST"]),
    ]

    client_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), "client", "dist")
    if os.path.isdir(client_dist):
        routes.append(Mount("/", app=StaticFiles(directory=client_dist, html=True)))

    async def lifespan(app):  # noqa: ARG001
        logger.info("spike: ready on http://localhost:9091")
        logger.info("spike: endpoints:")
        logger.info("  GET  /status")
        logger.info("  POST /switch?target=mcp|openclaw")
        logger.info("  POST /inject?text=...")
        logger.info("  POST /pop")
        yield
        logger.info("spike: shutting down")
        for t in list(_active_tasks):
            t.cancel()

    return Starlette(routes=routes, lifespan=lifespan)


if __name__ == "__main__":
    import uvicorn
    app = build_app()
    print("\n  🧪 LLMSwitcher + MCPDriver spike")
    print("  Open http://localhost:9091 in browser, then:")
    print("    curl http://localhost:9091/status")
    print("    curl -X POST 'http://localhost:9091/inject?text=hello'")
    print("    curl -X POST http://localhost:9091/pop")
    print("    curl -X POST 'http://localhost:9091/switch?target=openclaw'")
    print()
    uvicorn.run(app, host="localhost", port=9091, log_level="info")
