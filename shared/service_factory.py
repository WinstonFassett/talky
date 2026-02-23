"""Config-driven service factory for STT/TTS providers.

Reads all provider info from ~/.talky/voice-backends.yaml via profile_manager.
No hardcoded provider data — users add providers via YAML only.
"""

import importlib
import json
import os
from pathlib import Path
from typing import Any, Dict


def _split_dotted_path(dotted: str) -> tuple[str, str]:
    """Split 'pipecat.services.kokoro.tts.KokoroTTSService' → (module, class)."""
    parts = dotted.rsplit(".", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid dotted path (need module.ClassName): {dotted}")
    return parts[0], parts[1]


def load_credentials(provider_name: str) -> Dict[str, str]:
    """Load credentials from ~/.talky/credentials/{provider}.json."""
    credentials_file = Path.home() / ".talky" / "credentials" / f"{provider_name}.json"
    if not credentials_file.exists():
        return {}
    with open(credentials_file) as f:
        return json.load(f)


def _import_service_class(service_class_path: str):
    """Import and return a class from a dotted path."""
    module_path, class_name = _split_dotted_path(service_class_path)
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(f"Cannot import module '{module_path}': {e}")
    cls = getattr(module, class_name, None)
    if cls is None:
        raise ImportError(f"Class '{class_name}' not found in module '{module_path}'")
    return cls


def _create_service_from_backend_config(
    backend_config: Dict[str, Any],
    provider: str,
    **kwargs,
):
    """Shared logic for creating STT or TTS from a voice-backends.yaml entry."""
    service_class_path = backend_config.get("service_class")
    if not service_class_path:
        raise ValueError(f"Provider '{provider}' missing 'service_class' in config")

    # Handle credentials
    if backend_config.get("requires_credentials"):
        cred_type = backend_config.get("credential_type", provider)
        creds = load_credentials(cred_type)
        if not creds:
            raise ValueError(
                f"Credentials required for '{provider}'. "
                f"Add them to ~/.talky/credentials/{cred_type}.json"
            )
        
        # Special handling for Google TTS
        if provider == "google" and "credentials_path" in creds:
            # Set GOOGLE_APPLICATION_CREDENTIALS environment variable
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds["credentials_path"]
            # Don't pass the credentials_path to the service constructor
            # Google TTS reads the environment variable
        else:
            kwargs.update(creds)

    cls = _import_service_class(service_class_path)
    
    # Special handling for ElevenLabs HTTP service
    if provider == "elevenlabs" and "ElevenLabsHttpTTSService" in service_class_path:
        import aiohttp
        kwargs["aiohttp_session"] = aiohttp.ClientSession()
    
    return cls(**kwargs)


def create_stt_service_from_config(provider: str, **kwargs):
    """Create STT service from voice-backends.yaml config.

    Args:
        provider: Provider name (e.g. "deepgram", "whisper_local")
        **kwargs: Extra args passed to the service constructor
    """
    from server.config.profile_manager import get_profile_manager

    pm = get_profile_manager()
    config = pm.get_voice_backend_config("stt", provider)
    if not config:
        raise ValueError(f"STT provider '{provider}' not found in voice-backends.yaml")

    if "default_model" in config and "model" not in kwargs:
        kwargs["model"] = config["default_model"]

    return _create_service_from_backend_config(config, provider, **kwargs)


def create_tts_service_from_config(provider: str, **kwargs):
    """Create TTS service from voice-backends.yaml config.

    Args:
        provider: Provider name (e.g. "google", "kokoro")
        **kwargs: Extra args passed to the service constructor
    """
    from server.config.profile_manager import get_profile_manager

    pm = get_profile_manager()
    config = pm.get_voice_backend_config("tts", provider)
    if not config:
        raise ValueError(f"TTS provider '{provider}' not found in voice-backends.yaml")

    if "default_voice" in config and "voice_id" not in kwargs:
        kwargs["voice_id"] = config["default_voice"]

    return _create_service_from_backend_config(config, provider, **kwargs)
