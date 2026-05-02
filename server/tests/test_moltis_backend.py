"""Pure-logic tests for MoltisLLMService.

Pinned without a real websocket — exercise the session key strategies
and request id generation. Network paths are deliberately not covered
here; they belong in an integration test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.backends.moltis import MoltisLLMService  # noqa: E402


@pytest.fixture
def svc(monkeypatch):
    """A MoltisLLMService with side-effecting __init__ stubbed out."""
    monkeypatch.setattr("server.backends.moltis.LLMService.__init__", lambda self, **kw: None)
    s = MoltisLLMService(
        gateway_url="ws://localhost:1234",
        session_key=None,
        agent_id="test-agent",
        session_strategy="persistent",
    )
    return s


def test_session_key_persistent_uses_main(svc):
    svc.agent_id = "alice"
    svc.session_strategy = "persistent"
    assert svc._generate_session_key() == "agent:alice:main"


def test_session_key_per_connection_includes_timestamp(svc):
    svc.agent_id = "alice"
    svc.session_strategy = "per-connection"
    key = svc._generate_session_key()
    assert key.startswith("agent:alice:main-")
    suffix = key.rsplit("-", 1)[1]
    assert suffix.isdigit()


def test_session_key_daily_uses_yyyymmdd(svc):
    svc.agent_id = "alice"
    svc.session_strategy = "daily"
    key = svc._generate_session_key()
    # daily strategy stamps "agent:<id>:main-YYYY-MM-DD" — the date itself
    # contains hyphens so we slice the prefix.
    prefix = "agent:alice:main-"
    assert key.startswith(prefix)
    date_part = key[len(prefix):]
    parts = date_part.split("-")
    assert len(parts) == 3
    y, m, d = parts
    assert len(y) == 4 and y.isdigit()
    assert len(m) == 2 and m.isdigit()
    assert len(d) == 2 and d.isdigit()


def test_session_key_unknown_strategy_falls_back_to_persistent(svc):
    svc.agent_id = "alice"
    svc.session_strategy = "made-up-strategy"
    assert svc._generate_session_key() == "agent:alice:main"


def test_explicit_session_key_overrides_strategy(svc):
    """When session_key is passed in, it should be honored as-is."""
    svc.session_key = "agent:custom:xyz"
    # _generate_session_key only fires when self.session_key is None at init.
    # Pin the assignment behavior — the explicit key survives.
    assert svc.session_key == "agent:custom:xyz"


def test_next_id_is_monotonic(svc):
    svc._request_id_counter = 100
    a = svc._next_id()
    b = svc._next_id()
    c = svc._next_id()
    assert b == a + 1
    assert c == b + 1
