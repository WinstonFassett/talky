"""Unit tests for convo resume (WIN-534): sessions, replay, the daemon hook, the CLI."""

from __future__ import annotations

import argparse
import io
import json
import time

import pytest

from talky.backends.echo import EchoLLMService
from talky.convos.replay import HistoryPreambleMixin, format_history_preamble
from talky.convos.session import ConvoSession, native_session_id_of
from talky.convos.store import ConvoStore

TURNS = [
    {"role": "user", "content": "my code word is pineapple"},
    {"role": "assistant", "content": "got it"},
]


# --- replay ----------------------------------------------------------------


def test_preamble_contains_turns_and_is_one_shot():
    class Svc(HistoryPreambleMixin):
        pass

    svc = Svc()
    svc.set_history(TURNS)
    first = svc._consume_preamble("what was my code word?")
    assert "user: my code word is pineapple" in first
    assert first.endswith("what was my code word?")
    assert svc._consume_preamble("again") == "again"


def test_preamble_drops_oldest_turns_past_the_cap():
    turns = [{"role": "user", "content": f"turn {i} " + "x" * 50} for i in range(10)]
    out = format_history_preamble(turns, max_chars=200)
    assert "turn 9" in out and "turn 0" not in out


def test_empty_history_gives_no_preamble():
    assert format_history_preamble([]) == ""


@pytest.mark.parametrize(
    "module, cls",
    [
        ("talky.backends.claude_code", "ClaudeCodeLLMService"),
        ("talky.backends.hermes", "HermesLLMService"),
        ("talky.backends.opencode", "OpencodeLLMService"),
        ("talky.backends.pi_rpc", "PiRPCLLMService"),
        ("talky.backends.moltis", "MoltisLLMService"),
    ],
)
def test_every_real_backend_accepts_history(module, cls):
    import importlib

    assert issubclass(getattr(importlib.import_module(module), cls), HistoryPreambleMixin)


def test_hermes_import_keeps_talky_importable_and_does_not_leak_env():
    import os
    import sys

    before = list(sys.path)
    import talky.backends.hermes  # noqa: F401

    assert sys.path[: len(before)] == before, "talky's own sys.path entries stay first"
    assert "HERMES_DISABLE_LAZY_INSTALLS" not in os.environ


def test_echo_set_history_recalls_once():
    svc = EchoLLMService()
    svc.set_history(TURNS)
    assert "pineapple" in svc.render("remind me")
    assert "pineapple" not in svc.render("again")


# --- session ---------------------------------------------------------------


def test_session_creates_record_only_on_first_turn(tmp_path):
    store = ConvoStore(root=tmp_path)
    session = ConvoSession(store, backend="echo", profile="echo")
    assert store.list() == []
    session.append("user", "   ")
    assert store.list() == []
    session.append("user", "hi")
    assert [r.id for r in store.list()] == [session.record.id]


def test_session_resume_into_appends_to_existing_record(tmp_path):
    store = ConvoStore(root=tmp_path)
    old = store.create(backend="echo", profile="echo")
    store.append_turn(old.id, "user", "first call")

    session = ConvoSession(store, backend="claude-code", profile="claude", resume_into=old.id)
    session.append("user", "second call")

    assert [r.id for r in store.list()] == [old.id]
    assert [t.content for t in store.read_transcript(old.id)] == ["first call", "second call"]
    rec = store.get(old.id)
    assert (rec.backend, rec.profile) == ("claude-code", "claude")


def test_session_captures_native_id_once_backend_has_one(tmp_path):
    store = ConvoStore(root=tmp_path)
    box = {"id": None}
    session = ConvoSession(store, backend="claude-code", profile="claude", native_id_reader=lambda: box["id"])
    session.append("user", "hi")
    assert store.get(session.record.id).native_session_id is None
    session.append("assistant", "hello")
    box["id"] = "sdk-session-1"  # reported only after the last reply
    session.close()
    assert store.get(session.record.id).native_session_id == "sdk-session-1"


