"""Tests for ProfileManager.resolve_greeting_instruction (ticket 5d95).

Layered greeting-instruction resolution:
    talky-profile.greeting → backend.greeting → settings.greeting → built-in

The ``__none__`` sentinel at any layer explicitly disables the greeting.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.profile_manager import (
    BUILTIN_GREETING_INSTRUCTION,
    LLMBackend,
    ProfileManager,
    TalkyProfile,
)


def _empty_pm(tmp_path: Path) -> ProfileManager:
    """Construct a ProfileManager pointed at an empty config dir.

    Default config files get copied from bundled defaults; we then strip
    state by hand so each test starts from a clean baseline.
    """
    pm = ProfileManager(config_dir=tmp_path)
    pm.llm_backends = {}
    pm.talky_profiles = {}
    pm.defaults = {}
    return pm


def test_builtin_default_when_nothing_configured(tmp_path):
    pm = _empty_pm(tmp_path)
    assert pm.resolve_greeting_instruction(None) == BUILTIN_GREETING_INSTRUCTION


def test_settings_layer_overrides_builtin(tmp_path):
    pm = _empty_pm(tmp_path)
    pm.defaults = {"greeting": "From settings."}
    assert pm.resolve_greeting_instruction(None) == "From settings."


def test_backend_layer_overrides_settings(tmp_path):
    pm = _empty_pm(tmp_path)
    pm.defaults = {"greeting": "From settings."}
    pm.llm_backends = {
        "agent-ext": LLMBackend(
            name="agent-ext",
            description="",
            service_class="",
            config={},
            greeting="From backend.",
        ),
    }
    pm.talky_profiles = {
        "pi": TalkyProfile(name="pi", description="Pi", llm_backend="agent-ext"),
    }
    assert pm.resolve_greeting_instruction("pi") == "From backend."


def test_talky_profile_layer_overrides_backend(tmp_path):
    pm = _empty_pm(tmp_path)
    pm.defaults = {"greeting": "From settings."}
    pm.llm_backends = {
        "agent-ext": LLMBackend(
            name="agent-ext",
            description="",
            service_class="",
            config={},
            greeting="From backend.",
        ),
    }
    pm.talky_profiles = {
        "pi": TalkyProfile(
            name="pi",
            description="Pi",
            llm_backend="agent-ext",
            greeting="From profile.",
        ),
    }
    assert pm.resolve_greeting_instruction("pi") == "From profile."


def test_sentinel_at_profile_disables_greeting(tmp_path):
    pm = _empty_pm(tmp_path)
    pm.defaults = {"greeting": "From settings."}
    pm.llm_backends = {
        "agent-ext": LLMBackend(
            name="agent-ext",
            description="",
            service_class="",
            config={},
            greeting="From backend.",
        ),
    }
    pm.talky_profiles = {
        "pi": TalkyProfile(
            name="pi",
            description="Pi",
            llm_backend="agent-ext",
            greeting=ProfileManager.GREETING_DISABLED_SENTINEL,
        ),
    }
    assert pm.resolve_greeting_instruction("pi") is None


def test_sentinel_at_backend_disables_greeting(tmp_path):
    pm = _empty_pm(tmp_path)
    pm.defaults = {"greeting": "From settings."}
    pm.llm_backends = {
        "agent-ext": LLMBackend(
            name="agent-ext",
            description="",
            service_class="",
            config={},
            greeting=ProfileManager.GREETING_DISABLED_SENTINEL,
        ),
    }
    pm.talky_profiles = {
        "pi": TalkyProfile(name="pi", description="Pi", llm_backend="agent-ext"),
    }
    assert pm.resolve_greeting_instruction("pi") is None


def test_sentinel_at_settings_disables_greeting(tmp_path):
    pm = _empty_pm(tmp_path)
    pm.defaults = {"greeting": ProfileManager.GREETING_DISABLED_SENTINEL}
    assert pm.resolve_greeting_instruction(None) is None


def test_unknown_profile_falls_through_to_settings(tmp_path):
    pm = _empty_pm(tmp_path)
    pm.defaults = {"greeting": "From settings."}
    assert pm.resolve_greeting_instruction("does-not-exist") == "From settings."


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
