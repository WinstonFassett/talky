"""Factory for creating STT/TTS services from configuration.

Integrates with our existing talky configuration system.
Uses YAML config files and credential storage instead of .env files.
"""

import os
import sys
from pathlib import Path
from typing import Optional

from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService

# Import the config-driven factory from shared
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "shared"))
from service_factory import create_stt_service_from_config, create_tts_service_from_config


def create_stt_service(
    provider: str = "whisper", api_key: Optional[str] = None, model: Optional[str] = None, **kwargs
) -> STTService:
    """Create STT service from configuration.

    Args:
        provider: STT provider (deepgram, whisper, google)
        api_key: API key (if required)
        model: Model name (provider-specific)
        **kwargs: Additional provider-specific parameters

    Returns:
        Configured STT service

    """
    # Use the config-driven factory - no hardcoded logic
    if model:
        kwargs["model"] = model
    if api_key:
        kwargs["api_key"] = api_key

    return create_stt_service_from_config(provider, **kwargs)


def create_tts_service(
    provider: str = "kokoro",
    api_key: Optional[str] = None,
    voice_id: Optional[str] = None,
    **kwargs,
) -> TTSService:
    """Create TTS service from configuration.

    Args:
        provider: TTS provider (google, cartesia, elevenlabs, kokoro)
        api_key: API key (if required)
        voice_id: Voice ID/name
        **kwargs: Additional provider-specific parameters

    Returns:
        Configured TTS service

    """
    # Use the config-driven factory - no hardcoded logic
    if voice_id:
        kwargs["voice_id"] = voice_id
    if api_key:
        kwargs["api_key"] = api_key

    return create_tts_service_from_config(provider, **kwargs)


def create_services_from_env():
    """Create STT/TTS services from environment variables.

    Uses same env vars as our main talky:
    - STT_PROVIDER (default: whisper)
    - TTS_PROVIDER (default: kokoro)
    - Voice-specific API keys

    Returns:
        Tuple of (stt_service, tts_service)

    """
    stt_provider = os.getenv("STT_PROVIDER", "whisper")
    tts_provider = os.getenv("TTS_PROVIDER", "kokoro")

    stt = create_stt_service(stt_provider)
    tts = create_tts_service(tts_provider)

    return stt, tts
