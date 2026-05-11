"""Tests for VoiceChannel announcement lookup + greeting-instruction resolution.

After ticket e540 the historical "greeting" field was split:

- ``_announcement_for_profile`` — impersonal channel cue ("OpenClaw channel"),
  spoken by the daemon on *manual picker* switches only.
- ``ProfileManager.resolve_greeting_instruction`` — agent-greeting *instruction*,
  handed to the agent on /ws/agent connect so the agent generates its own
  greeting words. Tested separately under test_profile_manager_greeting.py.

This file covers the announcement helper only — the equivalent of what the
old ``_greeting_for_profile`` used to expose.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Put the mcp-server src dir on the path so `pipecat_mcp_server` imports.
_MCP_SRC = Path(__file__).parent.parent / "src"
if str(_MCP_SRC) not in sys.path:
    sys.path.insert(0, str(_MCP_SRC))

from pipecat_mcp_server.channel import VoiceChannel  # noqa: E402


def _fake_pm(backends: dict, talky_profiles: dict | None = None):
    """Fake profile manager returning predefined backends and talky profiles."""
    talky_profiles = talky_profiles or {}

    class _FakePM:
        def get_llm_backend(self, name):
            return backends.get(name)

        def get_talky_profile(self, name):
            return talky_profiles.get(name)

    return _FakePM()


def test_mcp_driver_profile_is_always_silent():
    """The null/passthrough MCP driver never has an announcement."""
    ch = VoiceChannel()
    assert ch._announcement_for_profile(VoiceChannel.MCP_DRIVER_PROFILE) is None


def test_unknown_profile_returns_none():
    """A profile the profile manager doesn't know about → silent."""
    ch = VoiceChannel()
    with patch(
        "shared.profile_manager.get_profile_manager",
        return_value=_fake_pm({}),
    ):
        assert ch._announcement_for_profile("nonexistent") is None


def test_profile_without_announcement_returns_none():
    """A backend present in the config but with announcement=None → silent."""
    backend = SimpleNamespace(name="q", announcement=None)
    ch = VoiceChannel()
    with patch(
        "shared.profile_manager.get_profile_manager",
        return_value=_fake_pm({"q": backend}),
    ):
        assert ch._announcement_for_profile("q") is None


def test_profile_with_announcement_returns_it():
    """A backend with a configured announcement returns the announcement text."""
    backend = SimpleNamespace(name="openclaw", announcement="OpenClaw channel.")
    ch = VoiceChannel()
    with patch(
        "shared.profile_manager.get_profile_manager",
        return_value=_fake_pm({"openclaw": backend}),
    ):
        assert ch._announcement_for_profile("openclaw") == "OpenClaw channel."


def test_profile_manager_exception_is_swallowed():
    """A broken profile manager must not crash the switch path."""
    ch = VoiceChannel()
    with patch(
        "shared.profile_manager.get_profile_manager",
        side_effect=RuntimeError("kaboom"),
    ):
        assert ch._announcement_for_profile("openclaw") is None
