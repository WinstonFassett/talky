"""Tests for VoiceChannel.request_leave (ticket 0b80).

Scope: the request_leave state machine — grace-window race between
speech onset and timeout, grace=0 shortcut, and the non-live no-op
path. Does NOT exercise real TTS, audio-cue playback, or WebRTC.

The live-pipeline paths are exercised via monkeypatched ``is_live``,
``speak``, ``_inject_cue``, and ``listen`` so the tests can observe
which steps ran and drive the grace-window timing deterministically.

request_leave takes no arguments — the grace window is set at
channel-construction time from server config (env var + YAML),
not per-call. Tests set it directly on the channel instance.

The grace window detects speech ONSET (``_user_speaking`` event from
VAD) rather than waiting for a full transcribed utterance. If speech
onset is detected, request_leave then waits for the full utterance
via listen() with no timeout.
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


# ── signoff phrase + cue are played, silence during grace ────────────────


@pytest.mark.asyncio
async def test_request_leave_plays_signoff_then_cue(monkeypatch):
    """Configured signoff is spoken, then the descending-beep cue plays.

    No speech onset during grace → left=True.
    """
    ch = _live_channel(monkeypatch, grace_seconds=0.1)
    ch._active_profile = "openclaw"

    monkeypatch.setattr(
        ch, "_signoff_for_profile", lambda name: "openclaw signing off"
    )

    result = await ch.request_leave()

    assert result["left"] is True
    assert result["user_interrupted"] is False
    ch.speak.assert_awaited_once_with("openclaw signing off")  # type: ignore[attr-defined]
    ch._inject_cue.assert_awaited_once()  # type: ignore[attr-defined]


# ── user interrupts the grace window via speech onset ────────────────────


@pytest.mark.asyncio
async def test_request_leave_user_interrupts_grace(monkeypatch):
    """Speech onset during grace window cancels the leave.

    _user_speaking fires during the grace window. request_leave then
    waits for the full utterance via listen() and returns
    user_interrupted=True.
    """
    ch = _live_channel(monkeypatch, grace_seconds=2.0)

    monkeypatch.setattr(ch, "_signoff_for_profile", lambda name: None)

    # listen() returns the full utterance after onset is detected.
    async def _fast_listen() -> dict[str, Any]:
        return {"text": "wait, one more thing", "segments": []}

    monkeypatch.setattr(ch, "listen", _fast_listen)

    # Simulate speech onset shortly after grace window opens.
    async def _fire_onset():
        await asyncio.sleep(0.05)
        ch._user_speaking.set()

    asyncio.create_task(_fire_onset())

    result = await ch.request_leave()

    assert result["left"] is False
    assert result["user_interrupted"] is True
    assert result["text"] == "wait, one more thing"


# ── the exact bug: speech onset near end of grace, slow transcription ────


@pytest.mark.asyncio
async def test_request_leave_late_onset_slow_transcription(monkeypatch):
    """Reproduces the 2026-04-09 bug: user starts speaking at second 3
    of a 4-second grace window, but the full utterance takes 2 more
    seconds to transcribe. The old implementation would have timed out
    and left while the user was mid-sentence. The new one detects onset
    and waits.
    """
    ch = _live_channel(monkeypatch, grace_seconds=0.5)

    monkeypatch.setattr(ch, "_signoff_for_profile", lambda name: None)

    # listen() takes a while to return (simulating slow transcription).
    async def _slow_listen() -> dict[str, Any]:
        await asyncio.sleep(0.5)  # longer than grace window
        return {"text": "no you cannot leave denied", "segments": []}

    monkeypatch.setattr(ch, "listen", _slow_listen)

    # Speech onset fires near the end of the grace window.
    async def _late_onset():
        await asyncio.sleep(0.4)  # just before grace expires
        ch._user_speaking.set()

    asyncio.create_task(_late_onset())

    result = await ch.request_leave()

    # Must NOT have left — speech was detected before grace expired.
    assert result["left"] is False
    assert result["user_interrupted"] is True
    assert result["text"] == "no you cannot leave denied"


# ── MCP driver profile: no signoff phrase, cue still plays ───────────────


@pytest.mark.asyncio
async def test_request_leave_mcp_driver_skips_signoff_plays_cue(monkeypatch):
    """MCPDriver skips the signoff phrase but still plays the cue.

    MCPDriver has no intrinsic voice; the agent supplies its own.
    """
    ch = _live_channel(monkeypatch, grace_seconds=0.1)
    ch._active_profile = VoiceChannel.MCP_DRIVER_PROFILE

    result = await ch.request_leave()

    assert result["left"] is True
    assert result["user_interrupted"] is False
    ch.speak.assert_not_awaited()  # type: ignore[attr-defined]
    ch._inject_cue.assert_awaited_once()  # type: ignore[attr-defined]


# ── peer disconnect during grace is treated as clean leave ──────────────


@pytest.mark.asyncio
async def test_request_leave_peer_disconnect_during_grace(monkeypatch):
    """Peer disconnect during grace window is treated as a clean leave.

    _disconnected event fires during the grace window; request_leave
    detects it and returns left=True without waiting for speech.
    """
    ch = _live_channel(monkeypatch, grace_seconds=2.0)

    monkeypatch.setattr(ch, "_signoff_for_profile", lambda name: None)

    # Simulate disconnect during grace.
    async def _fire_disconnect():
        await asyncio.sleep(0.05)
        ch._disconnected.set()

    asyncio.create_task(_fire_disconnect())

    result = await ch.request_leave()

    assert result["left"] is True
    assert result["user_interrupted"] is False
