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

# Add parent directory to path for shared modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib

from loguru import logger
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import Frame, LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.filters.function_filter import FunctionFilter
from pipecat.transports.base_transport import BaseTransport

from shared.service_factory import create_stt_service_from_config, create_tts_service_from_config
from pipecat.runner.types import RunnerArguments, SmallWebRTCRunnerArguments
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIProcessor

# Global reference to the voice switcher
voice_switcher = None

async def run_bot(
    transport: BaseTransport,
    llm_backend_name: str = None,
    voice_profile_name: str = None,
):
    """Main bot logic using clean backend/voice separation."""

    from server.config.profile_manager import get_profile_manager

    pm = get_profile_manager()

    llm_backend_name = (
        llm_backend_name or os.environ.get("LLM_BACKEND") or pm.get_default_llm_backend()
    )
    voice_profile_name = (
        voice_profile_name or os.environ.get("VOICE_PROFILE") or pm.get_default_voice_profile()
    )

    llm_backend = pm.get_llm_backend(llm_backend_name)
    if not llm_backend:
        raise ValueError(f"Unknown LLM backend: {llm_backend_name}")

    voice_profile = pm.get_voice_profile(voice_profile_name)
    if not voice_profile:
        raise ValueError(f"Unknown voice profile: {voice_profile_name}")

    logger.info(f"Using LLM backend: {llm_backend.name}")
    logger.info(f"Using voice profile: {voice_profile.name}")

    # Create STT service
    stt = create_stt_service_from_config(voice_profile.stt_provider, model=voice_profile.stt_model)
    logger.info(f"Created STT service: {type(stt).__name__}")

    # Create TTSSwitcher instead of single TTS service
    from server.lazy_voice_switcher import TTSSwitcher
    from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyManual
    
    # Create TTS services for your personal profiles
    tts_switcher = TTSSwitcher.from_profile_names(
        ["cloud-dude", "google-puck", "local-kokoro", "local-us-female"], 
        ServiceSwitcherStrategyManual
    )
    logger.info(f"Created TTSSwitcher with {len(tts_switcher.tts_services)} TTS services")
    
    # Set global reference for RTVI handlers
    global voice_switcher
    voice_switcher = tts_switcher

    # Import LLM service from backend
    module_path = ".".join(llm_backend.service_class.split(".")[:-1])
    class_name = llm_backend.service_class.split(".")[-1]
    
    # Fix module path to include server prefix if needed
    if not module_path.startswith("server.") and not module_path.startswith("."):
        module_path = f"server.{module_path}"

    llm_service_module = importlib.import_module(module_path)
    llm_service_class = getattr(llm_service_module, class_name)
    llm = llm_service_class(**llm_backend.config)

    # Add directory context to system message
    current_dir = os.getcwd()
    system_message_with_context = (
        f"{llm_backend.system_message}\n\nContext: You are running in the directory: {current_dir}"
    )

    messages = [{"role": "system", "content": system_message_with_context}]

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
            tts_switcher,  # Use LazyVoiceSwitcher
            transport.output(),
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )

    @task.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        print("🔥 CLIENT READY EVENT FIRED")
        logger.info("🔥 Client ready event fired")

    @task.rtvi.event_handler("on_client_message")
    async def on_client_message(rtvi, msg):
        """Handle custom client messages for voice profile control."""
        print(f"🔥 GOT CLIENT MESSAGE: {msg.type}, {msg.data}")
        logger.info(f"🔥 Received client message: {msg.type}, {msg.data}")
        
        if msg.type == "getVoiceProfiles":
            try:
                profiles = pm.list_voice_profiles()
                await rtvi.send_server_response(msg, {
                    "type": "voiceProfiles",
                    "data": [
                        {"name": name, "description": desc}
                        for name, desc in profiles.items()
                    ],
                    "status": "success"
                })
                logger.info(f"Sent {len(profiles)} voice profiles to client")
            except Exception as e:
                logger.error(f"Error in getVoiceProfiles: {e}")
                await rtvi.send_error_response(msg, f"Failed to get voice profiles: {e}")
                
        elif msg.type == "setVoiceProfile":
            try:
                profile_name = msg.data.get("profileName")
                profile = pm.get_voice_profile(profile_name)
                if not profile:
                    await rtvi.send_error_response(msg, f"Voice profile not found: {profile_name}")
                    return

                # Use the voice switcher to change voice profile
                if voice_switcher:
                    # Use the new method to find the service for this profile
                    target_service = voice_switcher.get_service_for_profile(profile_name)
                    
                    if target_service:
                        from pipecat.frames.frames import ManuallySwitchServiceFrame
                        switch_frame = ManuallySwitchServiceFrame(service=target_service)
                        await task.queue_frames([switch_frame])
                        
                        await rtvi.send_server_response(msg, {
                            "type": "voiceProfileSet",
                            "data": {
                                "name": profile.name,
                                "description": profile.description
                            },
                            "status": "success"
                        })
                        logger.info(f"Switched to voice profile: {profile_name}")
                    else:
                        await rtvi.send_error_response(msg, f"TTS service not found for profile: {profile_name}")
                else:
                    await rtvi.send_error_response(msg, "Voice switcher not available")
            except Exception as e:
                logger.error(f"Error in setVoiceProfile: {e}")
                await rtvi.send_error_response(msg, f"Failed to set voice profile: {e}")
                
        elif msg.type == "getCurrentVoiceProfile":
            try:
                import os
                current_profile_name = os.environ.get("VOICE_PROFILE")
                
                if not current_profile_name:
                    await rtvi.send_error_response(msg, "No voice profile set")
                    return
                
                profile = pm.get_voice_profile(current_profile_name)
                if not profile:
                    await rtvi.send_error_response(msg, f"Voice profile not found: {current_profile_name}")
                    return

                await rtvi.send_server_response(msg, {
                    "type": "currentVoiceProfile",
                    "data": {
                        "name": profile.name,
                        "description": profile.description
                    },
                    "status": "success"
                })
                logger.info(f"Current voice profile: {current_profile_name}")
            except Exception as e:
                logger.error(f"Error in getCurrentVoiceProfile: {e}")
                await rtvi.send_error_response(msg, f"Failed to get current voice profile: {e}")
        else:
            await rtvi.send_error_response(msg, f"Unknown message type: {msg.type}")

    @task.rtvi.event_handler("on_transport_message")
    async def on_transport_message(rtvi, message):
        """Debug: Catch all transport messages."""
        print(f"🔥 GOT TRANSPORT MESSAGE: {message}")
        logger.info(f"🔥 Received transport message: {message}")

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")
        context.messages.append(
            {
                "role": "user",
                "content": "Hello! Please greet me and let me know you're ready to help.",
            }
        )
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Main bot entry point."""
    llm_backend_name = os.getenv("LLM_BACKEND")
    voice_profile_name = os.getenv("VOICE_PROFILE")

    if not llm_backend_name or not voice_profile_name:
        raise ValueError("Both LLM_BACKEND and VOICE_PROFILE environment variables must be set")

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

    await run_bot(transport, llm_backend_name, voice_profile_name)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
