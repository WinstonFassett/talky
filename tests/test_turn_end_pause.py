"""Voice profile ``turn_end_pause_secs`` feeds the pipeline turn detector."""

from __future__ import annotations

import yaml

from talky.shared.profile_manager import DEFAULT_TURN_END_PAUSE_SECS, ProfileManager


def _pm(tmp_path, profiles: dict) -> ProfileManager:
    pm = ProfileManager(config_dir=tmp_path)
    (tmp_path / "voice-profiles.yaml").write_text(yaml.safe_dump({"voice_profiles": profiles}))
    pm.voice_profiles = {}
    pm._load_voice_profiles()
    return pm


def test_default_is_one_second(tmp_path):
    pm = _pm(tmp_path, {"default": {"tts_provider": "kokoro"}, "a": {}})
    assert DEFAULT_TURN_END_PAUSE_SECS == 1.0
    assert pm.get_voice_profile("a").turn_end_pause_secs == 1.0


def test_profile_override(tmp_path):
    pm = _pm(tmp_path, {"default": {}, "slow": {"turn_end_pause_secs": 2.5}})
    assert pm.get_voice_profile("slow").turn_end_pause_secs == 2.5
    assert pm.get_voice_profile("default").turn_end_pause_secs == 1.0


def test_inherits_from_default_profile(tmp_path):
    pm = _pm(tmp_path, {"default": {"turn_end_pause_secs": 1.8}, "b": {}})
    assert pm.get_voice_profile("b").turn_end_pause_secs == 1.8
