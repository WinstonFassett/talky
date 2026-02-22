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
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.transports.base_transport import BaseTransport

from shared.service_factory import create_stt_service_from_config, create_tts_service_from_config
from pipecat.runner.types import RunnerArguments, SmallWebRTCRunnerArguments
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

# Async lock for voice profile switching to prevent race conditions
voice_switch_lock = asyncio.Lock()

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

    # Create initial TTS service
    tts = create_tts_service_from_config(
        voice_profile.tts_provider, 
        voice_id=voice_profile.tts_voice
    )
    logger.info(f"Created TTS service: {type(tts).__name__}")

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
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )

    # Store current voice profile for switching - make it accessible in closures
    voice_switcher = {
        "current_profile": voice_profile_name,
        "tts_service": tts,
    }

    @task.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        logger.info("Client ready event fired")

    @task.rtvi.event_handler("on_client_message")
    async def on_client_message(rtvi, msg):
        """Handle custom client messages for voice profile control."""
        logger.debug(f"Received client message: {msg.type}")
        
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
                
        elif msg.type == "getCurrentVoiceProfile":
            try:
                current_profile_name = voice_switcher["current_profile"]
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
                logger.debug(f"Current voice profile: {current_profile_name}")
            except Exception as e:
                logger.error(f"Error in getCurrentVoiceProfile: {e}")
                await rtvi.send_error_response(msg, f"Failed to get current voice profile: {e}")
                
        elif msg.type == "setVoiceProfile":
            async with voice_switch_lock:
                try:
                    profile_name = msg.data.get("profileName")
                    
                    # Input validation
                    if not profile_name or not isinstance(profile_name, str):
                        await rtvi.send_error_response(msg, "Invalid profile name: must be a non-empty string")
                        return
                    
                    new_profile = pm.get_voice_profile(profile_name)
                    if not new_profile:
                        await rtvi.send_error_response(msg, f"Voice profile not found: {profile_name}")
                        return

                    current_profile_name = voice_switcher["current_profile"]
                    current_profile = pm.get_voice_profile(current_profile_name)
                    if not current_profile:
                        await rtvi.send_error_response(msg, f"Current voice profile not found: {current_profile_name}")
                        return

                    # Only allow same-provider switching for safety
                    if new_profile.tts_provider != current_profile.tts_provider:
                        await rtvi.send_error_response(
                            msg, 
                            f"Cannot switch from {current_profile.tts_provider} to {new_profile.tts_provider}. "
                            f"Only same-provider voice changes are supported."
                        )
                        return

                    # Try to change voice using set_voice method
                    tts_service = voice_switcher["tts_service"]
                    if hasattr(tts_service, 'set_voice'):
                        try:
                            tts_service.set_voice(new_profile.tts_voice)
                            voice_switcher["current_profile"] = profile_name
                            logger.info(f"Changed voice to: {new_profile.tts_voice}")
                            
                            await rtvi.send_server_response(msg, {
                                "type": "voiceProfileSet",
                                "data": {
                                    "name": new_profile.name,
                                    "description": new_profile.description
                                },
                                "status": "success"
                            })
                        except Exception as e:
                            logger.error(f"Failed to set voice: {e}")
                            await rtvi.send_error_response(msg, f"Failed to change voice: {e}")
                    else:
                        await rtvi.send_error_response(
                            msg, 
                            f"Current TTS service doesn't support voice changes. "
                            f"Service: {type(tts_service).__name__}"
                        )
                        
                except Exception as e:
                    logger.error(f"Error in setVoiceProfile: {e}")
                    await rtvi.send_error_response(msg, f"Failed to set voice profile: {e}")
        else:
            await rtvi.send_error_response(msg, f"Unknown message type: {msg.type}")

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
