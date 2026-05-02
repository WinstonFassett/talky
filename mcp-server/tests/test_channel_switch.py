"""Tests for VoiceChannel profile switching — the soft-switch invariant.

The architecture rests on the claim that switching profiles does not require
disconnecting the browser. There are two paths:

1. Soft path: no live pipeline → store desired profile, emit profileChanged,
   apply on next pipeline build.
2. Live path: pipeline is running → queue a ManuallySwitchServiceFrame at
   the LLMSwitcher slot.

This file pins the soft path end-to-end and pins the live path's frame-queuing
behavior with an AsyncMock pipeline. It does NOT exercise real pipecat
construction, STT, or TTS.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_MCP_SRC = Path(__file__).parent.parent / "src"
if str(_MCP_SRC) not in sys.path:
    sys.path.insert(0, str(_MCP_SRC))

from pipecat_mcp_server.channel import VoiceChannel  # noqa: E402


@pytest.fixture
def ch() -> VoiceChannel:
    return VoiceChannel(idle_ttl_seconds=None)


@pytest.fixture
def known_profiles(monkeypatch) -> list[str]:
    """Stub available_profiles to a known set, decoupling tests from YAML."""
    profiles = [VoiceChannel.MCP_DRIVER_PROFILE, "openclaw", "moltis"]
    monkeypatch.setattr(VoiceChannel, "available_profiles", lambda self: profiles)
    return profiles


@pytest.mark.asyncio
async def test_soft_switch_stores_profile_when_pipeline_not_live(ch, known_profiles, monkeypatch):
    """No live pipeline → desired profile is stored without raising."""
    emitted: list[str] = []

    async def fake_emit(_self, name):
        emitted.append(name)

    monkeypatch.setattr(VoiceChannel, "_emit_profile_changed", fake_emit)

    assert ch.is_live() is False
    await ch.switch_to_profile("openclaw")

    assert ch._active_profile == "openclaw"
    assert emitted == ["openclaw"]


@pytest.mark.asyncio
async def test_soft_switch_rejects_unknown_profile(ch, known_profiles):
    with pytest.raises(ValueError, match="unknown profile"):
        await ch.switch_to_profile("nope-not-real")


@pytest.mark.asyncio
async def test_live_switch_queues_manually_switch_service_frame(ch, known_profiles, monkeypatch):
    """When a pipeline is live, switch_to_profile queues a frame on it."""
    fake_service = object()
    ch._llm_services = {"openclaw": fake_service}

    fake_task = AsyncMock()
    fake_task.queue_frames = AsyncMock()
    ch._pipeline_task = fake_task

    monkeypatch.setattr(VoiceChannel, "is_live", lambda self: True)

    async def fake_emit(_self, _name):
        pass

    monkeypatch.setattr(VoiceChannel, "_emit_profile_changed", fake_emit)
    monkeypatch.setattr(VoiceChannel, "_greeting_for_profile", lambda self, name: None)

    await ch.switch_to_profile("openclaw")

    assert fake_task.queue_frames.await_count == 1
    queued = fake_task.queue_frames.await_args.args[0]
    assert len(queued) == 1
    from pipecat.frames.frames import ManuallySwitchServiceFrame

    assert isinstance(queued[0], ManuallySwitchServiceFrame)
    assert queued[0].service is fake_service
    assert ch._active_profile == "openclaw"


def test_available_profiles_includes_mcp_driver(ch):
    """The null MCPDriver profile is always present, even when YAML loading fails."""
    profiles = ch.available_profiles()
    assert VoiceChannel.MCP_DRIVER_PROFILE in profiles
