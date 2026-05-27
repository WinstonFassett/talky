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
| Browser disconnect / reload    | Transport event → :meth:`_disconnect_cleanup` |
| ``detach()``                   | Cancel pipeline task, clear refs            |
| ``request_leave`` (agent exit) | :meth:`request_leave` — signoff + grace     |
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
    UserStartedSpeakingFrame,
)
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyManual
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_response_universal import (
    LLMUserAggregatorParams,
)
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from pipecat_mcp_server.agent_ext_llm_service import AgentExtensionLLMService
from pipecat_mcp_server.mcp_driver_llm_service import MCPDriverLLMService
from pipecat_mcp_server.talky_turn import TalkyUserTurnDetector


def _instantiate_llm_backend(pm: Any, backend_name: str) -> Any:
    """Build a fresh LLM service instance for the named backend."""
    import json as _json
    from pathlib import Path as _Path

    backend = pm.get_llm_backend(backend_name)
    if backend is None:
        raise ValueError(f"backend {backend_name!r} not configured")

    config = dict(backend.config or {})

    # One-shot startup config: read ~/.talky/run/talky-args.json if present.
    # Only inject resume into backends whose constructor accepts it; delete the
    # file only after successful injection so it survives to the right backend.
    startup_path = _Path.home() / ".talky" / "run" / "talky-args.json"
    _startup_resume: str | None = None
    _startup_cwd: str | None = None
    _startup_bypass_permissions: bool = False
    if startup_path.exists():
        try:
            startup = _json.loads(startup_path.read_text())
            resume_cfg = startup.get("resume") or {}
            if resume_cfg.get("backend") == backend_name:
                _startup_resume = resume_cfg.get("session_id")
                _startup_cwd = resume_cfg.get("cwd")
            _startup_bypass_permissions = bool(startup.get("bypass_permissions", False))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to read talky-args.json: {e}")

    module_path = ".".join(backend.service_class.split(".")[:-1])
    class_name = backend.service_class.split(".")[-1]
    if not module_path.startswith("server.") and not module_path.startswith("pipecat_mcp_server.") and not module_path.startswith("."):
        module_path = f"server.{module_path}"
    llm_module = importlib.import_module(module_path)
    cls = getattr(llm_module, class_name)

    import inspect as _inspect
    sig = _inspect.signature(cls.__init__).parameters

    if _startup_bypass_permissions and "permission_mode" in sig:
        config["permission_mode"] = "bypassPermissions"
        logger.warning("claude-bg: bypass_permissions=true from talky-args.json — skipping all permission checks")

    if _startup_resume:
        if "resume" in sig:
            config["resume"] = _startup_resume
            if _startup_cwd and "cwd" in sig:
                config["cwd"] = _startup_cwd
            try:
                startup_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass

    return cls(**config)


