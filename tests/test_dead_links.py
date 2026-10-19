"""Dead linked sessions (WIN-584): opening a Chat whose native session is gone replays the transcript, says so, keeps the link.

Real: VoiceChannel, ConvoStore, ConvoSession, t3_threads.thread_state, native_sessions.link_state (on temp files / a temp SQLite).
Fake: the T3 client (a stub serving a shell snapshot) and the profile manager.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from talky.backends import native_sessions, t3_threads
from talky.convos.session import ConvoSession


class _Shell:
    def __init__(self, threads):
        self.threads = threads

    async def shell(self):
        return {"projects": [], "threads": self.threads}


def test_thread_state_reads_archived_deleted_missing_ok():
    c = _Shell([{"id": "a"}, {"id": "b", "archivedAt": "x"}, {"id": "c", "deletedAt": "x"}])
    got = [asyncio.run(t3_threads.thread_state(i, client=c)) for i in ("a", "b", "c", "zzz")]
    assert got == ["ok", "archived", "deleted", "missing"]


def test_thread_state_unreachable_is_unknown_not_dead():
    class Down:
        async def shell(self):
            raise OSError("down")

    assert asyncio.run(t3_threads.thread_state("a", client=Down())) is None


def test_link_state_claude_and_hermes(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    assert native_sessions.link_state("claude-code", "s1") is None  # no store to ask
    (tmp_path / "claude" / "projects" / "p").mkdir(parents=True)
    (tmp_path / "claude" / "projects" / "p" / "s1.jsonl").write_text("{}\n")
    assert native_sessions.link_state("claude-code", "s1") == "ok"
    assert native_sessions.link_state("claude-code", "gone") == "missing"
    assert native_sessions.link_state("hermes", "h1") is None
    (tmp_path / "hermes").mkdir()
    db = sqlite3.connect(tmp_path / "hermes" / "state.db")
    db.execute("CREATE TABLE sessions (id TEXT)")
    db.execute("INSERT INTO sessions VALUES ('h1')")
    db.commit()
    db.close()
    assert native_sessions.link_state("hermes", "h1") == "ok"
    assert native_sessions.link_state("hermes", "gone") == "missing"


@pytest.fixture
def channel(tmp_path, monkeypatch):
    monkeypatch.setenv("TALKY_CONVOS_DIR", str(tmp_path))

    class _TP:
        def __init__(self, backend):
            self.llm_backend = backend

    class _PM:
        def get_talky_profile(self, name):
            return _TP(name)

    monkeypatch.setattr("talky.shared.profile_manager.get_profile_manager", lambda: _PM())
    from talky.server.channel import VoiceChannel

    ch = VoiceChannel()

    async def noop_switch(name, **kw):
        ch._active_profile = name

    monkeypatch.setattr(ch, "switch_to_profile", noop_switch)
    return ch


def _t3_chat(channel):
    rec = channel._convo_store.create(backend="t3", profile="t3", native_session_id="thread-old")
    channel._convo_store.append_turn(rec.id, "user", "remember pineapple")
    channel._convo_store.append_turn(rec.id, "assistant", "ok")
    return rec


def _state(monkeypatch, value):
    async def fake(thread_id, **kw):
        return value

    monkeypatch.setattr(t3_threads, "thread_state", fake)


@pytest.mark.parametrize("state", ["archived", "deleted", "missing"])
def test_dead_t3_link_replays_notices_and_keeps_the_link(channel, monkeypatch, state):
    rec = _t3_chat(channel)
    _state(monkeypatch, state)

    out = asyncio.run(channel.open_convo(rec.id))

    assert ("archived or deleted" if state == "missing" else state) in out["notice"] and "link is kept" in out["notice"]
    assert out["native_session_id"] is None and out["replay_turns"] == 2
    assert channel._pending_resume["hold_link"] is True
    assert channel._convo_store.get(rec.id).native_session_id == "thread-old"


def test_live_dead_t3_link_does_not_repoint_and_a_new_thread_never_overwrites_the_link(channel, monkeypatch):
    from talky.backends.t3 import T3LLMService

    rec = _t3_chat(channel)
    svc = T3LLMService(resume="thread-other")
    monkeypatch.setattr(channel, "is_live", lambda: True)
    channel._active_profile = "t3"
    channel._llm_services = {"t3": svc}
    channel._convo_session = ConvoSession(channel._convo_store, backend="t3", profile="t3")
    monkeypatch.setattr(channel, "interrupt", lambda: asyncio.sleep(0))
    _state(monkeypatch, "archived")

    out = asyncio.run(channel.open_convo(rec.id))

    assert out["notice"] and svc._thread_id is None, "next message launches a new thread, never the other live one"
    assert "pineapple" in svc._replay_preamble
    # the backend reports the replacement thread; the dead link stays
    session = channel._convo_session
    session._native_id_reader = lambda: "thread-new"
    session.append("user", "hello again")
    assert channel._convo_store.get(rec.id).native_session_id == "thread-old"


def test_live_t3_link_opens_natively_without_notice(channel, monkeypatch):
    rec = _t3_chat(channel)
    _state(monkeypatch, "ok")

    out = asyncio.run(channel.open_convo(rec.id))

    assert out["notice"] is None and out["native_session_id"] == "thread-old" and out["replay_turns"] == 0


def test_unreachable_t3_is_not_called_dead(channel, monkeypatch):
    rec = _t3_chat(channel)
    _state(monkeypatch, None)

    assert asyncio.run(channel.open_convo(rec.id))["notice"] is None


def test_dead_claude_link_replays(channel, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    (tmp_path / "claude" / "projects" / "p").mkdir(parents=True)
    rec = channel._convo_store.create(backend="claude-code", profile="claude-code", native_session_id="sess-gone")
    channel._convo_store.append_turn(rec.id, "user", "hi")

    out = asyncio.run(channel.open_convo(rec.id))

    assert "Claude session" in out["notice"] and out["native_session_id"] is None and out["replay_turns"] == 1
    assert channel._convo_store.get(rec.id).native_session_id == "sess-gone"


def test_open_t3_chat_pulls_in_replies_that_landed_while_it_was_not_live(channel, monkeypatch):
    rec = _t3_chat(channel)
    _state(monkeypatch, "ok")
    calls = []

    async def fake_sync(store, convo_id):
        calls.append(convo_id)
        store.append_turn(convo_id, "assistant", "reply that arrived after the switch")
        return 1

    monkeypatch.setattr(t3_threads, "sync_convo", fake_sync)

    out = asyncio.run(channel.open_convo(rec.id))

    assert calls == [rec.id] and out["native_session_id"] == "thread-old"
    assert channel._convo_store.read_transcript(rec.id)[-1].content == "reply that arrived after the switch"


def test_dead_t3_chat_is_not_synced_from_its_dead_thread(channel, monkeypatch):
    rec = _t3_chat(channel)
    _state(monkeypatch, "deleted")

    async def boom(store, convo_id):
        raise AssertionError("must not sync a dead link")

    monkeypatch.setattr(t3_threads, "sync_convo", boom)
    assert asyncio.run(channel.open_convo(rec.id))["notice"]
