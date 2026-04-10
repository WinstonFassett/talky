"""Tests for VoiceChannel idle TTL behavior (ticket 0c5d).

Scope: the TTL state machine only — occupancy tracking, timer
scheduling/cancellation, and the teardown callback. Does NOT
exercise real pipeline construction, STT, TTS, or WebRTC.

A room is "empty" iff no browser peer is attached (i.e. the pipeline
task is not running). Agent membership is no longer a factor — see
the `Claude Code Was Fucking Stupid` retrospective and the follow-up
rip that tore out the speculative `_joined_agents` set. Occupancy is
driven entirely by `is_live()`.

Transitions into "empty" schedule a TTL timer (if TTL is configured).
Transitions out of "empty" cancel any pending timer. When the timer
fires, the channel calls ``detach()`` to tear down the room cleanly.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# Put the mcp-server src dir on the path so `pipecat_mcp_server` imports.
_MCP_SRC = Path(__file__).parent.parent / "src"
if str(_MCP_SRC) not in sys.path:
    sys.path.insert(0, str(_MCP_SRC))

from pipecat_mcp_server.channel import VoiceChannel  # noqa: E402


@pytest.fixture
def ch_no_ttl() -> VoiceChannel:
    """Channel with TTL disabled (infinity)."""
    return VoiceChannel(idle_ttl_seconds=None)


@pytest.fixture
def ch_short_ttl() -> VoiceChannel:
    """Channel with a very short TTL for fast tests.

    detach() is stubbed so we can assert on calls without touching
    the real pipeline teardown path.
    """
    ch = VoiceChannel(idle_ttl_seconds=0.05)
    ch.detach = AsyncMock()  # type: ignore[method-assign]
    return ch


def _simulate_peer_attached(ch: VoiceChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``is_live()`` return True without building a real pipeline."""
    monkeypatch.setattr(ch, "is_live", lambda: True)


def _simulate_peer_detached(ch: VoiceChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``is_live()`` return False."""
    monkeypatch.setattr(ch, "is_live", lambda: False)


# ── _is_empty() semantics ────────────────────────────────────────────────


def test_brand_new_channel_is_empty(ch_no_ttl: VoiceChannel):
    """A freshly constructed channel has no peer → empty."""
    assert ch_no_ttl._is_empty() is True


def test_channel_with_live_peer_is_not_empty(
    ch_no_ttl: VoiceChannel, monkeypatch: pytest.MonkeyPatch
):
    """A channel with a live pipeline (simulating browser attached) is not empty."""
    _simulate_peer_attached(ch_no_ttl, monkeypatch)
    assert ch_no_ttl._is_empty() is False


# ── TTL disabled (default / infinity) ────────────────────────────────────


@pytest.mark.asyncio
async def test_no_ttl_scheduled_when_disabled(ch_no_ttl: VoiceChannel):
    """With idle_ttl_seconds=None, the channel never schedules a timer."""
    # Even explicitly requesting TTL scheduling in an empty state is a no-op.
    ch_no_ttl._schedule_ttl_if_empty()
    assert ch_no_ttl._ttl_task is None


# ── TTL fires on empty room ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ttl_fires_and_calls_detach(ch_short_ttl: VoiceChannel):
    """TTL fires after the configured interval and calls detach().

    Room starts empty (no peer), so directly scheduling the TTL puts
    us on the fire path.
    """
    ch_short_ttl._schedule_ttl_if_empty()
    assert ch_short_ttl._ttl_task is not None

    await asyncio.sleep(0.15)
    ch_short_ttl.detach.assert_awaited_once()  # type: ignore[attr-defined]


# ── Cancellation on re-occupation ────────────────────────────────────────


@pytest.mark.asyncio
async def test_peer_reattach_before_expiry_cancels_ttl(
    ch_short_ttl: VoiceChannel, monkeypatch: pytest.MonkeyPatch
):
    """A browser peer attaching before TTL fires cancels the timer.

    The real attach path calls ``_cancel_ttl`` early; we exercise that
    directly here rather than constructing a real pipeline.
    """
    ch_short_ttl._schedule_ttl_if_empty()
    assert ch_short_ttl._ttl_task is not None

    # Simulate the browser reconnecting — attach() calls _cancel_ttl.
    ch_short_ttl._cancel_ttl()
    _simulate_peer_attached(ch_short_ttl, monkeypatch)

    # Let any pending timer have a chance to fire.
    await asyncio.sleep(0.15)
    ch_short_ttl.detach.assert_not_called()  # type: ignore[attr-defined]


# ── Repeated scheduling is idempotent ────────────────────────────────────


@pytest.mark.asyncio
async def test_repeated_schedule_on_empty_is_idempotent(ch_short_ttl: VoiceChannel):
    """Scheduling TTL twice in a row does not start two timers."""
    ch_short_ttl._schedule_ttl_if_empty()
    first = ch_short_ttl._ttl_task
    assert first is not None

    ch_short_ttl._schedule_ttl_if_empty()
    second = ch_short_ttl._ttl_task
    assert second is first  # same task, not replaced


@pytest.mark.asyncio
async def test_schedule_when_peer_attached_is_noop(
    ch_short_ttl: VoiceChannel, monkeypatch: pytest.MonkeyPatch
):
    """Scheduling TTL while a peer is attached is a no-op.

    The room is not empty, so no timer should start.
    """
    _simulate_peer_attached(ch_short_ttl, monkeypatch)
    ch_short_ttl._schedule_ttl_if_empty()
    assert ch_short_ttl._ttl_task is None


# ── TTL fired-but-re-occupied check ──────────────────────────────────────


@pytest.mark.asyncio
async def test_ttl_fire_skips_teardown_if_room_refilled(
    ch_short_ttl: VoiceChannel, monkeypatch: pytest.MonkeyPatch
):
    """TTL fire re-checks occupancy and skips teardown if room refilled.

    If a peer attaches during the TTL sleep, the fire handler's
    re-check should skip the detach() call.
    """
    ch_short_ttl._schedule_ttl_if_empty()
    # Race: simulate a peer attaching before the timer fires.
    _simulate_peer_attached(ch_short_ttl, monkeypatch)

    await asyncio.sleep(0.15)
    ch_short_ttl.detach.assert_not_called()  # type: ignore[attr-defined]


# ── Constructor API ──────────────────────────────────────────────────────


def test_default_ttl_is_none():
    """Default construction preserves legacy behavior — infinity."""
    ch = VoiceChannel()
    assert ch._idle_ttl_seconds is None


def test_explicit_ttl_stored():
    """An explicit TTL is stored on the channel unchanged."""
    ch = VoiceChannel(idle_ttl_seconds=30.0)
    assert ch._idle_ttl_seconds == 30.0


def test_default_grace_seconds_is_four():
    """Default grace window is 4 seconds."""
    ch = VoiceChannel()
    assert ch._request_leave_grace_seconds == 4.0


def test_explicit_grace_seconds_stored():
    """An explicit grace window is stored on the channel unchanged."""
    ch = VoiceChannel(request_leave_grace_seconds=7.5)
    assert ch._request_leave_grace_seconds == 7.5
