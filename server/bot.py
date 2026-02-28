#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""talky — Pipecat Voice Agent with Modular LLM Support

Pipeline: Speech-to-Text -> LLM -> Text-to-Speech
"""

import os
import sys
import asyncio

# Add parent directory to path for shared modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame, ManuallySwitchServiceFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.service_switcher import ServiceSwitcher, ServiceSwitcherStrategyManual
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.transports.base_transport import BaseTransport
from shared.service_factory import create_stt_service_from_config, create_tts_service_from_config
from server.features.voice_switcher import VoiceProfileSwitcher
from pipecat.runner.types import RunnerArguments, SmallWebRTCRunnerArguments
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

def run_bot_main(transport, llm_profile_name: str = None, voice_profile_name: str = None, session_key: str = None):
    """Run bot with given transport and profiles - for programmatic use"""
    # Import and run the actual bot
    # Call bot with the transport
    import asyncio

    import bot

    return asyncio.run(bot.run_bot(transport, llm_profile_name, voice_profile_name, session_key))

async def run_bot(
    transport: BaseTransport,
    llm_backend_name: str = None,
    voice_profile_name: str = None,
    session_key: str = None,
):
    """Main bot logic using clean backend/voice separation."""

    from server.config.profile_manager import get_profile_manager
    import os

    # Use CONFIG_DIR from environment if set
    config_dir = os.environ.get("CONFIG_DIR")
    pm = get_profile_manager(config_dir=config_dir)

    # Use provided parameters or config defaults (no env vars)
    llm_backend_name = llm_backend_name or pm.get_default_llm_backend()
    voice_profile_name = voice_profile_name or pm.get_default_voice_profile()

    # Get session override from CLI parameter first
    session_override = session_key or os.environ.get("SESSION_KEY")

    llm_backend = pm.get_llm_backend(llm_backend_name)
    if not llm_backend:
        raise ValueError(f"Unknown LLM backend: {llm_backend_name}")

    voice_profile = pm.get_voice_profile(voice_profile_name)
    if not voice_profile:
        raise ValueError(f"Unknown voice profile: {voice_profile_name}")

    logger.info(f"Using LLM backend: {llm_backend.name}")
    logger.info(f"Using voice profile: {voice_profile.name}")
    if session_override:
        logger.info(f"Using session override: {session_override}")

    # Create STT service
    stt = create_stt_service_from_config(voice_profile.stt_provider, model=voice_profile.stt_model)
    logger.info(f"Created STT service: {type(stt).__name__}")

    # Create voice profile switcher (handles TTS service creation internally)
    voice_switcher = VoiceProfileSwitcher(voice_profile_name, pm, None)  # task set later
    tts_switcher = voice_switcher.get_service_switcher()
    logger.info(f"Created voice profile switcher")

    # Import LLM service from backend
    module_path = ".".join(llm_backend.service_class.split(".")[:-1])
    class_name = llm_backend.service_class.split(".")[-1]
    
    # Fix module path to include server prefix if needed
    if not module_path.startswith("server.") and not module_path.startswith("."):
        module_path = f"server.{module_path}"

    llm_service_module = importlib.import_module(module_path)
    llm_service_class = getattr(llm_service_module, class_name)
    
    # Prepare backend config with session override (CLI > talky profile > backend config)
    backend_config = llm_backend.config.copy()
    
    # Apply session override if provided
    if session_override:
        backend_config["session_key"] = session_override
        logger.info(f"Applied CLI session override: {session_override}")
    
    llm = llm_service_class(**backend_config)

    messages = []

    context = LLMContext(messages)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts_switcher,  # Use ServiceSwitcher instead of direct TTS
            transport.output(),
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )

    # Set task reference in voice switcher (needed for ManuallySwitchServiceFrame)
    voice_switcher.set_task(task)

    @task.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        logger.info("Client ready event fired")
        # Add greeting message for all backends (including OpenClaw)
        context.messages.append(
            {
                "role": "user",
                "content": "Hello! I just joined via Talky voice chat. Continue any existing conversation, otherwise greet me and let me know you're ready to help.",
            }
        )
        await task.queue_frames([LLMRunFrame()])

    @task.rtvi.event_handler("on_client_message")
    async def on_client_message(rtvi, msg):
        """Handle custom client messages - delegate to voice switcher for voice profile control."""
        if msg.type in ["getVoiceProfiles", "getCurrentVoiceProfile", "setVoiceProfile"]:
            await voice_switcher.handle_message(rtvi, msg)
        else:
            await rtvi.send_error_response(msg, f"Unknown message type: {msg.type}")

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Main bot entry point."""
    from config.profile_manager import get_profile_manager
    
    pm = get_profile_manager()
    
    # Read config from environment variables set by main.py
    llm_backend_name = os.environ.get("LLM_BACKEND") or pm.get_default_llm_backend()
    voice_profile_name = os.environ.get("VOICE_PROFILE") or pm.get_default_voice_profile()
    session_key = os.environ.get("SESSION_KEY")  # Get session from env var
    
    logger.info(f"Using LLM backend: {llm_backend_name}")
    logger.info(f"Using voice profile: {voice_profile_name}")
    if session_key:
        logger.info(f"Using session from environment: {session_key}")

    transport = None

    match runner_args:
        case SmallWebRTCRunnerArguments():
            webrtc_connection: SmallWebRTCConnection = runner_args.webrtc_connection
            transport = SmallWebRTCTransport(
                webrtc_connection=webrtc_connection,
                params=TransportParams(
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                ),
            )
        case _:
            logger.error(f"Unsupported runner arguments type: {type(runner_args)}")
            return

    await run_bot(transport, llm_backend_name, voice_profile_name, session_key)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