def test_session_skips_ignored_announcement_once(tmp_path):
    store = ConvoStore(root=tmp_path)
    session = ConvoSession(store, backend="claude-code", profile="claude")
    session.ignore_assistant("Claude here.")
    session.append("assistant", "Claude  here.")
    session.append("user", "hi")
    session.append("assistant", "Claude here.")
    assert [(t.role, t.content) for t in store.read_transcript(session.record.id)] == [
        ("user", "hi"),
        ("assistant", "Claude here."),
    ]


def test_native_session_id_of_reads_known_attributes():
    class A:
        _last_session_id = "abc"

    assert native_session_id_of(A()) == "abc"
    assert native_session_id_of(object()) is None


def test_assistant_sink_flush_saves_half_spoken_reply(tmp_path):
    from pipecat.utils.string import TextPartForConcatenation

    from talky.convos.transcript_sink import ConvoAssistantTranscriptProcessor

    store = ConvoStore(root=tmp_path)
    session = ConvoSession(store, backend="echo", profile="echo")
    sink = ConvoAssistantTranscriptProcessor(session)
    sink._current_assistant_text_parts = [
        TextPartForConcatenation("earlier you said", includes_inter_part_spaces=False),
        TextPartForConcatenation("pineapple", includes_inter_part_spaces=False),
    ]
    sink.flush()
    turns = store.read_transcript(session.record.id)
    assert [(t.role, t.content) for t in turns] == [("assistant", "earlier you said pineapple")]
    sink.flush()  # nothing left; no duplicate
    assert len(store.read_transcript(session.record.id)) == 1


# --- daemon side: VoiceChannel.resume_convo ---------------------------------


@pytest.fixture
def channel(tmp_path, monkeypatch):
    monkeypatch.setenv("TALKY_CONVOS_DIR", str(tmp_path))

    class _TP:
        def __init__(self, backend):
            self.llm_backend = backend

    class _PM:
        def get_talky_profile(self, name):
            return _TP({"claude": "claude-code"}.get(name, name))

    monkeypatch.setattr("talky.shared.profile_manager.get_profile_manager", lambda: _PM())
    from talky.server.channel import VoiceChannel

    return VoiceChannel()


def test_resume_convo_without_live_call_is_pending_replay(channel):
    rec = channel._convo_store.create(backend="echo", profile="echo")
    channel._convo_store.append_turn(rec.id, "user", "my code word is pineapple")

    out = channel.resume_convo(rec.id, "claude")

    assert out["applied"] == "next_call"
    assert out["native_session_id"] is None and out["replay_turns"] == 1
    assert channel._pending_resume["backend"] == "claude-code"
    assert channel._pending_resume["convo_id"] == rec.id


def test_resume_convo_same_backend_with_native_id_skips_replay(channel):
    rec = channel._convo_store.create(backend="claude-code", profile="claude", native_session_id="sdk-1")
    channel._convo_store.append_turn(rec.id, "user", "hi")

    out = channel.resume_convo(rec.id)

    assert out["native_session_id"] == "sdk-1" and out["replay_turns"] == 0
    assert channel._pending_resume["replay"] is False


def test_resume_convo_unknown_id_raises(channel):
    with pytest.raises(ValueError):
        channel.resume_convo("nope")


def test_apply_replay_seeds_backend():
    from talky.server.channel import VoiceChannel

    svc = EchoLLMService()
    VoiceChannel._apply_replay(svc, {"replay": True, "turns": TURNS, "backend": "echo"})
    assert "pineapple" in svc.render("x")


# --- CLI: talky resume -----------------------------------------------------


