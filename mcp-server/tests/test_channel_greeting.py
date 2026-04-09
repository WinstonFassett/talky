"""Tests for VoiceChannel greeting-on-switch behavior (ticket 8c9d).

Scope: the greeting-lookup helper and its interaction with the MCP
driver profile. Does NOT exercise the full switch_to_profile path —
that requires a live pipeline task, which is out of scope for unit
tests (see test_channel_ttl.py for the same discipline).
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


def _fake_pm(backends: dict):
    """Fake profile manager returning predefined backends."""
    class _FakePM:
        def get_llm_backend(self, name):
            return backends.get(name)
    return _FakePM()


def test_mcp_driver_profile_is_always_silent():
    """The null/passthrough MCP driver never greets — agents handle that."""
    ch = VoiceChannel()
    assert ch._greeting_for_profile(VoiceChannel.MCP_DRIVER_PROFILE) is None


def test_unknown_profile_returns_none():
    """A profile the profile manager doesn't know about → silent."""
    ch = VoiceChannel()
    with patch(
        "shared.profile_manager.get_profile_manager",
        return_value=_fake_pm({}),
    ):
        assert ch._greeting_for_profile("nonexistent") is None


def test_profile_without_greeting_returns_none():
    """A backend present in the config but with greeting=None → silent."""
    backend = SimpleNamespace(name="q", greeting=None)
    ch = VoiceChannel()
    with patch(
        "shared.profile_manager.get_profile_manager",
        return_value=_fake_pm({"q": backend}),
    ):
        assert ch._greeting_for_profile("q") is None


def test_profile_with_greeting_returns_it():
    """A backend with a configured greeting returns the greeting text."""
    backend = SimpleNamespace(name="openclaw", greeting="Hey, OpenClaw here.")
    ch = VoiceChannel()
    with patch(
        "shared.profile_manager.get_profile_manager",
        return_value=_fake_pm({"openclaw": backend}),
    ):
        assert ch._greeting_for_profile("openclaw") == "Hey, OpenClaw here."


def test_profile_manager_exception_is_swallowed():
    """A broken profile manager must not crash the switch path."""
    ch = VoiceChannel()
    with patch(
        "shared.profile_manager.get_profile_manager",
        side_effect=RuntimeError("kaboom"),
    ):
        assert ch._greeting_for_profile("openclaw") is None
