"""Unit tests for Talky-owned convo persistence (WIN-534 slice 1)."""

from __future__ import annotations

import json
import time

import pytest

from talky.convos.store import ConvoRecord, ConvoStore, _derive_title


def test_create_persists_metadata(tmp_path):
    store = ConvoStore(root=tmp_path)
    rec = store.create(backend="echo", profile="echo", title="hello")
    assert (tmp_path / rec.id / "convo.json").exists()
    on_disk = json.loads((tmp_path / rec.id / "convo.json").read_text())
    assert on_disk["backend"] == "echo"
    assert on_disk["profile"] == "echo"
    assert on_disk["title"] == "hello"


def test_append_turn_appends_jsonl_and_touches_last_active(tmp_path):
    store = ConvoStore(root=tmp_path)
    rec = store.create(backend="echo", profile="echo")
    t0 = rec.last_active_at
    time.sleep(0.01)
    store.append_turn(rec.id, "user", "hello there")
    store.append_turn(rec.id, "assistant", "you said: hello there")

    lines = (tmp_path / rec.id / "transcript.jsonl").read_text().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(l) for l in lines]
    assert parsed[0]["role"] == "user"
    assert parsed[0]["content"] == "hello there"
    assert parsed[1]["role"] == "assistant"

    reread = store.get(rec.id)
    assert reread is not None
    assert reread.last_active_at > t0


def test_append_turn_auto_title_from_first_user_utterance(tmp_path):
    store = ConvoStore(root=tmp_path)
    rec = store.create(backend="echo", profile="echo")
    store.append_turn(rec.id, "assistant", "greeting first")
    assert (store.get(rec.id).title or "") == ""  # assistant doesn't title
    store.append_turn(rec.id, "user", "please help with the thing")
    assert store.get(rec.id).title == "please help with the thing"


def test_list_is_newest_first(tmp_path):
    store = ConvoStore(root=tmp_path)
    r1 = store.create(backend="echo", profile="echo")
    time.sleep(0.01)
    r2 = store.create(backend="hermes", profile="hermes")
    time.sleep(0.01)
    store.append_turn(r1.id, "user", "touch r1 last")
    out = store.list()
    assert [r.id for r in out] == [r1.id, r2.id]


def test_blank_turn_is_dropped(tmp_path):
    store = ConvoStore(root=tmp_path)
    rec = store.create(backend="echo", profile="echo")
    store.append_turn(rec.id, "user", "   ")
    assert not (tmp_path / rec.id / "transcript.jsonl").exists()


def test_read_transcript_roundtrips(tmp_path):
    store = ConvoStore(root=tmp_path)
    rec = store.create(backend="echo", profile="echo")
    store.append_turn(rec.id, "user", "first")
    store.append_turn(rec.id, "assistant", "second")
    turns = store.read_transcript(rec.id)
    assert [t.role for t in turns] == ["user", "assistant"]
    assert [t.content for t in turns] == ["first", "second"]


def test_set_native_session_id(tmp_path):
    store = ConvoStore(root=tmp_path)
    rec = store.create(backend="pi", profile="pi")
    store.set_native_session_id(rec.id, "pi-sess-123")
    assert store.get(rec.id).native_session_id == "pi-sess-123"


def test_get_missing_returns_none(tmp_path):
    store = ConvoStore(root=tmp_path)
    assert store.get("does-not-exist") is None


def test_record_from_dict_roundtrips():
    rec = ConvoRecord(
        id="abc",
        backend="echo",
        profile="echo",
        started_at=1.0,
        last_active_at=2.0,
        title="t",
        native_session_id="nsid",
    )
    assert ConvoRecord.from_dict(rec.to_dict()) == rec


def test_derive_title_truncates():
    s = "x" * 100
    assert len(_derive_title(s)) <= 60
    assert _derive_title("short").endswith("short")


def test_env_override_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TALKY_CONVOS_DIR", str(tmp_path / "alt"))
    from talky.convos.store import default_root

    assert default_root() == tmp_path / "alt"


@pytest.mark.asyncio
async def test_user_sink_persists_user_turn_text_frame(tmp_path):
    from pipecat.processors.frame_processor import FrameDirection

    from talky.convos.session import ConvoSession
    from talky.convos.transcript_sink import TalkyUserTranscriptProcessor
    from talky.server.turn import UserTurnTextFrame

    store = ConvoStore(root=tmp_path)
    session = ConvoSession(store, backend="echo", profile="echo")
    sink = TalkyUserTranscriptProcessor(session)
    pushed: list = []

    async def _push(frame, direction=None):  # noqa: ANN001
        pushed.append(frame)

    sink.push_frame = _push  # type: ignore[assignment]

    frame = UserTurnTextFrame(text="hello world", timestamp=time.time())
    await sink.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert session.record is not None
    turns = store.read_transcript(session.record.id)
    assert [(t.role, t.content) for t in turns] == [("user", "hello world")]
    assert pushed == [frame]