def test_cmd_resume_asks_daemon_then_switches_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("TALKY_CONVOS_DIR", str(tmp_path / "convos"))
    import talky.cli as cli
    from talky.convos import default_store

    rec = default_store().create(backend="claude-code", profile="claude", native_session_id="sdk-1")
    calls: list = []

    def fake_urlopen(req, timeout=3):  # noqa: ARG001
        url = req if isinstance(req, str) else req.full_url
        path = url.split("8765", 1)[-1] if "8765" in url else url
        body = json.loads(req.data) if not isinstance(req, str) and req.data else None
        calls.append((path, body))
        if path.endswith("/status"):
            payload = {"channel": {"live": True}}
        elif path.endswith("/api/convos/resume"):
            payload = {"backend": "claude-code", "native_session_id": "sdk-1", "replay_turns": 0, "applied": "live"}
        else:
            payload = {"status": "ok"}

        class _R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _R(json.dumps(payload).encode())

    monkeypatch.setattr(cli, "ensure_daemon", lambda *a, **k: True)
    monkeypatch.setattr(cli, "daemon_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(cli, "daemon_urlopen", fake_urlopen)
    monkeypatch.setattr(cli, "_open_client", lambda url: calls.append(("OPEN", url)))

    cli.cmd_resume(argparse.Namespace(id=None, profile=None, no_open=False))  # no id = most recent

    posts = [(p, b) for p, b in calls if b is not None]
    assert [p for p, _ in posts] == ["/api/convos/resume", "/api/profile"]
    assert posts[0][1] == {"convo_id": rec.id, "profile": "claude"}
    assert not (tmp_path / ".talky" / "run" / "talky-args.json").exists(), "CLI no longer writes startup args"
    assert not any(c[0] == "OPEN" for c in calls), "live call: nothing to open"


@pytest.mark.asyncio
async def test_echo_processes_frame_with_seeded_recall():
    from pipecat.frames.frames import TextFrame
    from pipecat.processors.frame_processor import FrameDirection

    from talky.server.turn import UserTurnTextFrame

    svc = EchoLLMService()
    svc.set_history(TURNS)
    emitted: list = []

    async def _push(frame, direction=None):  # noqa: ANN001
        emitted.append(frame)

    svc.push_frame = _push  # type: ignore[assignment]
    await svc.process_frame(UserTurnTextFrame(text="say it", timestamp=time.time()), FrameDirection.DOWNSTREAM)
    texts = [f.text for f in emitted if isinstance(f, TextFrame)]
    assert texts and "pineapple" in texts[0]


# --- channel races that broke resume ----------------------------------------


@pytest.mark.asyncio
async def test_stale_disconnect_from_replaced_peer_does_not_tear_down(channel, monkeypatch):
    torn = []

    async def fake_teardown(preserve_active_profile=False):
        torn.append(preserve_active_profile)

    monkeypatch.setattr(channel, "_teardown_locked", fake_teardown)
    old_peer, new_peer = object(), object()
    channel._transport = new_peer

    await channel._disconnect_cleanup(old_peer)
    assert torn == []

    await channel._disconnect_cleanup(new_peer)
    assert torn == [True]


def test_claude_never_continues_the_cwds_latest_session():
    from talky.backends.claude_code import ClaudeCodeLLMService

    assert ClaudeCodeLLMService()._build_options().continue_conversation is False
    opts = ClaudeCodeLLMService(resume="sdk-1")._build_options()
    assert opts.continue_conversation is False and opts.resume == "sdk-1"


def test_claude_sdk_handles_rate_limit_and_unknown_messages_natively():
    """claude-agent-sdk needs no monkeypatch (WIN-547): rate_limit_event is a
    first-class message and unknown types are skipped, so the stream survives.
    """
    from claude_agent_sdk import RateLimitEvent
    from claude_agent_sdk._internal.message_parser import parse_message

    # The CLI's rate_limit_event — the type that used to kill claude-code-sdk
    # 0.0.25 streams mid-reply — parses to a typed message, not an exception.
    evt = parse_message({
        "type": "rate_limit_event",
        "rate_limit_info": {"status": "allowed_warning"},
        "uuid": "u1",
        "session_id": "s1",
    })
    assert isinstance(evt, RateLimitEvent)

    # Unknown message types return None (skipped) instead of ending the stream.
    assert parse_message({"type": "future_event_talky_does_not_know"}) is None


# --- review fixes (WIN-534) ----------------------------------------------------


def test_backend_change_replaces_native_id(tmp_path):
    store = ConvoStore(root=tmp_path)
    rec = store.create(backend="claude-code", profile="claude", native_session_id="claude-sid")
    session = ConvoSession(store, backend="pi", profile="pi", resume_into=rec.id, native_id_reader=lambda: None)
    session.append("user", "hi")
    got = store.get(rec.id)
    assert (got.backend, got.native_session_id) == ("pi", None)


def test_disabled_session_writes_nothing(tmp_path):
    store = ConvoStore(root=tmp_path)
    ConvoSession(store, enabled=False, backend="echo", profile="echo").append("user", "hi")
    assert store.list() == []


def test_unplayed_announcement_does_not_swallow_a_later_reply(tmp_path):
    store = ConvoStore(root=tmp_path)
    session = ConvoSession(store, backend="claude-code", profile="claude")
    session.ignore_assistant("Claude here.")  # interrupted, never recorded
    session.append("user", "say: Claude here.")
    session.append("assistant", "Claude here.")
    assert [t.content for t in store.read_transcript(session.record.id)] == ["say: Claude here.", "Claude here."]


@pytest.mark.asyncio
async def test_shutdown_cancels_native_id_recheck(tmp_path):
    store = ConvoStore(root=tmp_path)
    session = ConvoSession(store, backend="echo", profile="echo")
    session.append("assistant", "hello")
    assert session._recheck is not None
    handle = session._recheck
    session.shutdown()
    assert handle.cancelled() and session._recheck is None


def test_store_rejects_non_uuid_ids_and_is_private(tmp_path):
    import stat

    store = ConvoStore(root=tmp_path / "convos")
    assert store.get("../../etc") is None
    with pytest.raises(ValueError):
        store.append_turn("../evil", "user", "x")
    rec = store.create(backend="echo", profile="echo")
    store.append_turn(rec.id, "user", "secret")
    mode = lambda p: stat.S_IMODE(p.stat().st_mode)  # noqa: E731
    assert mode(tmp_path / "convos") == 0o700
    assert mode(tmp_path / "convos" / rec.id) == 0o700
    assert mode(tmp_path / "convos" / rec.id / "transcript.jsonl") == 0o600
    assert mode(tmp_path / "convos" / rec.id / "convo.json") == 0o600


def test_list_skips_record_whose_id_does_not_match_its_dir(tmp_path):
    import uuid as _uuid

    store = ConvoStore(root=tmp_path)
    good = store.create(backend="echo", profile="echo")
    bad_dir = tmp_path / str(_uuid.uuid4())
    bad_dir.mkdir()
    (bad_dir / "convo.json").write_text(json.dumps({**good.to_dict(), "id": "/tmp/elsewhere"}))
    assert [r.id for r in store.list()] == [good.id]


def test_pending_resume_expires(channel, monkeypatch):
    rec = channel._convo_store.create(backend="echo", profile="echo")
    channel._convo_store.append_turn(rec.id, "user", "hi")
    channel.resume_convo(rec.id)
    channel._pending_resume["expires_at"] = 0
    assert channel._take_pending_resume() is None
    channel.resume_convo(rec.id)
    assert channel._take_pending_resume()["convo_id"] == rec.id


def test_live_resume_always_replays_even_when_native(channel, monkeypatch):
    rec = channel._convo_store.create(backend="claude-code", profile="claude", native_session_id="sdk-1")
    channel._convo_store.append_turn(rec.id, "user", "my code word is kiwi")

    seen = {}

    class Svc:
        def set_history(self, turns):
            seen["turns"] = turns

    monkeypatch.setattr(channel, "is_live", lambda: True)
    channel._llm_services = {"claude-code": Svc()}
    channel._convo_session = ConvoSession(channel._convo_store, backend="claude-code", profile="claude")

    out = channel.resume_convo(rec.id)

    assert out["applied"] == "live"
    assert seen["turns"][0]["content"] == "my code word is kiwi"
    assert channel._convo_session.resume_into == rec.id
    channel._active_profile = "claude"
    assert channel._take_pending_resume()["native_session_id"] == "sdk-1", "next build still resumes natively"


@pytest.mark.asyncio
async def test_reconnect_carries_the_convo_to_the_next_build(channel):
    rec = channel._convo_store.create(backend="claude-code", profile="claude", native_session_id="sdk-1")
    session = ConvoSession(channel._convo_store, backend="claude-code", profile="claude", resume_into=rec.id)
    session.append("user", "hi")
    channel._convo_session = session
    channel._active_profile = "claude"

    await channel._teardown_locked(preserve_active_profile=True)

    pending = channel._take_pending_resume()
    assert pending["convo_id"] == rec.id and pending["native_session_id"] == "sdk-1"


@pytest.mark.asyncio
async def test_explicit_detach_does_not_carry_the_convo(channel):
    rec = channel._convo_store.create(backend="echo", profile="echo")
    session = ConvoSession(channel._convo_store, backend="echo", profile="echo", resume_into=rec.id)
    session.append("user", "hi")
    channel._convo_session = session

    await channel._teardown_locked(preserve_active_profile=False)

    assert channel._take_pending_resume() is None


@pytest.mark.asyncio
async def test_reconnect_after_profile_switch_replays_into_the_new_backend(channel):
    rec = channel._convo_store.create(backend="claude-code", profile="claude", native_session_id="sdk-1")
    session = ConvoSession(channel._convo_store, backend="claude-code", profile="claude", resume_into=rec.id)
    session.append("user", "my code word is heron")
    channel._convo_session = session
    channel._active_profile = "claude"

    await channel._teardown_locked(preserve_active_profile=True)

    channel._active_profile = "pi"  # switched between calls; resolved at the next build
    pending = channel._take_pending_resume()
    assert pending["backend"] == "pi" and pending["native_session_id"] is None and pending["replay"]


def test_new_convo_drops_pending_and_starts_a_fresh_record(channel):
    rec = channel._convo_store.create(backend="echo", profile="echo")
    session = ConvoSession(channel._convo_store, backend="echo", profile="echo", resume_into=rec.id)
    session.append("user", "old")
    channel._convo_session = session
    channel._pending_resume = {"convo_id": rec.id, "follow_active": True, "expires_at": 1e18}

    channel.new_convo()
    session.append("user", "new")

    assert channel._take_pending_resume() is None
    assert session.record.id != rec.id
    assert [t.content for t in channel._convo_store.read_transcript(rec.id)] == ["old"]


def test_live_resume_then_more_turns_then_reconnect_sees_the_new_turns(channel, monkeypatch):
    rec = channel._convo_store.create(backend="echo", profile="echo")
    channel._convo_store.append_turn(rec.id, "user", "first")

    class Svc:
        def set_history(self, turns):
            pass

    monkeypatch.setattr(channel, "is_live", lambda: True)
    channel._llm_services = {"pi": Svc()}
    channel._convo_session = ConvoSession(channel._convo_store, backend="pi", profile="pi")
    channel.resume_convo(rec.id, "pi")
    channel._convo_session.append("user", "said after the resume")

    channel._active_profile = "pi"
    plan = channel._take_pending_resume()
    assert [t["content"] for t in plan["turns"]] == ["first", "said after the resume"]


def test_corrupt_record_does_not_block_the_next_build(channel, tmp_path):
    rec = channel._convo_store.create(backend="echo", profile="echo")
    (channel._convo_store.root / rec.id / "convo.json").write_text("{not json")
    channel._pending_resume = {"convo_id": rec.id, "follow_active": True, "expires_at": 1e18}
    assert channel._take_pending_resume() is None


def test_mcp_mode_does_not_take_over_the_session_profile(tmp_path):
    store = ConvoStore(root=tmp_path)
    session = ConvoSession(store, backend="claude", profile="claude")
    session.append("user", "hello")
    rec_id = session.record.id

    session.set_active(backend="__mcp__", profile="__mcp__", native_id_reader=None)
    session.append("user", "typed while in mcp mode")

    assert (session.backend, session.profile) == ("claude", "claude")
    rec = store.get(rec_id)
    assert (rec.backend, rec.profile) == ("claude", "claude")
