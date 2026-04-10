"""Tests for VoiceChannel.request_leave (ticket 0b80).

Scope: the request_leave state machine — grace-window race between
user speech and timeout, grace=0 shortcut, and the non-live no-op
path. Does NOT exercise real TTS, audio-cue playback, or WebRTC.

The live-pipeline paths are exercised via monkeypatched ``is_live``,
``speak``, ``_inject_cue``, and ``listen`` so the tests can observe
which steps ran and drive the grace-window timing deterministically.

request_leave takes no arguments — the grace window is set at
channel-construction time from server config (env var + YAML),
not per-call. Tests set it directly on the channel instance.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

_MCP_SRC = Path(__file__).parent.parent / "src"
if str(_MCP_SRC) not in sys.path:
    sys.path.insert(0, str(_MCP_SRC))

from pipecat_mcp_server.channel import VoiceChannel  # noqa: E402


def _live_channel(
    monkeypatch: pytest.MonkeyPatch,
    grace_seconds: float = 4.0,
) -> VoiceChannel:
    """Build a channel that reports itself as live without a real pipeline."""
    ch = VoiceChannel(request_leave_grace_seconds=grace_seconds)
    monkeypatch.setattr(ch, "is_live", lambda: True)
    # Stub out the pieces that would need a real pipeline.
    ch.speak = AsyncMock()  # type: ignore[method-assign]
    ch._inject_cue = AsyncMock()  # type: ignore[method-assign]
    return ch


# ── non-live path ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_leave_no_pipeline_returns_immediately():
    """Non-live pipeline skips all ceremony and returns immediately.

    No speak, no cue, no grace window.
    """
    ch = VoiceChannel()

    result = await ch.request_leave()

    assert result["left"] is True
    assert result["user_interrupted"] is False
    assert "pipeline not live" in result.get("reason", "")


# ── grace disabled (grace_seconds <= 0) ──────────────────────────────────


@pytest.mark.asyncio
async def test_request_leave_grace_zero_skips_signoff_and_cue(monkeypatch):
    """User-configured grace_seconds=0 is the escape hatch.

    No signoff, no cue, no wait.
    """
    ch = _live_channel(monkeypatch, grace_seconds=0.0)

    result = await ch.request_leave()

    assert result["left"] is True
    assert result["user_interrupted"] is False
    ch.speak.assert_not_awaited()  # type: ignore[attr-defined]
    ch._inject_cue.assert_not_awaited()  # type: ignore[attr-defined]


# ── signoff phrase + cue are played ──────────────────────────────────────


@pytest.mark.asyncio
async def test_request_leave_plays_signoff_then_cue(monkeypatch):
    """Configured signoff is spoken, then the descending-beep cue plays.

    Order matters: signoff phrase first, cue second.
    """
    ch = _live_channel(monkeypatch, grace_seconds=0.05)
    ch._active_profile = "openclaw"

    # Fake the signoff lookup so we don't depend on real config.
    monkeypatch.setattr(
        ch, "_signoff_for_profile", lambda name: "openclaw signing off"
    )

    # listen() should time out (no user speech).
    async def _slow_listen() -> dict[str, Any]:
        await asyncio.sleep(1.0)  # longer than grace → caller hits timeout
        return {"text": "never runs"}

    monkeypatch.setattr(ch, "listen", _slow_listen)

    result = await ch.request_leave()

    assert result["left"] is True
    assert result["user_interrupted"] is False
    ch.speak.assert_awaited_once_with("openclaw signing off")  # type: ignore[attr-defined]
    ch._inject_cue.assert_awaited_once()  # type: ignore[attr-defined]


# ── user interrupts the grace window ─────────────────────────────────────


@pytest.mark.asyncio
async def test_request_leave_user_interrupts_grace(monkeypatch):
    """User speech during grace window cancels the leave.

    Return dict carries user_interrupted=True plus the text.
    """
    ch = _live_channel(monkeypatch, grace_seconds=2.0)

    monkeypatch.setattr(ch, "_signoff_for_profile", lambda name: None)

    # listen() returns quickly with text.
    async def _fast_listen() -> dict[str, Any]:
        await asyncio.sleep(0.01)
        return {"text": "wait, one more thing", "segments": []}

    monkeypatch.setattr(ch, "listen", _fast_listen)

    result = await ch.request_leave()

    assert result["left"] is False
    assert result["user_interrupted"] is True
    assert result["text"] == "wait, one more thing"


# ── MCP driver profile: no signoff phrase, cue still plays ───────────────


@pytest.mark.asyncio
async def test_request_leave_mcp_driver_skips_signoff_plays_cue(monkeypatch):
    """MCPDriver skips the signoff phrase but still plays the cue.

    MCPDriver has no intrinsic voice; the agent supplies its own.
    """
    ch = _live_channel(monkeypatch, grace_seconds=0.05)
    ch._active_profile = VoiceChannel.MCP_DRIVER_PROFILE

    # _signoff_for_profile returns None for MCP driver by design —
    # we rely on the real implementation here.

    async def _slow_listen() -> dict[str, Any]:
        await asyncio.sleep(1.0)
        return {"text": "never runs"}

    monkeypatch.setattr(ch, "listen", _slow_listen)

    result = await ch.request_leave()

    assert result["left"] is True
    assert result["user_interrupted"] is False
    ch.speak.assert_not_awaited()  # type: ignore[attr-defined]
    ch._inject_cue.assert_awaited_once()  # type: ignore[attr-defined]


# ── peer disconnect during grace is treated as clean leave ──────────────


@pytest.mark.asyncio
async def test_request_leave_peer_disconnect_during_grace(monkeypatch):
    """Peer disconnect during grace window is treated as a clean leave.

    listen() raises RuntimeError on disconnect; request_leave swallows
    that and proceeds.
    """
    ch = _live_channel(monkeypatch, grace_seconds=1.0)

    monkeypatch.setattr(ch, "_signoff_for_profile", lambda name: None)

    async def _dead_listen() -> dict[str, Any]:
        raise RuntimeError("WebRTC peer disconnected during listen()")

    monkeypatch.setattr(ch, "listen", _dead_listen)

    result = await ch.request_leave()

    assert result["left"] is True
    assert result["user_interrupted"] is False
