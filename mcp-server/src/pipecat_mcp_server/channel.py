#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""In-process voice channel for talky's MCP server — ticket 58db.

This module is the heart of the "hot voice channel" refactor. It replaces
the old child-process-based voice path (``agent_ipc.py`` + ``agent.py`` +
``bot.py``), which spawned a separate pipecat process for every convo and
required ~12s of cold-start latency per ``start_convo``.

## Architecture

One ``VoiceChannel`` instance per MCP server process, held on the Starlette
app state. It owns:

- **Pre-warmed services** — STT provider + ``VoiceProfileSwitcher`` are
  instantiated once at lifespan startup so the "first connection" path is
  fast. Pipecat services have ``start()``/``stop()`` lifecycles tied to a
  pipeline, so the pre-warmed *instances* can't be reused across pipelines
  directly. Instead, pre-warming pulls model config / credentials into
  memory so that *new* instances created per connection construct in
  milliseconds rather than seconds.

- **Active pipeline task** — exactly one pipeline lives at a time, built
  on the ``on_connection`` callback from ``SmallWebRTCRequestHandler``
  when a browser peer arrives. The pipeline runs as an ``asyncio.Task`` on
  the MCP server's event loop. No ``multiprocessing.Process``.

- **User-speech queue** — an ``asyncio.Queue`` of transcribed user
  utterances, populated by the aggregator's ``on_user_turn_stopped``
  handler and drained by :meth:`VoiceChannel.listen`.

## Lifecycle events

| Event                          | What happens                                |
| ------------------------------ | ------------------------------------------- |
| Lifespan startup               | ``warmup()`` loads profile config / STT     |
| Browser POST /api/offer        | ``on_connection()`` → ``attach(conn)``      |
| ``attach()``                   | Build fresh pipeline, start asyncio task    |
| ``convo_speak(text)``          | :meth:`speak` queues ``LLMTextFrame``       |
| ``convo_listen()``             | :meth:`listen` awaits the speech queue      |
| Browser disconnect / reload    | Transport event → :meth:`detach`            |
| ``detach()``                   | Cancel pipeline task, clear refs            |
| ``end_convo``                  | :meth:`end` — alias for detach              |
| Lifespan shutdown              | :meth:`shutdown` — drain any pending work   |

## Intentional differences from the old ``PipecatMCPAgent``

- No ``_started`` flag. Liveness is read off the actual task state
  (``task.has_finished() / self._runner_task.done()``). This fixes the
  d5e6 reconnect-zombie pattern at the source — there's nothing to go
  stale.
- No ``_DISCONNECT_SENTINEL`` queue poisoning. ``listen()`` races the
  speech queue against a disconnect event directly.
- No child ``bot.py`` lookup. The pipeline is built in-process with an
  explicit function; ``pipecat.runner.run`` is not involved at all, so
  the sys.path bot-discovery bug (bb5a) cannot recur.
- STT / VoiceProfileSwitcher are constructed fresh per connection, not
  reused. Pipecat services' lifecycle is pipeline-bound — we learned
  from the 58db spike that reusing service instances across pipelines
  leaves them in a broken post-``stop()`` state. Pre-warming loads the
  heavy stuff into memory so fresh construction is near-free.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import time
from typing import Any, Optional

# The talky project root needs to be on sys.path so we can `import server.*`
# and `import shared.*`. When talky is installed via `uv tool install
# --editable .` and invoked as `talky mcp`, the mcp-server package is on
# sys.path via its entry point, but the project root containing `server/`
# and `shared/` is not. Add it here, once at import time, before any of
# the runtime imports below reach for those modules.
# Layout: <talky_root>/mcp-server/src/pipecat_mcp_server/channel.py
# Walk: dirname(__file__) → src → mcp-server → talky_root  (three `..`)
_TALKY_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
if _TALKY_ROOT not in sys.path:
    sys.path.insert(0, _TALKY_ROOT)

from loguru import logger  # noqa: E402
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
)
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyManual
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
    UserTurnStoppedMessage,
)
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from pipecat_mcp_server.mcp_driver_llm_service import MCPDriverLLMService


