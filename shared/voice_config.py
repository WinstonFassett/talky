"""Shared voice configuration for bot and agent parity.

Thin helpers that delegate to profile_manager + service_factory.
"""

import sys

from pipecat.audio.vad.silero import SileroVADAnalyzer

from .service_factory import (
    create_stt_service_from_config,
    create_tts_service_from_config,
)


def create_tts_for_profile(voice_profile_name=None, provider=None, voice_id=None):
    """Create TTS service from a voice profile name or explicit provider/voice.

    Used by say_command and tts_daemon to avoid duplicating this logic.
    """
    from server.config.profile_manager import get_profile_manager

    pm = get_profile_manager()

    # Explicit provider takes precedence
    if provider:
        kwargs = {}
        if voice_id:
            kwargs["voice_id"] = voice_id
        return create_tts_service_from_config(provider, **kwargs)

    # Resolve from voice profile
    profile_name = voice_profile_name or pm.get_default_voice_profile()
    profile = pm.get_voice_profile(profile_name)
    if not profile:
        raise ValueError(f"Voice profile not found: {profile_name}")

    return create_tts_service_from_config(
        profile.tts_provider,
        voice_id=profile.tts_voice,
    )


def create_vad_analyzer():
    """Create VAD analyzer with consistent configuration."""
    return SileroVADAnalyzer()


def configure_quiet_logging():
    """Override Pipecat's noisy logging but keep useful info."""
    from loguru import logger

    logger.remove()

    def smart_filter(record):
        if record["level"].name in ("ERROR", "WARNING", "CRITICAL"):
            return True

        if record["level"].name == "INFO":
            module = record.get("name", "")
            message = record.get("message", "")

            noisy_modules = [
                "pipecat.transports.smallwebrtc.connection",
                "pipecat.processors.frame_processor",
                "pipecat.services.google.tts",
                "pipecat.services.deepgram.stt",
                "pipecat.audio.vad.silero",
                "pipecat.audio.turn.smart_turn",
                "pipecat.processors.aggregators",
                "pipecat.processors.metrics",
                "pipecat.pipeline.runner",
                "pipecat.pipeline.task",
                "pipecat.runner.run",
                "pipecat.processors.frameworks.rtvi",
            ]
            if any(m in module for m in noisy_modules):
                return False

            blocked_patterns = [
                "Adding remote candidate",
                "ICE connection state",
                "Track audio received",
                "Track video received",
                "Linking",
                "PipelineTask#",
                "usage characters",
                "TTFB:",
                "processing time:",
                "cleaning up TTS context",
                "Generating TTS",
                "User started speaking",
                "User stopped speaking",
                "Bot started speaking",
                "Bot stopped speaking",
                "Loading Silero VAD model",
                "Loaded Silero VAD",
                "Loading Local Smart Turn",
                "Loaded Local Smart Turn",
                "Setting VAD params to:",
                "analyze_end_of_turn",
                "append_audio",
                "_on_user_turn_started",
                "_on_user_turn_stopped",
                "webrtc_connection_callback executed successfully",
                "Received client-ready",
                "Client Details",
                "Received app message inside",
                "StartFrame#",
                "reached the end of the pipeline",
                "Runner PipelineRunner#",
                "started running PipelineTask#",
                "_wait_for_pipeline_start",
                "_source_push_frame",
                "received interruption task frame",
            ]
            for pattern in blocked_patterns:
                if pattern in message:
                    return False

            return True

        return False

    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
        filter=smart_filter,
    )


# Common transport parameters
COMMON_TRANSPORT_PARAMS = {
    "audio_in_enabled": True,
    "audio_out_enabled": True,
}
