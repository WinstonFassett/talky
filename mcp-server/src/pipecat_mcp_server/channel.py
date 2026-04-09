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

    def __init__(self, idle_ttl_seconds: Optional[float] = None) -> None:
        """Construct an unwarmed channel.

        Args:
            idle_ttl_seconds: Teardown delay once the room transitions
                to empty (no browser peer AND no joined agents). ``None``
                means infinity — preserve legacy behavior. Ticket 0c5d.
        """
        self._idle_ttl_seconds: Optional[float] = idle_ttl_seconds
        self._ttl_task: Optional[asyncio.Task] = None

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
        self._llm_switcher: Optional[Any] = None  # LLMSwitcher
        self._llm_services: dict[str, Any] = {}  # profile name → LLMService
        self._active_profile: Optional[str] = None

        # Room membership (ticket 3f12 phase 1). The set of agent identifiers
        # currently "joined" to the room. Agents call join_convo / leave_convo
        # to manage membership. Currently advisory — not enforced by
        # convo_speak / convo_listen yet. One-at-a-time limit enforced by
        # join_convo raising if someone else is already joined.
        self._joined_agents: set[str] = set()

        # Speech buffer — written by MCPDriverLLMService, drained by listen()
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
        ttl_pending = self._ttl_task is not None and not self._ttl_task.done()
        return {
            "warm": self._warm,
            "live": self.is_live(),
            "voice_profile": self._warm_profile_name,
            "active_llm_profile": self._active_profile,
            "available_llm_profiles": sorted(self._llm_services.keys()) if self._llm_services else [],
            "joined_agents": sorted(self._joined_agents),
            "queue_depth": self._user_speech_queue.qsize(),
            "disconnected": self._disconnected.is_set(),
            "idle_ttl_seconds": self._idle_ttl_seconds,
            "idle_ttl_pending": ttl_pending,
            "is_empty": self._is_empty(),
        }

    # ── room membership (3f12 phase 1) ──────────────────────────────────────

    def _is_empty(self) -> bool:
        """Room occupancy check: no browser peer AND no joined agents.

        Used to drive idle-TTL scheduling (ticket 0c5d).
        """
        return not self.is_live() and len(self._joined_agents) == 0

    def _schedule_ttl_if_empty(self) -> None:
        """Start the idle teardown timer if the room is empty and TTL is set.

        No-op if:
          - TTL is disabled (``_idle_ttl_seconds`` is None — infinity)
          - a timer is already scheduled
          - the room is not empty

        Safe to call repeatedly.
        """
        if self._idle_ttl_seconds is None:
            return
        if self._ttl_task is not None and not self._ttl_task.done():
            return
        if not self._is_empty():
            return
        self._ttl_task = asyncio.create_task(self._ttl_countdown())

    def _cancel_ttl(self) -> None:
        """Cancel any pending idle TTL timer. Safe to call repeatedly."""
        if self._ttl_task is not None and not self._ttl_task.done():
            self._ttl_task.cancel()
        self._ttl_task = None

    async def _ttl_countdown(self) -> None:
        """Sleep for the TTL interval, then tear down the room.

        Cancelled by ``_cancel_ttl`` (e.g. on rejoin or browser attach)
        before it fires.
        """
        try:
            assert self._idle_ttl_seconds is not None  # checked by caller
            await asyncio.sleep(self._idle_ttl_seconds)
            # Re-check occupancy before tearing down — the room could
            # have filled up during the sleep window if _cancel_ttl
            # raced us.
            if not self._is_empty():
                logger.debug(
                    "VoiceChannel: idle TTL fired but room is no longer empty — skipping teardown"
                )
                return
            logger.info(
                f"VoiceChannel: idle TTL fired after {self._idle_ttl_seconds}s — tearing down empty room"
            )
            await self.detach()
        except asyncio.CancelledError:
            logger.debug("VoiceChannel: idle TTL cancelled before fire")
            raise

    def join_convo(self, agent_id: str) -> dict:
        """Register an agent as a driver of the room.

        Multiple agents may be joined simultaneously. The common case
        is one at a time — clients swap between them — but nothing
        here enforces that. Agents who want exclusive access should
        coordinate out of band.

        Re-joining with the same agent_id is idempotent. Any pending
        idle-TTL timer is cancelled as a side effect (room is no longer
        empty).
        """
        self._joined_agents.add(agent_id)
        self._cancel_ttl()
        logger.info(f"VoiceChannel: agent {agent_id!r} joined (members: {sorted(self._joined_agents)})")
        return self.status()

    def leave_convo(self, agent_id: str) -> dict:
        """Unregister an agent. Idempotent on not-joined.

        If the room becomes empty as a result of this leave, schedules
        the idle-TTL teardown (ticket 0c5d). A no-op leave on a room
        that was already empty does NOT schedule a timer — TTL is only
        started on *transitions* into the empty state.
        """
        if agent_id in self._joined_agents:
            self._joined_agents.remove(agent_id)
            logger.info(f"VoiceChannel: agent {agent_id!r} left (members: {sorted(self._joined_agents)})")
            self._schedule_ttl_if_empty()
        else:
            logger.debug(f"VoiceChannel: leave_convo no-op for {agent_id!r} (not joined)")
        return self.status()

    def available_profiles(self) -> list[str]:
        """List of profile names the channel will accept for ``switch_to_profile``.

        Works whether or not a pipeline is live — reads from the profile
        manager directly so a user can preselect a profile before the
        browser is open.
        """
        from shared.profile_manager import get_profile_manager

        try:
            backends = list(get_profile_manager().list_llm_backends().keys())
        except Exception:  # noqa: BLE001
            backends = []
        return [self.MCP_DRIVER_PROFILE, *backends]

    async def switch_to_profile(self, profile_name: str) -> None:
        """Flip the active LLM profile.

        If a pipeline is live, queues a ``ManuallySwitchServiceFrame`` to
        flip routing inside the LLMSwitcher. If no pipeline is live yet,
        just stores the desired profile on the channel; the next pipeline
        build will auto-apply it (Phase 2 restore path). Either way,
        ``_active_profile`` is updated.

        Raises ``ValueError`` if the profile isn't known.
        """
        if profile_name not in self.available_profiles():
            raise ValueError(
                f"unknown profile {profile_name!r}; available: {self.available_profiles()}"
            )

        if not self.is_live() or self._pipeline_task is None:
            # Soft path: no live pipeline. Store the desired profile and
            # let the next build apply it.
            self._active_profile = profile_name
            logger.info(
                f"VoiceChannel: profile {profile_name!r} stored as desired "
                "(no live pipeline — will apply on next browser connect)"
            )
            return

        # Live path: queue the switch frame.
        service = self._llm_services.get(profile_name)
        if service is None:
            # Shouldn't happen given the validation above, but be defensive.
            raise ValueError(
                f"profile {profile_name!r} valid but not in active switcher"
            )

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
            # A browser peer is arriving — cancel any pending idle TTL
            # from the empty state we're leaving (ticket 0c5d).
            self._cancel_ttl()

            if self.is_live():
                logger.info("VoiceChannel.attach: tearing down previous pipeline first")
                await self._teardown_locked()

            # Fresh sentinel + queue per connection so leftover state from
            # the previous session can't leak into this one.
            self._disconnected = asyncio.Event()
            self._user_speech_queue = asyncio.Queue()

            await self._build_and_start_pipeline(connection)

    async def detach(self) -> None:
        """Tear down the active pipeline and clear room state.

        Explicit detach (e.g. from `end_convo`) clears everything:
        pipeline, joined agents, active profile. Contrast with the
        browser-disconnect path, which keeps room state so the next
        browser connection can pick up where it left off.
        """
        # Cancel any pending idle-TTL timer first so it can't race with
        # the teardown (and can't try to call detach() recursively).
        self._cancel_ttl()
        async with self._attach_lock:
            await self._teardown_locked(preserve_room_state=False)

    async def _disconnect_cleanup(self) -> None:
        """Tear down the pipeline but preserve room state.

        Triggered by the transport's ``on_client_disconnected`` event
        when the browser peer goes away. The next ``attach()`` will
        rebuild a fresh pipeline and restore the saved profile.

        If no agents are joined, the room becomes empty as a result —
        schedule the idle-TTL teardown (ticket 0c5d).
        """
        async with self._attach_lock:
            await self._teardown_locked(preserve_room_state=True)
        # Outside the lock: the room may be empty now. Schedule TTL if so.
        self._schedule_ttl_if_empty()

    async def _restore_profile_on_startup(self, profile_name: str) -> None:
        """Switch the active LLM to ``profile_name`` shortly after a
        pipeline starts, used to restore the room's last active profile
        across a disconnect/reconnect cycle. Best-effort — logs and
        swallows errors (no-op if pipeline isn't live by the time the
        task runs).
        """
        # Small delay to let the runner get past initial StartFrame
        # propagation before we queue a switch frame.
        await asyncio.sleep(0.1)
        try:
            await self.switch_to_profile(profile_name)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"VoiceChannel: could not restore profile {profile_name!r}: {e}")

    async def _teardown_locked(self, preserve_room_state: bool = False) -> None:
        """Cancel the pipeline task. Caller must hold ``_attach_lock``.

        Args:
            preserve_room_state: If True, keep ``_joined_agents`` and
                ``_active_profile`` so the next attach() can restore them.
                Used when the browser peer disconnects but the room should
                survive (ticket 3f12 phase 2). If False (explicit detach /
                shutdown), all state is cleared.
        """
        self._disconnected.set()
        task = self._pipeline_task
        runner_task = self._runner_task
        self._pipeline_task = None
        self._runner_task = None
        self._transport = None
        self._voice_switcher = None
        self._llm_switcher = None
        # Per-pipeline LLM service instances can't be reused across rebuilds
        # (pipecat services are pipeline-bound), so clear them regardless.
        self._llm_services = {}

        if not preserve_room_state:
            self._joined_agents = set()
            self._active_profile = None
        # If preserving: joined_agents stays, and _active_profile stays as the
        # "desired profile" that the next attach() will switch back to.

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
            logger.info("VoiceChannel: client disconnected — tearing down pipeline, preserving room state")
            # Phase 2 (3f12): tear down the pipeline when the peer goes
            # away, but preserve room state (joined_agents, active_profile)
            # so the next attach() can restore it. Pipecat services can't
            # be reused across pipeline rebuilds, so the pipeline itself
            # has to be rebuilt — but from the user's perspective, closing
            # and reopening the browser should feel seamless.
            asyncio.create_task(self._disconnect_cleanup())

        # NB: No on_user_turn_stopped handler here.
        # Speech queue writes are handled by MCPDriverLLMService via
        # LLMContextFrame. Audio cues on turn-stop were removed per ticket
        # b5ee — they're only wanted in the walkie-talkie `ask` path.

        # Remember the profile the room had before (possibly from a
        # previous pipeline that got torn down on disconnect). Default
        # to MCP_DRIVER if none saved.
        desired_profile = self._active_profile or self.MCP_DRIVER_PROFILE

        # Commit to state *before* starting the runner so anything the runner
        # emits immediately is visible via is_live().
        self._transport = transport
        self._pipeline_task = pipeline_task
        self._voice_switcher = voice_switcher
        self._llm_switcher = llm_switcher
        self._llm_services = profile_map
        self._active_profile = self.MCP_DRIVER_PROFILE  # will be re-set below
        self._runner = PipelineRunner(handle_sigint=False)
        self._runner_task = asyncio.create_task(self._runner.run(pipeline_task))

        # If the room had a non-default profile active before the disconnect,
        # switch back to it on this new pipeline. Fire-and-forget — the switch
        # frame will be processed as soon as the runner picks it up.
        if desired_profile != self.MCP_DRIVER_PROFILE and desired_profile in profile_map:
            asyncio.create_task(self._restore_profile_on_startup(desired_profile))

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
