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


def create_services_from_profile():
    """Create STT/TTS services from voice profile configuration.
    
    Uses the talky voice profile system instead of environment variables.
    Requires proper configuration setup - no fallbacks.
    
    Returns:
        Tuple of (stt_service, tts_service)
        
    Raises:
        ValueError: If configuration is missing or invalid
    """
    # Import profile manager to access voice profiles
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))
    from config.profile_manager import get_profile_manager
    
    pm = get_profile_manager()
    
    # Get default voice profile from config - fail if not set
    default_profile_name = pm.get_default_voice_profile()
    if not default_profile_name:
        raise ValueError(
            "No default voice profile configured. Run 'talky config' to set up your configuration."
        )
    
    profile = pm.get_voice_profile(default_profile_name)
    if not profile:
        raise ValueError(
            f"Default voice profile '{default_profile_name}' not found. "
            f"Run 'talky config' to set up your configuration."
        )
    
    # Use the voice profile's configured providers
    stt_provider = profile.stt_provider
    tts_provider = profile.tts_provider
    
    if not stt_provider:
        raise ValueError(
            f"Voice profile '{default_profile_name}' has no STT provider configured. "
            f"Run 'talky config' to fix your configuration."
        )
    
    if not tts_provider:
        raise ValueError(
            f"Voice profile '{default_profile_name}' has no TTS provider configured. "
            f"Run 'talky config' to fix your configuration."
        )

    try:
        stt = create_stt_service(stt_provider)
        tts = create_tts_service(tts_provider)
    except ValueError as e:
        if "Credentials required" in str(e):
            raise ValueError(
                f"Credentials required for {stt_provider if 'STT' in str(e) else tts_provider}. "
                f"Set up credentials in ~/.talky/credentials/ or run 'talky config'."
            )
        raise
    except Exception as e:
        raise ValueError(
            f"Failed to create services for profile '{default_profile_name}': {e}. "
            f"Check your configuration and credentials."
        )

    return stt, tts