class VoiceChannel:
    """A single in-process voice pipeline with speak/listen operations.

    One instance per MCP server process. Attach a WebRTC connection, then
    call :meth:`speak` / :meth:`listen` to drive it. Only one pipeline is
    active at a time — if a second connection arrives while the first is
    live, the new one replaces the old one (last-writer-wins semantics).
    """

    # Internal profile name for the MCPDriverLLMService null passthrough.
    MCP_DRIVER_PROFILE = "__mcp__"

    def __init__(
        self,
        idle_ttl_seconds: Optional[float] = None,
        request_leave_grace_seconds: float = 4.0,
    ) -> None:
        """Construct an unwarmed channel.

        Args:
            idle_ttl_seconds: Teardown delay once the room transitions
                to empty (no browser peer). ``None`` means infinity —
                preserve legacy behavior. Ticket 0c5d.
            request_leave_grace_seconds: Grace window for
                ``request_leave`` to wait on user speech before actually
                leaving. User-configured, not agent-controlled. Ticket
                0b80 / follow-up rip.
        """
        self._idle_ttl_seconds: Optional[float] = idle_ttl_seconds
        self._request_leave_grace_seconds: float = request_leave_grace_seconds
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

        # Speech buffer — written by MCPDriverLLMService, drained by listen()
        self._user_speech_queue: asyncio.Queue[Any] = asyncio.Queue()

        # Speech-onset sentinel — set when VAD detects the user started
        # speaking, cleared on pipeline teardown. Used by request_leave
        # to detect speech ONSET during the grace window rather than
        # waiting for a completed utterance. Ticket 0b80 bug fix.
        self._user_speaking: asyncio.Event = asyncio.Event()

        # Disconnect sentinel — signals listen() callers when the peer goes away
        self._disconnected: asyncio.Event = asyncio.Event()

        # Attach lock — prevents two concurrent connections from racing to build
        # a pipeline on top of each other.
        self._attach_lock: asyncio.Lock = asyncio.Lock()

        # Health state per backend — populated by _health_poll_loop.
        self._health: dict[str, bool] = {}
        self._health_task: Optional[asyncio.Task] = None

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
            "queue_depth": self._user_speech_queue.qsize(),
            "disconnected": self._disconnected.is_set(),
            "idle_ttl_seconds": self._idle_ttl_seconds,
            "idle_ttl_pending": ttl_pending,
            "is_empty": self._is_empty(),
        }

    # ── room occupancy & TTL ────────────────────────────────────────────────

    def _is_empty(self) -> bool:
        """Room occupancy check: empty iff no browser peer is attached.

        Drives idle-TTL scheduling (ticket 0c5d). Previously also gated
        on "joined agents" state (3f12 phase 1), but that data structure
        was ripped because it had no user requirement behind it — the
        TTL depends entirely on whether a user (browser peer) is in the
        room.
        """
        return not self.is_live()

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

    def join_convo(self) -> dict:
        """Check in to the conversation.

        Returns channel status so the caller can verify the pipeline is
        live, inspect the active profile, etc. No state mutation — the
        old room-membership set was ripped because it had no user
        requirement behind it (speculative 3f12 phase 1 data structure).
        Kept as a tool because agents still want an explicit "I'm here"
        ritual before they start driving the conversation.
        """
        return self.status()

    async def request_leave(self) -> dict:
        """Politely announce intent to leave the convo. Ticket 0b80.

        Flow:
            1. Play the active profile's signoff phrase via TTS (if any).
            2. Play the descending-beep audio cue (ticket 6b60 problem A).
            3. Wait for the configured grace window (user-controlled,
               not agent-controlled — see ``_request_leave_grace_seconds``).
            4. If the user speaks during the grace window, return
               ``{"left": False, "user_interrupted": True, "text": ...}``
               and the caller should resume the conversation.
            5. Otherwise return ``{"left": True, ...}`` and the caller
               is expected to stop driving.

        No state is mutated on the channel — "leaving" is an agent-side
        behavior (stop calling convo_speak / convo_listen). The channel
        only orchestrates the signoff ritual and reports whether the
        user objected.

        Grace window is configured via
        ``TALKY_REQUEST_LEAVE_GRACE_SECS`` env var /
        ``room.request_leave_grace_seconds`` in ``~/.talky/settings.yaml``.
        A value of ``0`` disables the ceremony entirely.
        """
        if not self.is_live():
            return {
                "left": True,
                "user_interrupted": False,
                "reason": "pipeline not live",
            }

        grace = self._request_leave_grace_seconds
        if grace <= 0:
            return {
                "left": True,
                "user_interrupted": False,
                "reason": "grace disabled (grace_seconds <= 0)",
            }

        # 1. Signoff phrase (skipped for MCP driver — agent supplies its own voice).
        signoff = self._signoff_for_profile(self._active_profile)
        if signoff:
            try:
                await self.speak(signoff)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"VoiceChannel.request_leave: signoff speak failed: {e}")

        # 2. Descending-beep cue. Always played, even for MCP driver
        #    (the beep is the cue that tells the user somebody is leaving).
        try:
            from shared.audio_cues import stop_cue_pcm

            await self._inject_cue(stop_cue_pcm())
        except Exception as e:  # noqa: BLE001
            logger.warning(f"VoiceChannel.request_leave: cue inject failed: {e}")

        # 3. Grace window — detect speech ONSET, not a completed utterance.
        #
        # The old approach raced listen() (which waits for a full
        # transcribed turn) against the grace timeout. If the user
        # started speaking at second 3 of a 4-second window but the
        # turn took 3 seconds to transcribe, the timeout won and the
        # system left while the user was mid-sentence. Bug confirmed
        # live on 2026-04-09.
        #
        # New approach: race _user_speaking (set on VAD speech onset)
        # against the timeout. If the user makes ANY sound during the
        # grace window, wait for their full utterance with no timeout.
        self._user_speaking.clear()
        onset_task = asyncio.create_task(self._user_speaking.wait())
        disc_task = asyncio.create_task(self._disconnected.wait())
        try:
            done, _ = await asyncio.wait(
                {onset_task, disc_task},
                timeout=grace,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (onset_task, disc_task):
                if not t.done():
                    t.cancel()

        if disc_task in done:
            # Peer disconnected during grace — nothing to do, leave.
            logger.debug("VoiceChannel.request_leave: peer disconnected during grace")
            return {
                "left": True,
                "user_interrupted": False,
                "grace_seconds": grace,
            }

        if onset_task in done:
            # User started speaking — wait for the full utterance,
            # no timeout. They spoke up; we owe them the floor.
            logger.info("VoiceChannel.request_leave: speech onset detected during grace — waiting for full utterance")
            try:
                spoken = await self.listen()
                logger.info(
                    f"VoiceChannel.request_leave: user interrupted ({spoken.get('text', '')!r}) — staying"
                )
                return {
                    "left": False,
                    "user_interrupted": True,
                    "text": spoken.get("text", ""),
                }
            except RuntimeError as e:
                logger.debug(f"VoiceChannel.request_leave: listen raised after onset: {e}")
                return {
                    "left": True,
                    "user_interrupted": False,
                    "grace_seconds": grace,
                }

        # Timeout — no speech onset at all. Leave.
        return {
            "left": True,
            "user_interrupted": False,
            "grace_seconds": grace,
        }

    def available_profiles(self) -> list[str]:
        """List of profile names the channel will accept for ``switch_to_profile``.

        Works whether or not a pipeline is live — reads from the profile
        manager directly so a user can preselect a profile before the
        browser is open. Includes talky profile names (which may overlap
        with LLM backend names).
        """
        from shared.profile_manager import get_profile_manager

        try:
            pm = get_profile_manager()
            # Union of talky profile names and LLM backend names.
            names = set(pm.list_talky_profiles().keys()) | set(pm.list_llm_backends().keys())
        except Exception:  # noqa: BLE001
            names = set()
        return [self.MCP_DRIVER_PROFILE, *sorted(names)]

    async def switch_to_profile(self, profile_name: str, *, announce: bool = True) -> None:
        """Flip the active LLM profile.

        If a pipeline is live, queues a ``ManuallySwitchServiceFrame`` to
        flip routing inside the LLMSwitcher. If no pipeline is live yet,
        just stores the desired profile on the channel; the next pipeline
        build will auto-apply it (Phase 2 restore path). Either way,
        ``_active_profile`` is updated.

        ``announce``: when True (default), speak the profile's
        ``announcement`` cue ("OpenClaw channel"). Callers triggered by
        an *agent joining* (e.g. /ws/agent) should pass ``announce=False``
        so the agent owns its own greeting via the TTS stream. Ticket e540.

        Raises ``ValueError`` if the profile isn't known.
        """
        if profile_name not in self.available_profiles():
            raise ValueError(
                f"unknown profile {profile_name!r}; available: {self.available_profiles()}"
            )

        # Install any missing deps for this backend before switching.
        # If the dep was just installed it won't be importable until restart —
        # we install it now so the *next* daemon start works, and warn the user.
        try:
            from shared.dependency_installer import (
                _check_extra_installed,
                get_llm_backend_extra,
                install_extra_no_reexec,
            )
            from shared.profile_manager import get_profile_manager
            pm = get_profile_manager()
            # Resolve talky profile → llm_backend name, then look up the extra.
            tp = pm.get_talky_profile(profile_name)
            backend_name = (tp.llm_backend if tp else None) or profile_name
            extra = get_llm_backend_extra(backend_name)
            if extra:
                already_installed = _check_extra_installed(extra)
                if not already_installed:
                    ok = install_extra_no_reexec(extra)
                    if ok:
                        logger.warning(
                            f"Deps for {profile_name!r} installed — restart the daemon: talky kill && talky daemon"
                        )
                    else:
                        logger.warning(f"Could not install deps for {profile_name!r} (extra: {extra!r})")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Dep check for {profile_name!r} failed: {e}")

        if not self.is_live() or self._pipeline_task is None:
            # Soft path: no live pipeline. Store the desired profile and
            # let the next build apply it.
            self._active_profile = profile_name
            logger.info(
                f"VoiceChannel: profile {profile_name!r} stored as desired "
                "(no live pipeline — will apply on next browser connect)"
            )
            await self._emit_profile_changed(profile_name)
            return

        # Live path: queue the switch frame.
        # Talky profile names map to backend names — resolve before lookup.
        from shared.profile_manager import get_profile_manager as _gpm
        _pm = _gpm()
        _tp = _pm.get_talky_profile(profile_name)
        service_key = (_tp.llm_backend if _tp and _tp.llm_backend else None) or profile_name
        service = self._llm_services.get(service_key)
        if service is None:
            raise ValueError(
                f"profile {profile_name!r} valid but not in active switcher"
            )

        from pipecat.frames.frames import ManuallySwitchServiceFrame

        await self._pipeline_task.queue_frames([ManuallySwitchServiceFrame(service=service)])
        self._active_profile = profile_name
        logger.info(f"VoiceChannel: active profile → {profile_name!r}")
        await self._emit_profile_changed(profile_name)

        # System announcement on manual picker switches only (tickets 8c9d, e540).
        # Agent-join paths pass announce=False — the agent owns its own greeting
        # via the extension's TTS stream.
        if announce:
            announcement = self._announcement_for_profile(profile_name)
            if announcement:
                try:
                    await self.speak(announcement)
                    logger.info(f"VoiceChannel: announced {announcement!r}")
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"VoiceChannel: announcement failed for {profile_name!r}: {e}")

    async def _emit_profile_changed(self, profile_name: str) -> None:
        """Push a profileChanged event to the SSE bus."""
        from pipecat_mcp_server.event_bus import event_bus

        await event_bus.emit("profileChanged", {
            "type": "llm",
            "profile": profile_name,
        })

    # ── voice profiles ───────────────────────────────────────────────────────

    def voices_info(self) -> list[dict]:
        """Return available voice profiles with current selection.

        Works without a live pipeline — reads from config.
        """
        from shared.profile_manager import get_profile_manager

        try:
            pm = get_profile_manager()
            current = (
                self._voice_switcher.get_current_profile()
                if self._voice_switcher
                else pm.get_default_voice_profile() or None
            )
            out = []
            for name, _desc in pm.list_voice_profiles().items():
                p = pm.get_voice_profile(name)
                if p is None:
                    continue
                tts_label = (
                    f"{p.tts_provider} · {p.tts_voice}"
                    if p.tts_voice
                    else p.tts_provider
                )
                stt_label = (
                    f"{p.stt_provider} · {p.stt_model}"
                    if p.stt_model
                    else p.stt_provider
                )
                out.append(
                    {
                        "name": name,
                        "description": p.description,
                        "active": name == current,
                        "tts": tts_label,
                        "stt": stt_label,
                        "provider": p.tts_provider,
                    }
                )
            return out
        except Exception:  # noqa: BLE001
            return []

    async def switch_voice(self, profile_name: str) -> None:
        """Switch the active voice profile.

        Delegates to the VoiceProfileSwitcher. Requires a live pipeline.
        Raises ``RuntimeError`` if no pipeline, ``ValueError`` if unknown.
        """
        if not self.is_live() or self._voice_switcher is None:
            raise RuntimeError("no live pipeline — connect a browser first")

        from shared.profile_manager import get_profile_manager

        pm = get_profile_manager()
        profile = pm.get_voice_profile(profile_name)
        if profile is None:
            raise ValueError(
                f"unknown voice profile {profile_name!r}"
            )

        current = pm.get_voice_profile(self._voice_switcher.current_profile)
        if current is None:
            raise RuntimeError("current voice profile not found")

        if profile.tts_provider == current.tts_provider:
            # Same provider — just change voice on existing service.
            svc = self._voice_switcher.tts_service_map.get(current.tts_provider)
            if svc and hasattr(svc, "set_voice"):
                svc.set_voice(profile.tts_voice)
            else:
                raise RuntimeError(f"TTS service for {current.tts_provider} doesn't support set_voice")
        else:
            # Cross-provider — switch via ServiceSwitcher frame.
            new_svc = self._voice_switcher.tts_service_map.get(profile.tts_provider)
            if new_svc is None:
                raise ValueError(f"TTS service for {profile.tts_provider} not available")
            if hasattr(new_svc, "set_voice"):
                new_svc.set_voice(profile.tts_voice)
            from pipecat.frames.frames import ManuallySwitchServiceFrame
            assert self._pipeline_task is not None  # guarded by is_live() above
            await self._pipeline_task.queue_frames([ManuallySwitchServiceFrame(service=new_svc)])

        self._voice_switcher.current_profile = profile_name
        logger.info(f"VoiceChannel: voice profile → {profile_name!r}")

        from pipecat_mcp_server.event_bus import event_bus
        await event_bus.emit("voiceChanged", {
            "profile": profile_name,
        })

    # ── talky profiles ──────────────────────────────────────────────────────

    def profiles_info(self) -> list[dict]:
        """Return talky profiles with metadata + health.

        Works without a live pipeline — reads from config. Each entry:
        ``{"name": str, "label": str, "description": str, "active": bool,
        "healthy": bool | None}``.

        MCP Mode is prepended as a synthetic entry (it's the raw
        receiving state, not a talky profile).
        """
        from shared.profile_manager import get_profile_manager

        active = self._active_profile
        profiles: list[dict] = [
            {
                "name": self.MCP_DRIVER_PROFILE,
                "label": "MCP Mode",
                "description": "External agent drives the conversation",
                "active": active == self.MCP_DRIVER_PROFILE,
                "healthy": self._health.get(self.MCP_DRIVER_PROFILE),
            },
        ]
        try:
            pm = get_profile_manager()
            for name, desc in pm.list_talky_profiles().items():
                # Health is keyed by the underlying LLM backend name,
                # which may differ from the talky profile name. Fall
                # back to the profile name itself for the lookup.
                tp = pm.get_talky_profile(name)
                backend_name = (tp.llm_backend if tp and tp.llm_backend else name)
                profiles.append({
                    "name": name,
                    "label": name,
                    "description": desc,
                    "active": active == name,
                    "healthy": self._health.get(backend_name),
                })
        except Exception:  # noqa: BLE001
            pass
        return profiles

    # ── health polling ──────────────────────────────────────────────────────

    def start_health_polling(self, interval: float = 30.0) -> None:
        """Launch background health polling. Call once at lifespan startup."""
        if self._health_task is not None:
            return
        self._health_task = asyncio.ensure_future(self._health_poll_loop(interval))

    async def _health_poll_loop(self, interval: float) -> None:
        """Periodically probe each configured backend and emit healthChanged."""
        from pipecat_mcp_server.event_bus import event_bus

        while True:
            try:
                for name in self.available_profiles():
                    healthy = await self._check_backend_health(name)
                    prev = self._health.get(name)
                    self._health[name] = healthy
                    if prev != healthy:
                        await event_bus.emit("healthChanged", {
                            "backend": name,
                            "healthy": healthy,
                        })
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.debug("Health poll iteration failed", exc_info=True)
            await asyncio.sleep(interval)

    async def _check_backend_health(self, name: str) -> bool:
        """Probe a single backend. Returns True if reachable."""
        if name == self.MCP_DRIVER_PROFILE:
            return True

        # AgentExtensionLLMService health = whether an extension is connected.
        svc = self._llm_services.get(name)
        if isinstance(svc, AgentExtensionLLMService):
            return svc.connected

        from shared.profile_manager import get_profile_manager

        try:
            pm = get_profile_manager()
            backend = pm.get_llm_backend(name)
            if backend is None:
                return False

            # Quick TCP connect check to the backend's gateway/endpoint.
            url = self._backend_url(name, backend)
            if url is None:
                return True  # No URL to check — assume healthy.

            return await self._tcp_probe(url)
        except Exception:  # noqa: BLE001
            return False

    def _backend_url(self, name: str, backend: Any) -> Optional[str]:
        """Extract the gateway/endpoint URL from a backend config."""
        config = getattr(backend, "config", {}) or {}
        # Backends store their URL under various keys.
        for key in ("gateway_url", "endpoint", "url", "base_url"):
            if key in config and config[key]:
                return config[key]
        return None

    @staticmethod
    async def _tcp_probe(url: str, timeout: float = 3.0) -> bool:
        """Try a TCP connect to the host:port in a URL."""
        import urllib.parse

        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme in ("https", "wss") else 80

        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _announcement_for_profile(self, profile_name: str) -> Optional[str]:
        """Look up the optional system announcement for a profile.

        Impersonal cue spoken on *manual* picker-initiated profile switches
        ("OpenClaw channel"). Not a greeting from the agent — see ticket e540.

        Returns ``None`` for the MCP driver profile, for unknown profiles,
        or for profiles without an ``announcement`` field. Tickets 8c9d, e540.
        """
        if profile_name == self.MCP_DRIVER_PROFILE:
            return None
        try:
            from shared.profile_manager import get_profile_manager

            pm = get_profile_manager()
            tp = pm.get_talky_profile(profile_name)
            backend_name = (tp.llm_backend if tp and tp.llm_backend else None) or profile_name
            backend = pm.get_llm_backend(backend_name)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"VoiceChannel: could not read backend for announcement lookup: {e}")
            return None
        if backend is None:
            return None
        return getattr(backend, "announcement", None)

    def _signoff_for_profile(self, profile_name: Optional[str]) -> Optional[str]:
        """Look up the optional fixed-string signoff phrase for a profile.

        Returns ``None`` for the MCP driver profile (agent-driven — the
        agent supplies its own voice), for unknown profiles, or for
        profiles without a ``signoff`` field. Ticket 0b80.
        """
        if profile_name is None or profile_name == self.MCP_DRIVER_PROFILE:
            return None
        try:
            from shared.profile_manager import get_profile_manager

            backend = get_profile_manager().get_llm_backend(profile_name)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"VoiceChannel: could not read backend for signoff lookup: {e}")
            return None
        if backend is None:
            return None
        return getattr(backend, "signoff", None)

    async def _inject_cue(self, pcm: bytes, sample_rate: int = 16000) -> None:
        """Queue a raw-PCM audio cue into the live pipeline and wait for it.

        Used by request_leave to play a descending-beep cue before the
        agent actually departs. No-op if the pipeline isn't live. Ticket 6b60.
        """
        if not self.is_live() or self._pipeline_task is None:
            return
        frame = OutputAudioRawFrame(
            audio=pcm,
            sample_rate=sample_rate,
            num_channels=1,
        )
        await self._pipeline_task.queue_frames([frame])
        # Let the frame actually play out before the caller tears anything
        # down. Duration of the cue plus a small buffer for transport latency.
        from shared.audio_cues import cue_duration_s

        await asyncio.sleep(cue_duration_s() + 0.1)

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

            # Notify SSE clients that the pipeline is now live so
            # pickers can re-fetch (voice active flag only resolves
            # once the VoiceProfileSwitcher exists).
            try:
                from pipecat_mcp_server.event_bus import event_bus

                await event_bus.emit("peerConnected", {
                    "profiles": self.profiles_info(),
                    "voices": self.voices_info(),
                    "live": True,
                })
            except Exception:  # noqa: BLE001
                pass

    async def detach(self) -> None:
        """Tear down the active pipeline and clear profile state.

        Explicit detach clears the pipeline and the saved active
        profile. Contrast with the browser-disconnect path, which keeps
        the active profile so the next browser connection can restore
        it.

        No longer exposed as an MCP tool (the old ``end_convo`` was
        removed in ticket 0b80). Still called by the lifespan shutdown
        hook and the idle-TTL countdown.
        """
        # Cancel any pending idle-TTL timer first so it can't race with
        # the teardown (and can't try to call detach() recursively).
        self._cancel_ttl()
        async with self._attach_lock:
            await self._teardown_locked(preserve_active_profile=False)

    async def _disconnect_cleanup(self) -> None:
        """Tear down the pipeline but preserve the active profile.

        Triggered by the transport's ``on_client_disconnected`` event
        when the browser peer goes away. The next ``attach()`` will
        rebuild a fresh pipeline and restore the saved profile.

        The room is now empty (browser peer is gone) — schedule the
        idle-TTL teardown (ticket 0c5d).
        """
        async with self._attach_lock:
            await self._teardown_locked(preserve_active_profile=True)
            # Schedule TTL inside the lock so a concurrent attach() that
            # already holds or is waiting on the lock will see the cancel
            # call in its own attach() path and not race with us.
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

    async def _teardown_locked(self, preserve_active_profile: bool = False) -> None:
        """Cancel the pipeline task. Caller must hold ``_attach_lock``.

        Args:
            preserve_active_profile: If True, keep ``_active_profile``
                so the next ``attach()`` can restore it. Used when the
                browser peer disconnects but the room should survive
                (ticket 3f12 phase 2). If False (explicit detach /
                shutdown), the profile is cleared too.
        """
        self._disconnected.set()
        self._user_speaking.clear()
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

        if not preserve_active_profile:
            self._active_profile = None
        # If preserving: _active_profile stays as the "desired profile"
        # that the next attach() will switch back to.

        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"VoiceChannel teardown: task cancel raised {e!r}")
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

        If MCP mode is not currently active (another profile is driving),
        blocks until MCP mode is restored before listening. This lets an
        MCP agent call listen() after handing off to another profile and
        automatically resume when it gets control back.

        Returns a dict with ``text`` (combined string) and ``segments`` (list
        of per-utterance dicts with ``text`` and ``timestamp``).
        """
        if not self.is_live():
            raise RuntimeError(
                "VoiceChannel.listen called with no active pipeline. "
                "Call start_convo() first and connect the browser."
            )

        # If another profile is active, wait for MCP mode to come back.
        if self._active_profile != self.MCP_DRIVER_PROFILE:
            logger.info(
                f"VoiceChannel.listen: waiting for MCP mode "
                f"(currently {self._active_profile!r})"
            )
            from pipecat_mcp_server.event_bus import event_bus

            async with event_bus.subscribe() as queue:
                while self._active_profile != self.MCP_DRIVER_PROFILE:
                    try:
                        await asyncio.wait_for(queue.get(), timeout=2.0)
                        # profileChanged events update _active_profile via
                        # switch_to_profile, so just re-check the loop condition.
                    except asyncio.TimeoutError:
                        # Check disconnect while waiting.
                        if self._disconnected.is_set():
                            raise RuntimeError(
                                "WebRTC peer disconnected while waiting for MCP mode"
                            )
                        continue

            logger.info("VoiceChannel.listen: MCP mode restored, listening")

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

        # Both tasks may have completed simultaneously; if first_task raised
        # (e.g. CancelledError on rapid disconnect), treat it as a disconnect.
        try:
            first = first_task.result()
        except Exception as e:
            raise RuntimeError(f"WebRTC peer disconnected during listen(): {e}") from e
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
        profile_map: dict[str, Any] = {
            self.MCP_DRIVER_PROFILE: mcp_driver,
        }

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

        # Pong responder — echo a JSON pong on the data channel whenever
        # the client sends a "ping". The client tracks pong arrival to
        # detect server death faster than ICE can. Sent as JSON so the
        # client's handleMessage doesn't error on parse. Ticket 6b60.
        #
        # We can't register a second @_pc.on("datachannel") handler
        # because pipecat's connection already consumed that event by
        # the time _build_and_start_pipeline runs. Instead, add a
        # second on("message") listener to the data channel after it's
        # been assigned by the connection's own datachannel handler.
        # Poll briefly for _data_channel to appear (it's set async
        # when the browser creates the channel).
        async def _setup_pong_responder():
            for _ in range(50):  # wait up to 5s
                dc = getattr(connection, "_data_channel", None)
                if dc is not None:
                    @dc.on("message")
                    def _reply_pong(message):
                        if isinstance(message, str) and message.startswith("ping"):
                            try:
                                dc.send('{"type":"pong"}')
                            except Exception:  # noqa: BLE001
                                pass
                    logger.info("VoiceChannel: pong responder attached to data channel")
                    return
                await asyncio.sleep(0.1)
            logger.warning("VoiceChannel: pong responder timed out waiting for data channel")

        asyncio.create_task(_setup_pong_responder())

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

        # Turn detector — ticket 76a3. Reuses LLMUserAggregator's VAD +
        # turn controller + transcription aggregation machinery, but
        # emits a lightweight UserTurnTextFrame instead of accumulating
        # an LLMContext. Carries the c1a2 fix (SpeechTimeoutUserTurnStop
        # instead of the smart-turn ML analyzer). No assistant aggregator
        # — talky's backends don't consume bot response history.
        turn_detector = TalkyUserTurnDetector(
            params=LLMUserAggregatorParams(
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
                turn_detector,
                llm_switcher,
                tts_switcher,
                transport.output(),
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

        # RTVI voice-switcher handlers removed — voice profile switching
        # is now REST+SSE (ticket 2ed2). The VoiceProfileSwitcher instance
        # is still used for its TTS ServiceSwitcher and profile tracking;
        # the channel's switch_voice() method drives it via REST.

        # Transport lifecycle → channel lifecycle.
        @transport.event_handler("on_client_connected")
        async def on_connected(transport, client):  # noqa: ANN001, ARG001
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(f"VoiceChannel: client connected ({elapsed_ms:.0f}ms to build)")

        @transport.event_handler("on_client_disconnected")
        async def on_disconnected(transport, client):  # noqa: ANN001, ARG001
            logger.info("VoiceChannel: client disconnected — tearing down pipeline, preserving active profile")
            # Phase 2 (3f12): tear down the pipeline when the peer goes
            # away, but preserve the active profile so the next attach()
            # can restore it. Pipecat services can't be reused across
            # pipeline rebuilds, so the pipeline itself has to be rebuilt
            # — but from the user's perspective, closing and reopening
            # the browser should feel seamless.
            asyncio.create_task(self._disconnect_cleanup())

        # Speech-onset handler — sets _user_speaking when VAD detects
        # the user started talking. Used by request_leave to detect
        # speech during the grace window without waiting for a full
        # transcribed utterance. Ticket 0b80 bug fix.
        #
        # UserStartedSpeakingFrame is a pipeline frame, not a transport
        # event — must be caught via the pipeline task's upstream filter,
        # not via transport.event_handler (which silently ignores unknown
        # event names).
        pipeline_task.add_reached_upstream_filter((UserStartedSpeakingFrame,))

        @pipeline_task.event_handler("on_frame_reached_upstream")
        async def on_user_speaking(task, frame):  # noqa: ANN001, ARG001
            if isinstance(frame, UserStartedSpeakingFrame):
                self._user_speaking.set()

        # NB: No on_user_turn_stopped handler here.
        # Speech queue writes are handled by MCPDriverLLMService via
        # UserTurnTextFrame (ticket 76a3). Audio cues on turn-stop were
        # removed per ticket b5ee — they're only wanted in the
        # walkie-talkie `ask` path.

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
        # Resolve talky profile name → backend name for profile_map lookup.
        # Pass the original desired_profile (talky name) to restore so
        # _active_profile stays consistent with talky profile names.
        _tp = pm.get_talky_profile(desired_profile) if desired_profile else None
        resolved_profile = (_tp.llm_backend if _tp and _tp.llm_backend else None) or desired_profile
        if desired_profile != self.MCP_DRIVER_PROFILE and resolved_profile in profile_map:
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
