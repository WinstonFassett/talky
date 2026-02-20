"""Tests for profile_manager and service_factory."""

# Ensure project root is on path
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# -- Fixtures ---------------------------------------------------------------

MINIMAL_LLM_BACKENDS = {
    "llm_backends": {
        "test-backend": {
            "description": "Test backend",
            "service_class": "backends.test.TestLLMService",
            "config": {"url": "ws://localhost:1234"},
            "system_message": "You are a test.",
        }
    }
}

MINIMAL_VOICE_BACKENDS = {
    "voice_backends": {
        "tts": {
            "kokoro": {
                "description": "Local TTS",
                "requires_credentials": False,
                "service_class": "pipecat.services.kokoro.tts.KokoroTTSService",
                "default_voice": "af_heart",
            }
        },
        "stt": {
            "whisper_local": {
                "description": "Local Whisper",
                "requires_credentials": False,
                "service_class": "pipecat.services.whisper.stt.WhisperSTTServiceMLX",
                "default_model": "mlx-community/whisper-large-v3-turbo",
            }
        },
    }
}

MINIMAL_VOICE_PROFILES = {
    "voice_profiles": {
        "test-voice": {
            "description": "Test voice",
            "tts_provider": "kokoro",
            "tts_voice": "af_heart",
            "tts_config": {},
            "stt_provider": "whisper_local",
            "stt_model": "mlx-community/whisper-large-v3-turbo",
            "stt_config": {},
        }
    }
}

MINIMAL_TALKY_PROFILES = {
    "talky_profiles": {
        "test-profile": {
            "description": "Test profile",
            "llm_backend": "test-backend",
            "voice_profile": "test-voice",
        }
    }
}

MINIMAL_DEFAULTS = {"defaults": {"llm_backend": "test-backend", "voice_profile": "test-voice"}}


def _write_all_yamls(config_dir: Path):
    """Write all 5 config yamls to the given directory."""
    config_dir.mkdir(parents=True, exist_ok=True)
    for filename, data in [
        ("llm-backends.yaml", MINIMAL_LLM_BACKENDS),
        ("voice-backends.yaml", MINIMAL_VOICE_BACKENDS),
        ("voice-profiles.yaml", MINIMAL_VOICE_PROFILES),
        ("talky-profiles.yaml", MINIMAL_TALKY_PROFILES),
        ("settings.yaml", MINIMAL_DEFAULTS),
    ]:
        (config_dir / filename).write_text(yaml.dump(data))


# -- ProfileManager Tests ---------------------------------------------------


def test_profile_manager_loads_all_configs(tmp_path):
    """5 yamls in tmp dir → PM loads them all."""
    from server.config.profile_manager import ProfileManager

    _write_all_yamls(tmp_path)
    pm = ProfileManager(config_dir=tmp_path)

    assert "test-backend" in pm.llm_backends
    assert "test-voice" in pm.voice_profiles
    assert "test-profile" in pm.talky_profiles
    assert pm.defaults["llm_backend"] == "test-backend"
    assert pm.get_voice_backend_config("tts", "kokoro")["service_class"] == (
        "pipecat.services.kokoro.tts.KokoroTTSService"
    )


def test_profile_manager_copies_defaults_on_missing(tmp_path):
    """Empty dir + bundled defaults → PM copies and loads."""
    from server.config.profile_manager import ProfileManager

    # tmp_path is empty, PM should copy from bundled defaults
    pm = ProfileManager(config_dir=tmp_path)

    # Should have copied default yaml files
    for name in [
        "llm-backends.yaml",
        "voice-backends.yaml",
        "voice-profiles.yaml",
        "talky-profiles.yaml",
        "settings.yaml",
    ]:
        assert (tmp_path / name).exists(), f"Missing: {name}"


def test_profile_manager_resolves_talky_profile(tmp_path):
    """Name → LLM backend + voice profile + configs."""
    from server.config.profile_manager import ProfileManager

    _write_all_yamls(tmp_path)
    pm = ProfileManager(config_dir=tmp_path)

    resolved = pm.resolve_talky_profile("test-profile")
    assert resolved["llm_backend"].name == "test-backend"
    assert resolved["voice_profile"].name == "test-voice"
    assert resolved["talky_profile"].llm_backend == "test-backend"


def test_profile_manager_resolve_unknown_raises(tmp_path):
    """Unknown profile → ValueError."""
    from server.config.profile_manager import ProfileManager

    _write_all_yamls(tmp_path)
    pm = ProfileManager(config_dir=tmp_path)

    with pytest.raises(ValueError, match="Unknown talky profile"):
        pm.resolve_talky_profile("nonexistent")


# -- ServiceFactory Tests ---------------------------------------------------


def test_service_factory_splits_dotted_path():
    """Dotted path → correct module + class."""
    from shared.service_factory import _split_dotted_path

    mod, cls = _split_dotted_path("pipecat.services.kokoro.tts.KokoroTTSService")
    assert mod == "pipecat.services.kokoro.tts"
    assert cls == "KokoroTTSService"


def test_service_factory_splits_short_path():
    """Short dotted path → module + class."""
    from shared.service_factory import _split_dotted_path

    mod, cls = _split_dotted_path("mymodule.MyClass")
    assert mod == "mymodule"
    assert cls == "MyClass"


def test_service_factory_raises_on_invalid_path():
    """No dot → ValueError."""
    from shared.service_factory import _split_dotted_path

    with pytest.raises(ValueError, match="Invalid dotted path"):
        _split_dotted_path("NoDotHere")


def test_service_factory_handles_credentials(tmp_path, monkeypatch):
    """requires_credentials=true → loads from credentials dir."""
    import json

    from shared.service_factory import load_credentials

    cred_dir = tmp_path / ".talky" / "credentials"
    cred_dir.mkdir(parents=True)
    (cred_dir / "google.json").write_text(json.dumps({"credentials_path": "/tmp/test.json"}))

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    creds = load_credentials("google")
    assert creds["credentials_path"] == "/tmp/test.json"


def test_service_factory_raises_on_missing_provider(tmp_path):
    """Unknown provider → ValueError."""
    import shared.service_factory as sf
    from server.config.profile_manager import ProfileManager, get_profile_manager
    from shared.service_factory import create_tts_service_from_config

    _write_all_yamls(tmp_path)

    # Reset singleton so it uses our tmp config
    import server.config.profile_manager as pm_mod

    old = pm_mod._instance
    pm_mod._instance = None
    pm = get_profile_manager(config_dir=tmp_path)

    try:
        with pytest.raises(ValueError, match="not found"):
            create_tts_service_from_config("nonexistent_provider")
    finally:
        pm_mod._instance = old
