"""Tests for VoiceChannel idle TTL behavior (ticket 0c5d).

Scope: the TTL state machine only — occupancy tracking, timer
scheduling/cancellation, and the teardown callback. Does NOT
exercise real pipeline construction, STT, TTS, or WebRTC.

A room is "empty" when BOTH of these are true:
  1. No browser peer is connected (pipeline not live).
  2. No agent is joined via join_convo.

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


# ── _is_empty() semantics ────────────────────────────────────────────────


def test_brand_new_channel_is_empty(ch_no_ttl: VoiceChannel):
    """A freshly constructed channel has no peer and no agents → empty."""
    assert ch_no_ttl._is_empty() is True


def test_channel_with_joined_agent_is_not_empty(ch_no_ttl: VoiceChannel):
    """A channel with an agent joined is not empty, even with no peer."""
    ch_no_ttl.join_convo("alice")
    assert ch_no_ttl._is_empty() is False


# ── TTL disabled (default / infinity) ────────────────────────────────────


@pytest.mark.asyncio
async def test_no_ttl_scheduled_when_disabled(ch_no_ttl: VoiceChannel):
    """With idle_ttl_seconds=None, the channel never schedules a timer."""
    ch_no_ttl.join_convo("alice")
    ch_no_ttl.leave_convo("alice")
    assert ch_no_ttl._ttl_task is None


# ── TTL fires on empty room ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ttl_fires_and_calls_detach(ch_short_ttl: VoiceChannel):
    """TTL fires after the configured interval and calls detach().

    Channel enters the empty state via join then leave of a single agent.
    """
    ch_short_ttl.join_convo("alice")
    ch_short_ttl.leave_convo("alice")
    assert ch_short_ttl._ttl_task is not None

    await asyncio.sleep(0.15)
    ch_short_ttl.detach.assert_awaited_once()  # type: ignore[attr-defined]


# ── Cancellation on re-occupation ────────────────────────────────────────


@pytest.mark.asyncio
async def test_rejoin_before_expiry_cancels_ttl(ch_short_ttl: VoiceChannel):
    """Rejoining the same agent before TTL fires cancels the timer."""
    ch_short_ttl.join_convo("alice")
    ch_short_ttl.leave_convo("alice")
    assert ch_short_ttl._ttl_task is not None

    ch_short_ttl.join_convo("bob")
    # Let any pending timer have a chance to fire.
    await asyncio.sleep(0.15)
    ch_short_ttl.detach.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_different_agent_rejoin_still_cancels_ttl(ch_short_ttl: VoiceChannel):
    """The specific agent identity doesn't matter — any join resets occupancy."""
    ch_short_ttl.join_convo("alice")
    ch_short_ttl.leave_convo("alice")
    ch_short_ttl.join_convo("carol")
    await asyncio.sleep(0.15)
    ch_short_ttl.detach.assert_not_called()  # type: ignore[attr-defined]


# ── Idempotent transitions ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_agents_leave_in_order_only_one_timer(ch_short_ttl: VoiceChannel):
    """With two agents, TTL should only start when the LAST one leaves."""
    ch_short_ttl.join_convo("alice")
    ch_short_ttl.join_convo("bob")

    ch_short_ttl.leave_convo("alice")
    # Still has bob → no TTL scheduled.
    assert ch_short_ttl._ttl_task is None

    ch_short_ttl.leave_convo("bob")
    assert ch_short_ttl._ttl_task is not None


@pytest.mark.asyncio
async def test_leave_noop_does_not_schedule(ch_short_ttl: VoiceChannel):
    """A no-op leave on an already-empty room does not schedule TTL.

    TTL is only scheduled on *transitions* into the empty state, not
    on leaves that don't actually change occupancy.
    """
    # Room starts empty. leave_convo on unknown agent is a no-op.
    # In particular, it should NOT schedule a TTL on a room that was
    # already empty (TTL is only scheduled on *transitions* into empty).
    ch_short_ttl.leave_convo("ghost")
    assert ch_short_ttl._ttl_task is None


# ── Constructor API ──────────────────────────────────────────────────────


def test_default_ttl_is_none():
    """Default construction preserves legacy behavior — infinity."""
    ch = VoiceChannel()
    assert ch._idle_ttl_seconds is None


def test_explicit_ttl_stored():
    """An explicit TTL is stored on the channel unchanged."""
    ch = VoiceChannel(idle_ttl_seconds=30.0)
    assert ch._idle_ttl_seconds == 30.0
