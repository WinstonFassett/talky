"""WIN-587: a T3-linked Chat is refreshed from its T3 thread (T3 is truth, the transcript a cache)."""

import asyncio

import pytest

from talky.backends import t3_threads
from talky.convos.store import ConvoStore

TID = "thread:p:a:b"


class FakeT3:
    def __init__(self, messages, status="completed", down=False):
        self.messages, self.status, self.down = messages, status, down

    async def shell(self):
        if self.down:
            raise ConnectionError("t3 down")
        return {"projects": [], "threads": [{"id": TID, "title": "x", "projectId": "p", "status": self.status}]}

    async def thread_projection(self, _tid):
        return {"messages": self.messages}

    async def close(self):
        pass


def _m(role, text, at="2026-10-19T10:00:00.000Z"):
    return {"role": role, "text": text, "createdAt": at}


@pytest.fixture
def t3(monkeypatch):
    fake = FakeT3([])
    monkeypatch.setattr(t3_threads, "T3Client", lambda *a, **k: fake)
    monkeypatch.setattr(t3_threads, "ensure_token", lambda *a, **k: "t")
    return fake


def _linked(tmp_path, *turns):
    store = ConvoStore(root=tmp_path)
    rec = store.create(backend="t3", profile="t3", native_session_id=TID, title="x")
    for role, text in turns:
        store.append_turn(rec.id, role, text)
    return store, rec


def _turns(store, rec):
    return [(t.role, t.content) for t in store.read_transcript(rec.id)]


def test_abandoned_reply_and_web_typed_message_are_appended_once(tmp_path, t3):
    store, rec = _linked(tmp_path, ("user", "hi"))
    store.set_in_progress(rec.id, True)
    t3.messages = [
        _m("user", "hi"),
        _m("assistant", "the reply", "2026-10-19T10:00:05.000Z"),
        _m("user", "typed in T3 web", "2026-10-19T10:05:00.000Z"),
        _m("assistant", "web answer", "2026-10-19T10:05:03.000Z"),
    ]
    assert asyncio.run(t3_threads.sync_convo(store, rec.id)) == 3
    want = [("user", "hi"), ("assistant", "the reply"), ("user", "typed in T3 web"), ("assistant", "web answer")]
    assert _turns(store, rec) == want
    ts = [t.timestamp for t in store.read_transcript(rec.id)][1:]
    assert ts == [1792404005.0, 1792404300.0, 1792404303.0]  # T3 created_at, in order
    assert store.get(rec.id).in_progress is False
    # idempotent
    assert asyncio.run(t3_threads.sync_convo(store, rec.id)) == 0
    assert _turns(store, rec) == want


def test_running_thread_sets_in_progress(tmp_path, t3):
    store, rec = _linked(tmp_path, ("user", "hi"))
    t3.status, t3.messages = "running", [_m("user", "hi")]
    asyncio.run(t3_threads.sync_convo(store, rec.id))
    assert store.get(rec.id).in_progress is True
    t3.status = "completed"
    asyncio.run(t3_threads.sync_convo(store, rec.id))
    assert store.get(rec.id).in_progress is False


def test_unreachable_t3_leaves_data_alone(tmp_path, t3):
    store, rec = _linked(tmp_path, ("user", "hi"))
    store.set_in_progress(rec.id, True)
    t3.down = True
    assert asyncio.run(t3_threads.sync_convo(store, rec.id)) == 0
    assert _turns(store, rec) == [("user", "hi")] and store.get(rec.id).in_progress is True


def test_no_overlap_never_duplicates(tmp_path, t3):
    store, rec = _linked(tmp_path, ("user", "local only"))
    t3.messages = [_m("user", "something else")]
    assert asyncio.run(t3_threads.sync_convo(store, rec.id)) == 0
    assert _turns(store, rec) == [("user", "local only")]


def test_attach_existing_link_syncs(tmp_path, t3):
    store, rec = _linked(tmp_path, ("user", "hi"))
    t3.messages = [_m("user", "hi"), _m("assistant", "yo")]
    out = asyncio.run(t3_threads.attach_thread(TID, store=store))
    assert out["created"] is False and out["convo_id"] == rec.id
    assert _turns(store, rec) == [("user", "hi"), ("assistant", "yo")]


def test_get_convo_api_syncs_unless_live(tmp_path, t3, monkeypatch):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    import talky.server.__main__ as m

    store, rec = _linked(tmp_path, ("user", "hi"))
    t3.messages = [_m("user", "hi"), _m("assistant", "yo")]
    monkeypatch.setattr(m.voice_channel, "_convo_store", store)
    monkeypatch.setattr(m.voice_channel, "_convo_session", None)
    routes, _ = m._build_webrtc_routes()
    with TestClient(Starlette(routes=routes)) as client:
        class Live:
            convo_id = rec.id

        monkeypatch.setattr(m.voice_channel, "_convo_session", Live())
        assert client.get(f"/api/convos/{rec.id}").json()["total"] == 1  # live call: no racing writes
        monkeypatch.setattr(m.voice_channel, "_convo_session", None)
        body = client.get(f"/api/convos/{rec.id}").json()
        assert [t["content"] for t in body["turns"]] == ["hi", "yo"]
        assert store.get(rec.id).in_progress is False
        t3.down = True
        assert client.get(f"/api/convos/{rec.id}").status_code == 200
