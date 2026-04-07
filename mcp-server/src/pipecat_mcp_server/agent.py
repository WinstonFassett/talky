#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Pipecat MCP Agent for voice I/O over MCP protocol.

This module provides the `PipecatMCPAgent` class that exposes voice input/output
capabilities through MCP tools. It manages a Pipecat pipeline with STT and TTS
services, allowing an MCP client to listen for user speech and speak responses.
"""

import asyncio
import os
import sys
import time
from typing import Any, Optional

from loguru import logger
from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    EndFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.pipeline.parallel_pipeline import ParallelPipeline
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
    UserTurnStoppedMessage,
)
from pipecat.runner.types import (
    DailyRunnerArguments,
    RunnerArguments,
    SmallWebRTCRunnerArguments,
    WebSocketRunnerArguments,
)
from pipecat.runner.utils import create_transport
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from shared.service_factory import create_stt_service_from_config, create_tts_service_from_config
from shared.voice_config import (
    configure_quiet_logging,
    create_vad_analyzer,
)

# Common transport parameters for all transports
COMMON_TRANSPORT_PARAMS = {
    "audio_in_enabled": True,
    "audio_out_enabled": True,
}



class PipecatMCPAgent:
    """Pipecat MCP Agent that exposes voice I/O tools.

    Tools:
    - listen(): Wait for user speech and return transcription
    - speak(text): Speak text to the user via TTS
    """

    # Sentinel value to indicate client disconnection
    _DISCONNECT_SENTINEL = object()

    def __init__(
        self,
        transport: BaseTransport,
        runner_args: RunnerArguments,
    ):
        """Initialize the Pipecat MCP Agent.

        Args:
            transport: Transport for audio I/O (Daily, Twilio, or WebRTC).
            runner_args: Runner configuration arguments.

        """
        self._transport = transport
        self._runner_args = runner_args

        self._task: Optional[asyncio.Task] = None
        self._pipeline_task: Optional[PipelineTask] = None
        self._pipeline_runner: Optional[PipelineRunner] = None
        self._user_speech_queue: asyncio.Queue[Any] = asyncio.Queue()

        self._started = False

    def _is_live(self) -> bool:
        """True iff the pipeline is actually running right now.

        `_started` alone is a flag, not an invariant — it can lie if the
        pipeline task was cancelled behind our back (e.g. on_client_disconnected
        cancels the task, but the handler may race with listen()/speak()).
        Checking the task state directly is the source of truth.
        """
        if not self._started:
            return False
        if self._pipeline_task is None:
            return False
        if self._pipeline_task.has_finished():
            return False
        if self._task is not None and self._task.done():
            return False
        return True

    async def start(self):
        """Start the voice pipeline.

        Initializes STT and TTS services, creates the processing pipeline,
        and starts it in the background. The pipeline remains active until
        `stop()` is called.

        Raises:
            ValueError: If required API keys are missing from environment.

        """
        if self._is_live():
            return

        # The flag was true but the pipeline is actually dead. Normalize state
        # so we can cleanly rebuild. (Belt-and-suspenders for d5e6 — the
        # disconnect handler also clears this, but the handler may not have
        # fired yet, and a flag without a live check is the bug shape we were
        # burned by in the first place.)
        self._started = False
        self._pipeline_task = None

        logger.info("Starting Pipecat MCP Agent pipeline...")

        # Override Pipecat's noisy logging
        configure_quiet_logging()

        # Create STT service (TTS handled by VoiceProfileSwitcher)
        stt = self._create_stt_service()

        context = LLMContext()
        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                # c1a2: use silence-based turn detection instead of the default
                # smart-turn ML analyzer, which was cutting users off mid-sentence.
                # 1.0s wait after VAD stop gives ~1.2-1.4s total end-of-turn latency
                # (VAD stop_secs 0.2-0.4s + user_speech_timeout 1.0s).
                user_turn_strategies=UserTurnStrategies(
                    stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=1.0)]
                ),
                vad_analyzer=create_vad_analyzer(),
            ),
        )

        # Create voice profile switcher (same as main server)
        # Use safe import with proper path handling
        try:
            import os
            import sys
            # Get absolute path to talky root (avoid relative path issues)
            current_file = os.path.abspath(__file__)
            mcp_server_src = os.path.dirname(current_file)
            mcp_server = os.path.dirname(mcp_server_src)
            talky_root = os.path.dirname(os.path.dirname(mcp_server))
            
            # Only add if not already in sys.path to avoid duplication
            if talky_root not in sys.path:
                sys.path.insert(0, talky_root)
            
            # Debug the path calculation
            logger.debug(f"Current file: {current_file}")
            logger.debug(f"MCP server src: {mcp_server_src}")
            logger.debug(f"MCP server: {mcp_server}")
            logger.debug(f"Talky root: {talky_root}")
            logger.debug(f"sys.path includes talky_root: {talky_root in sys.path}")
            
            from server.features.voice_switcher import VoiceProfileSwitcher
            from shared.profile_manager import get_profile_manager
        except ImportError as e:
            logger.error(f"Failed to import VoiceProfileSwitcher: {e}")
            logger.error(f"Current working directory: {os.getcwd()}")
            logger.error(f"sys.path: {sys.path}")
            logger.error("Make sure TALKY_ROOT environment variable is set or run from talky directory")
            raise RuntimeError("VoiceProfileSwitcher import failed - check environment setup")
        
        pm = get_profile_manager()
        profile_name = pm.get_default_voice_profile()
        # Use standard VoiceProfileSwitcher (ServiceSwitcher doesn't support dynamic loading)
        self.voice_switcher = VoiceProfileSwitcher(profile_name, pm, task=None)
        tts_switcher = self.voice_switcher.get_service_switcher()

        # Create simplified pipeline
        pipeline = Pipeline(
            [
                self._transport.input(),
                stt,
                user_aggregator,
                tts_switcher,  # Use ServiceSwitcher for dynamic TTS
                self._transport.output(),
                assistant_aggregator,
            ]
        )

        # 9e7d: disable pipecat's default 5-minute idle timeout. Without this
        # override, sessions quietly self-cancel whenever the user steps away
        # for more than 300s, leaving zombie state. server/bot.py already does
        # this; mcp-server was inheriting the default.
        self._pipeline_task = PipelineTask(
            pipeline,
            params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
            idle_timeout_secs=None,
            cancel_on_idle_timeout=False,
        )

        # Set task reference for voice switcher (needed for ManuallySwitchServiceFrame)
        self.voice_switcher.set_task(self._pipeline_task)

        # Add RTVI event handlers for voice switching (CRITICAL: Must be registered BEFORE runner.run)
        @self._pipeline_task.rtvi.event_handler("on_client_ready")
        async def on_client_ready(rtvi):
            logger.info("Client ready event fired")

        @self._pipeline_task.rtvi.event_handler("on_client_message")
        async def handle_client_message(rtvi, msg):
            """Handle RTVI messages from browser voice client."""
            await self.voice_switcher.handle_message(rtvi, msg)

        self._pipeline_runner = PipelineRunner(handle_sigterm=True)

        @self._transport.event_handler("on_client_connected")
        async def on_connected(transport, client):
            logger.info(f"Client connected")

        @self._transport.event_handler("on_client_disconnected")
        async def on_disconnected(transport, client):
            logger.info(f"Client disconnected")
            if not self._pipeline_task:
                return

            if isinstance(self._runner_args, DailyRunnerArguments):
                await self._user_speech_queue.put("I just disconnected, but I might come back.")
            else:
                await self._user_speech_queue.put(self._DISCONNECT_SENTINEL)
                await self._pipeline_task.cancel()
                # d5e6: reset lifecycle flag so the next listen()/speak() will
                # rebuild a fresh pipeline instead of reusing the cancelled one.
                # Without this, convo_listen() blocks forever on a dead queue.
                self._started = False
                self._pipeline_task = None

        @user_aggregator.event_handler("on_user_turn_stopped")
        async def on_user_turn_stopped(aggregator, strategy, message: UserTurnStoppedMessage):
            if message.content:
                await self._user_speech_queue.put({
                    "text": message.content,
                    "timestamp": time.time(),
                })

        # Start pipeline in background
        self._task = asyncio.create_task(self._pipeline_runner.run(self._pipeline_task))

        self._started = True
        logger.info("Pipecat MCP Agent started!")

    async def stop(self):
        """Stop the voice pipeline.

        Sends an `EndFrame` to gracefully shut down the pipeline and waits
        for the background task to complete.
        """
        # Use the flag here rather than _is_live(): even if the pipeline is
        # already dead, we want to run the cleanup path to null out refs and
        # flip the flag.
        if not self._started and self._pipeline_task is None:
            return

        logger.info("Stopping Pipecat MCP agent...")

        if self._pipeline_task:
            await self._pipeline_task.queue_frame(EndFrame())

        if self._task:
            await self._task

        self._started = False
        logger.info("Pipecat MCP Agent stopped")

    async def listen(self) -> dict:
        """Wait for user speech and return all buffered transcriptions.

        Blocks until at least one utterance is available, then drains any
        additional buffered utterances. Returns combined text plus individual
        segments with timestamps for gap/silence awareness.

        Returns:
            Dict with 'text' (combined string) and 'segments' (list of
            dicts with 'text' and 'timestamp' for each utterance).

        Raises:
            RuntimeError: If the pipeline task is not initialized.

        """
        if not self._is_live():
            await self.start()

        if not self._pipeline_task:
            raise RuntimeError("Pipecat MCP Agent not initialized")

        # Block until first utterance
        first = await self._user_speech_queue.get()

        # Check if this is a disconnect signal
        if first is self._DISCONNECT_SENTINEL:
            raise RuntimeError("I just disconnected, but I might come back.")

        segments = [first]

        # Drain any additional buffered utterances (non-blocking)
        while not self._user_speech_queue.empty():
            try:
                item = self._user_speech_queue.get_nowait()
                if item is self._DISCONNECT_SENTINEL:
                    break
                segments.append(item)
            except asyncio.QueueEmpty:
                break

        # Build combined text
        combined = " ".join(seg["text"] for seg in segments)

        return {
            "text": combined,
            "segments": segments,
        }

    async def speak(self, text: str):
        """Speak text to the user using text-to-speech.

        Queues LLM response frames to synthesize and play the given text.
        Starts the pipeline automatically if not already running.

        Args:
            text: The text to speak to the user.

        Raises:
            RuntimeError: If the pipeline task is not initialized.

        """
        if not self._is_live():
            await self.start()

        if not self._pipeline_task:
            raise RuntimeError("Pipecat MCP Agent not initialized")

        await self._pipeline_task.queue_frames(
            [
                LLMFullResponseStartFrame(),
                LLMTextFrame(text=text),
                LLMFullResponseEndFrame(),
            ]
        )

    
    def _create_stt_service(self) -> STTService:
        """Create STT service from default voice profile."""
        from shared.profile_manager import get_profile_manager

        pm = get_profile_manager()
        profile_name = pm.get_default_voice_profile()
        profile = pm.get_voice_profile(profile_name)

        if profile:
            stt = create_stt_service_from_config(profile.stt_provider, model=profile.stt_model)
        else:
            stt = create_stt_service_from_config("whisper_local")

        return stt


async def create_agent(runner_args: RunnerArguments) -> PipecatMCPAgent:
    """Create a PipecatMCPAgent with the appropriate transport.

    Args:
        runner_args: Runner configuration specifying transport type and settings.

    Returns:
        A configured `PipecatMCPAgent` instance ready to be started.

    """
    transport_params = {}

    # Create transport based on runner args type
    if isinstance(runner_args, DailyRunnerArguments):
        from pipecat.transports.daily.transport import DailyParams

        transport_params["daily"] = lambda: DailyParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            video_out_enabled=True,
            audio_in_filter=RNNoiseFilter(),
        )
    elif isinstance(runner_args, SmallWebRTCRunnerArguments):
        transport_params["webrtc"] = lambda: TransportParams(
            **COMMON_TRANSPORT_PARAMS,
            video_out_enabled=True,
        )
    elif isinstance(runner_args, WebSocketRunnerArguments):
        params_callback = lambda: FastAPIWebsocketParams(
            **COMMON_TRANSPORT_PARAMS,
        )
        transport_params["twilio"] = params_callback
        transport_params["telnyx"] = params_callback
        transport_params["plivo"] = params_callback
        transport_params["exotel"] = params_callback

    transport = await create_transport(runner_args, transport_params)
    return PipecatMCPAgent(transport, runner_args)