def _instantiate_llm_backend(pm: Any, backend_name: str) -> Any:
    """Build a fresh LLM service instance for the named backend."""
    backend = pm.get_llm_backend(backend_name)
    if backend is None:
        raise ValueError(f"backend {backend_name!r} not configured")

    module_path = ".".join(backend.service_class.split(".")[:-1])
    class_name = backend.service_class.split(".")[-1]
    if not module_path.startswith("server.") and not module_path.startswith("."):
        module_path = f"server.{module_path}"
    llm_module = importlib.import_module(module_path)
    return getattr(llm_module, class_name)(**backend.config)


class VoiceChannel:
    """A single in-process voice pipeline with speak/listen operations.

    One instance per MCP server process. Attach a WebRTC connection, then
    call :meth:`speak` / :meth:`listen` to drive it. Only one pipeline is
    active at a time — if a second connection arrives while the first is
    live, the new one replaces the old one (last-writer-wins semantics).
    """

    # Special profile name that refers to the MCPDriverLLMService null
    # passthrough (used for external-agent-driven mode). Chosen so it
    # can't collide with user-configured LLM backend names.
    MCP_DRIVER_PROFILE = "__mcp__"

    def __init__(self) -> None:
        # Pre-warmed state (populated by warmup())
        self._warm = False
        self._warm_profile_name: Optional[str] = None
        self._warm_voice_prompt: Optional[str] = None

        # Active pipeline state (populated by attach(), cleared by detach())
        self._transport: Optional[SmallWebRTCTransport] = None
        self._pipeline_task: Optional[PipelineTask] = None
        self._runner: Optional[PipelineRunner] = None
        self._runner_task: Optional[asyncio.Task] = None
        self._voice_switcher: Optional[Any] = None  # VoiceProfileSwitcher

        # LLM switcher + profile → service map (populated by attach()).
        # _llm_switcher is the ServiceSwitcher wrapping all LLM services.
        # _llm_services maps a profile name (or MCP_DRIVER_PROFILE) to the
        # LLMService instance inside the switcher. Used by switch_to_profile
        # to look up the target service by name.
        self._llm_switcher: Optional[Any] = None  # LLMSwitcher
        self._llm_services: dict[str, Any] = {}  # profile name → LLMService
        self._active_profile: Optional[str] = None

        # Speech buffer — written by the turn-stopped handler, drained by listen()
        self._user_speech_queue: asyncio.Queue[Any] = asyncio.Queue()

        # Disconnect sentinel — signals listen() callers when the peer goes away
        self._disconnected: asyncio.Event = asyncio.Event()

        # Attach lock — prevents two concurrent connections from racing to build
        # a pipeline on top of each other.
        self._attach_lock: asyncio.Lock = asyncio.Lock()

    # ── warmup ──────────────────────────────────────────────────────────────

    def warmup(self) -> None:
        """Pre-load profile config / voice prompt so per-connection construction
        is fast. Safe to call multiple times.
        """
        if self._warm:
            return

        t0 = time.monotonic()
        from shared.profile_manager import get_profile_manager

        pm = get_profile_manager()
        self._warm_profile_name = pm.get_default_voice_profile()
        vp = pm.get_voice_profile(self._warm_profile_name)
        if vp is None:
            raise RuntimeError(
                f"Default voice profile '{self._warm_profile_name}' not found. "
                "Run `talky config` to create one."
            )

        # Lazy import so the channel module can be imported without the full
        # voice-prompt module loaded.
        try:
            from server.config.voice_prompts import VOICE_PROMPT

            self._warm_voice_prompt = VOICE_PROMPT
        except ImportError:
            self._warm_voice_prompt = None

        self._warm = True
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            f"VoiceChannel warm (profile={self._warm_profile_name}, {elapsed_ms:.0f}ms)"
        )

    # ── status ──────────────────────────────────────────────────────────────

    def is_live(self) -> bool:
        """True iff a pipeline is currently running.

        Checks the task and pipeline state directly. Does not trust a flag —
        see the d5e6 post-mortem in ``tickets/spike-notes/architecture-coherence.md``.
        """
        if self._pipeline_task is None:
            return False
        if self._pipeline_task.has_finished():
            return False
        if self._runner_task is not None and self._runner_task.done():
            return False
        return True

    def status(self) -> dict:
        """Return channel state for debugging / introspection."""
        return {
            "warm": self._warm,
            "live": self.is_live(),
            "voice_profile": self._warm_profile_name,
            "active_llm_profile": self._active_profile,
            "available_llm_profiles": sorted(self._llm_services.keys()) if self._llm_services else [],
            "queue_depth": self._user_speech_queue.qsize(),
            "disconnected": self._disconnected.is_set(),
        }

    async def switch_to_profile(self, profile_name: str) -> None:
        """Flip the active LLM in the LLMSwitcher to ``profile_name``."""
        if not self.is_live() or self._pipeline_task is None:
            raise RuntimeError("no live pipeline")
        service = self._llm_services.get(profile_name)
        if service is None:
            raise ValueError(f"unknown profile {profile_name!r}; available: {sorted(self._llm_services)}")

        from pipecat.frames.frames import ManuallySwitchServiceFrame
        await self._pipeline_task.queue_frames([ManuallySwitchServiceFrame(service=service)])
        self._active_profile = profile_name
        logger.info(f"VoiceChannel: active profile → {profile_name!r}")

    # ── attach / detach ─────────────────────────────────────────────────────

    async def attach(self, connection: SmallWebRTCConnection) -> None:
        """Build and start a pipeline for the given WebRTC connection.

        Called from the ``on_connection`` callback passed to
        ``SmallWebRTCRequestHandler.handle_web_request``. If a pipeline is
        already running, it's torn down first (last-writer-wins).
        """
        async with self._attach_lock:
            if self.is_live():
                logger.info("VoiceChannel.attach: tearing down previous pipeline first")
                await self._teardown_locked()

            # Fresh sentinel + queue per connection so leftover state from
            # the previous session can't leak into this one.
            self._disconnected = asyncio.Event()
            self._user_speech_queue = asyncio.Queue()

            await self._build_and_start_pipeline(connection)

    async def detach(self) -> None:
        """Tear down the active pipeline and release the WebRTC connection."""
        async with self._attach_lock:
            await self._teardown_locked()

    async def _teardown_locked(self) -> None:
        """Cancel the pipeline task. Caller must hold ``_attach_lock``."""
        self._disconnected.set()
        task = self._pipeline_task
        runner_task = self._runner_task
        self._pipeline_task = None
        self._runner_task = None
        self._transport = None
        self._voice_switcher = None
        self._llm_switcher = None
        self._llm_services = {}
        self._active_profile = None

        if task is not None:
            try:
                await task.cancel()
            except Exception as e:
                logger.debug(f"VoiceChannel teardown: task.cancel() raised {e!r}")
        if runner_task is not None and not runner_task.done():
            runner_task.cancel()
            try:
                await runner_task
            except (asyncio.CancelledError, Exception):
                pass

    # ── speak / listen ──────────────────────────────────────────────────────

    async def speak(self, text: str) -> None:
        """Queue ``text`` to be spoken by the TTS half of the active pipeline.

        Raises ``RuntimeError`` if no pipeline is attached. Does not wait for
        the audio to play on the peer.
        """
        if not self.is_live() or self._pipeline_task is None:
            raise RuntimeError(
                "VoiceChannel.speak called with no active pipeline. "
                "Call start_convo() first, or wait for the browser to connect."
            )

        await self._pipeline_task.queue_frames(
            [
                LLMFullResponseStartFrame(),
                LLMTextFrame(text=text),
                LLMFullResponseEndFrame(),
            ]
        )

    async def listen(self) -> dict:
        """Wait for at least one user utterance and return it.

        Blocks until either (a) an utterance arrives on the speech queue, or
        (b) the WebRTC peer disconnects (raises ``RuntimeError``). After the
        first utterance, drains any additional already-buffered utterances.

        Returns a dict with ``text`` (combined string) and ``segments`` (list
        of per-utterance dicts with ``text`` and ``timestamp``).
        """
        if not self.is_live():
            raise RuntimeError(
                "VoiceChannel.listen called with no active pipeline. "
                "Call start_convo() first and connect the browser."
            )

        # Race the speech queue against the disconnect event so a browser
        # reload doesn't leave us blocked forever (this is the d5e6 fix
        # re-expressed at the right layer).
        first_task = asyncio.create_task(self._user_speech_queue.get())
        disc_task = asyncio.create_task(self._disconnected.wait())
        try:
            done, pending = await asyncio.wait(
                {first_task, disc_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (first_task, disc_task):
                if not t.done():
                    t.cancel()

        if disc_task in done and first_task not in done:
            raise RuntimeError("WebRTC peer disconnected during listen()")

        first = first_task.result()
        segments = [first]

        # Drain whatever else was buffered (non-blocking).
        while not self._user_speech_queue.empty():
            try:
                segments.append(self._user_speech_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        combined = " ".join(seg["text"] for seg in segments if seg.get("text"))
        return {"text": combined, "segments": segments}

    # ── shutdown ────────────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Lifespan-shutdown hook. Tears down any active pipeline."""
        await self.detach()
        logger.info("VoiceChannel shutdown complete")

    # ── pipeline construction ──────────────────────────────────────────────

    async def _build_and_start_pipeline(
        self, connection: SmallWebRTCConnection
    ) -> None:
        """Build a fresh pipeline bound to ``connection`` and start it.

        Assumes ``_attach_lock`` is held by the caller.
        """
        if not self._warm:
            self.warmup()

        from server.features.voice_switcher import VoiceProfileSwitcher
        from shared.profile_manager import get_profile_manager
        from shared.service_factory import create_stt_service_from_config

        t0 = time.monotonic()

        pm = get_profile_manager()
        profile_name = pm.get_default_voice_profile()
        vp = pm.get_voice_profile(profile_name)
        if vp is None:
            raise RuntimeError(f"Voice profile '{profile_name}' disappeared")

        # STT, fresh per pipeline (modules are already loaded).
        stt = create_stt_service_from_config(vp.stt_provider, model=vp.stt_model)

        # LLMSwitcher slot: MCPDriver first (default active) then every
        # configured backend. See ticket ea77 / c3a1.
        mcp_driver = MCPDriverLLMService(user_speech_queue=self._user_speech_queue)
        llm_services: list = [mcp_driver]
        profile_map: dict[str, Any] = {self.MCP_DRIVER_PROFILE: mcp_driver}

        for backend_name in pm.list_llm_backends().keys():
            try:
                svc = _instantiate_llm_backend(pm, backend_name)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"VoiceChannel: skipping LLM profile {backend_name!r}: {e}")
                continue
            llm_services.append(svc)
            profile_map[backend_name] = svc

        llm_switcher = LLMSwitcher(llms=llm_services, strategy_type=ServiceSwitcherStrategyManual)
        logger.info(
            f"VoiceChannel: LLMSwitcher ready — profiles={list(profile_map.keys())}, "
            f"active={self.MCP_DRIVER_PROFILE}"
        )

        # Transport bound to the browser's WebRTC connection.
        transport = SmallWebRTCTransport(
            webrtc_connection=connection,
            params=TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
            ),
        )

        # VoiceProfileSwitcher owns TTS via a ServiceSwitcher. Task is bound
        # after PipelineTask is constructed because the switcher needs a task
        # reference for ManuallySwitchServiceFrame routing.
        voice_switcher = VoiceProfileSwitcher(profile_name, pm, task=None)
        tts_switcher = voice_switcher.get_service_switcher()

        # Aggregator with c1a2 fix (SpeechTimeoutUserTurnStopStrategy instead
        # of the default smart-turn ML analyzer that cuts users off mid-sentence).
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

        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                user_aggregator,
                llm_switcher,
                tts_switcher,
                transport.output(),
                assistant_aggregator,
            ]
        )

        # 9e7d fix: disable pipecat's default 5-minute idle timeout so the
        # session doesn't self-cancel when the user steps away.
        pipeline_task = PipelineTask(
            pipeline,
            params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
            idle_timeout_secs=None,
            cancel_on_idle_timeout=False,
        )
        voice_switcher.set_task(pipeline_task)

        # b3c4: descending cue when the user finishes speaking. Shared with
        # the local-audio daemon via shared.audio_cues.
        from shared.audio_cues import stop_cue_pcm

        _stop_cue_bytes = stop_cue_pcm(16000)

        # Voice profile switcher RTVI handlers.
        @pipeline_task.rtvi.event_handler("on_client_ready")
        async def on_client_ready(rtvi):  # noqa: ANN001
            logger.info("VoiceChannel: RTVI client ready")
            if self._warm_voice_prompt:
                context.messages.append(
                    {"role": "system", "content": self._warm_voice_prompt}
                )
            # Optional: queue a greeting frame here if desired. Leaving it off
            # so `convo_speak` is the explicit trigger.

        @pipeline_task.rtvi.event_handler("on_client_message")
        async def on_client_message(rtvi, msg):  # noqa: ANN001
            """Route voice-switcher RTVI messages from the browser."""
            await voice_switcher.handle_message(rtvi, msg)

        # Transport lifecycle → channel lifecycle.
        @transport.event_handler("on_client_connected")
        async def on_connected(transport, client):  # noqa: ANN001, ARG001
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(f"VoiceChannel: client connected ({elapsed_ms:.0f}ms to build)")

        @transport.event_handler("on_client_disconnected")
        async def on_disconnected(transport, client):  # noqa: ANN001, ARG001
            logger.info("VoiceChannel: client disconnected")
            # Flag the disconnect so listen() unblocks cleanly. Actual
            # pipeline teardown happens via detach() when the outer handler
            # decides it's time.
            self._disconnected.set()

        # User-turn events → audio cue only. The speech queue push is now
        # handled inside MCPDriverLLMService (via LLMContextFrame), which
        # keeps the "what happens when the user finishes talking" logic in
        # one place. This event handler is only kept for the b3c4 descending
        # cue, which is a pure side effect of turn completion that doesn't
        # belong inside an LLM service.
        @user_aggregator.event_handler("on_user_turn_stopped")
        async def on_user_turn_stopped(
            aggregator, strategy, message: UserTurnStoppedMessage
        ):  # noqa: ANN001, ARG001
            if not message.content:
                return

            try:
                await pipeline_task.queue_frame(
                    OutputAudioRawFrame(
                        audio=_stop_cue_bytes,
                        sample_rate=16000,
                        num_channels=1,
                    )
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"VoiceChannel: could not queue stop cue: {e}")

        # Commit to state *before* starting the runner so anything the runner
        # emits immediately is visible via is_live().
        self._transport = transport
        self._pipeline_task = pipeline_task
        self._voice_switcher = voice_switcher
        self._llm_switcher = llm_switcher
        self._llm_services = profile_map
        self._active_profile = self.MCP_DRIVER_PROFILE
        self._runner = PipelineRunner(handle_sigint=False)
        self._runner_task = asyncio.create_task(self._runner.run(pipeline_task))

        # Add a done callback so a crashed runner flips us back to "not live"
        # cleanly, instead of leaving stale refs.
        def _on_runner_done(t: asyncio.Task) -> None:
            if t is not self._runner_task:
                return  # superseded by a newer attach
            if t.cancelled():
                logger.debug("VoiceChannel: runner task cancelled")
            elif t.exception() is not None:
                logger.error(f"VoiceChannel: runner task failed: {t.exception()}")
            else:
                logger.info("VoiceChannel: runner task finished cleanly")
            self._disconnected.set()

        self._runner_task.add_done_callback(_on_runner_done)

        build_ms = (time.monotonic() - t0) * 1000
        logger.info(f"VoiceChannel: pipeline built and running in {build_ms:.0f}ms")
