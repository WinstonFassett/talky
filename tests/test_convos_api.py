"""Session browsing (WIN-535): store search/paging/rename/delete, live status, HTTP routes."""

from __future__ import annotations

import time

import pytest

from talky.convos.session import ConvoSession
from talky.convos.store import ConvoStore

# --- store -----------------------------------------------------------------


def _convo(store: ConvoStore, *turns: tuple[str, str], profile: str = "echo"):
    rec = store.create(backend=profile, profile=profile)
    for role, content in turns:
        store.append_turn(rec.id, role, content)
        time.sleep(0.002)
    return store.get(rec.id)


def test_search_matches_title_and_transcript_with_snippet(tmp_path):
    store = ConvoStore(root=tmp_path)
    a = _convo(store, ("user", "plan the garden"), ("assistant", "plant tomatoes near the fence"))
    b = _convo(store, ("user", "fix the Python build"))

    by_text = store.search("TOMATOES")
    assert [r.id for r, _ in by_text] == [a.id]
    assert "tomatoes" in by_text[0][1]

    by_title = store.search("python")
    assert [r.id for r, _ in by_title] == [b.id]

    assert len(store.search("")) == 2
    assert store.search("nothing like this") == []


def test_update_renames_pins_archives_without_reordering(tmp_path):
    store = ConvoStore(root=tmp_path)
    rec = _convo(store, ("user", "hello"))
    before = rec.last_active_at

    out = store.update(rec.id, title="  My   chat ", pinned=True, archived=True)

    assert out is not None
    again = store.get(rec.id)
    assert (again.title, again.pinned, again.archived) == ("My chat", True, True)
    assert again.last_active_at == before
    assert store.update("00000000-0000-4000-8000-000000000000", title="x") is None


def test_delete_removes_record_even_if_corrupt(tmp_path):
    store = ConvoStore(root=tmp_path)
    rec = _convo(store, ("user", "bye"))
    (tmp_path / rec.id / "convo.json").write_text("{not json")

    assert store.delete(rec.id) is True
    assert not (tmp_path / rec.id).exists()
    assert store.delete(rec.id) is False
    assert store.delete("../etc") is False


def test_transcript_pages_back_from_the_end(tmp_path):
    store = ConvoStore(root=tmp_path)
    rec = store.create(backend="echo", profile="echo")
    for i in range(5):
        store.append_turn(rec.id, "user", f"turn {i}")

    last, start, total = store.read_transcript_page(rec.id, limit=2)
    assert [t.content for t in last] == ["turn 3", "turn 4"] and (start, total) == (3, 5)

    older, start2, _ = store.read_transcript_page(rec.id, before=start, limit=2)
    assert [t.content for t in older] == ["turn 1", "turn 2"] and start2 == 1

    oldest, start3, _ = store.read_transcript_page(rec.id, before=start2, limit=2)
    assert [t.content for t in oldest] == ["turn 0"] and start3 == 0


def test_old_records_without_pin_fields_still_load(tmp_path):
    store = ConvoStore(root=tmp_path)
    rec = store.create(backend="echo", profile="echo")
    p = tmp_path / rec.id / "convo.json"
    p.write_text(p.read_text().replace('"pinned": false,', "").replace('"archived": false', '"x": 1'))
    again = store.get(rec.id)
    assert again is not None and again.pinned is False and again.archived is False


# --- session status + change events ------------------------------------------


def test_session_reports_created_then_turns_with_status(tmp_path):
    store = ConvoStore(root=tmp_path)
    seen: list[tuple[str, str, str]] = []
    session = ConvoSession(store, backend="echo", profile="echo", on_change=lambda *a: seen.append(a))

    session.append("user", "hi")
    assert session.status == "working"
    session.append("assistant", "hello")
    assert session.status == "idle"
    session.append("user", "boom")
    session.set_failed()

    cid = session.record.id
    assert seen == [
        (cid, "created", "working"),
        (cid, "turn", "idle"),
        (cid, "turn", "working"),
        (cid, "status", "failed"),
    ]


def test_session_resuming_existing_record_reports_turn_not_created(tmp_path):
    store = ConvoStore(root=tmp_path)
    rec = _convo(store, ("user", "first"))
    seen: list[tuple] = []
    session = ConvoSession(store, backend="echo", profile="echo", resume_into=rec.id, on_change=lambda *a: seen.append(a))
    session.append("user", "again")
    assert seen == [(rec.id, "turn", "working")]


# --- daemon: current convo + routes ------------------------------------------


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    """The daemon's real routes over a temp store, no pipeline."""
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    import talky.server.__main__ as m

    class _TP:
        def __init__(self, backend):
            self.llm_backend = backend

    class _PM:
        def get_talky_profile(self, name):
            return _TP(name)

    monkeypatch.setattr("talky.shared.profile_manager.get_profile_manager", lambda: _PM())
    store = ConvoStore(root=tmp_path)
    monkeypatch.setattr(m.voice_channel, "_convo_store", store)
    monkeypatch.setattr(m.voice_channel, "_pending_resume", None)
    monkeypatch.setattr(m.voice_channel, "_convo_session", None)
    routes, _ = m._build_webrtc_routes()
    with TestClient(Starlette(routes=routes)) as client:
        yield client, store, m.voice_channel


def test_list_search_and_current(daemon):
    client, store, channel = daemon
    old = _convo(store, ("user", "about cats"), ("assistant", "cats purr"))
    new = _convo(store, ("user", "about dogs"))

    body = client.get("/api/convos").json()
    assert [c["id"] for c in body["convos"]] == [new.id, old.id]
    assert body["current"] is None

    hits = client.get("/api/convos", params={"q": "purr"}).json()["convos"]
    assert [c["id"] for c in hits] == [old.id] and "purr" in hits[0]["snippet"]

    channel.resume_convo(old.id)
    assert client.get("/api/convos").json()["current"] == {"id": old.id, "status": "idle", "live": False}

    channel.new_convo()
    assert client.get("/api/convos").json()["current"] is None


def test_pinned_first_and_archived_hidden_by_default(daemon):
    client, store, _ = daemon
    a = _convo(store, ("user", "a"))
    b = _convo(store, ("user", "b"))
    c = _convo(store, ("user", "c"))

    assert client.patch(f"/api/convos/{a.id}", json={"pinned": True}).status_code == 200
    assert client.patch(f"/api/convos/{c.id}", json={"archived": True}).status_code == 200

    assert [x["id"] for x in client.get("/api/convos").json()["convos"]] == [a.id, b.id]
    assert [x["id"] for x in client.get("/api/convos?archived=1").json()["convos"]] == [c.id]
    assert len(client.get("/api/convos?archived=all").json()["convos"]) == 3


def test_get_convo_pages_turns(daemon):
    client, store, _ = daemon
    rec = store.create(backend="echo", profile="echo")
    for i in range(5):
        store.append_turn(rec.id, "user", f"t{i}")

    page = client.get(f"/api/convos/{rec.id}", params={"limit": 2}).json()
    assert [t["content"] for t in page["turns"]] == ["t3", "t4"]
    assert [t["index"] for t in page["turns"]] == [3, 4]
    assert (page["start"], page["total"]) == (3, 5)

    older = client.get(f"/api/convos/{rec.id}", params={"limit": 2, "before": page["start"]}).json()
    assert [t["content"] for t in older["turns"]] == ["t1", "t2"]

    assert client.get("/api/convos/00000000-0000-4000-8000-000000000000").status_code == 404
    assert client.get(f"/api/convos/{rec.id}", params={"before": "x"}).status_code == 400


def test_patch_validates_and_renames(daemon):
    client, store, _ = daemon
    rec = _convo(store, ("user", "hello"))
    assert client.patch(f"/api/convos/{rec.id}", json={"pinned": "yes"}).status_code == 400
    assert client.patch(f"/api/convos/{rec.id}", json={"title": 5}).status_code == 400
    r = client.patch(f"/api/convos/{rec.id}", json={"title": "Renamed"})
    assert r.status_code == 200 and r.json()["convo"]["title"] == "Renamed"
    assert store.get(rec.id).title == "Renamed"


def test_delete_current_convo_starts_fresh(daemon):
    client, store, channel = daemon
    rec = _convo(store, ("user", "secret"))
    channel.resume_convo(rec.id)

    assert client.delete(f"/api/convos/{rec.id}").status_code == 200
    assert store.get(rec.id) is None
    assert channel._pending_resume is None
    assert client.get("/api/convos").json()["current"] is None
    assert client.delete(f"/api/convos/{rec.id}").status_code == 404


def test_backend_error_marks_the_live_convo_failed(daemon):
    _, store, channel = daemon
    from talky.server.event_bus import Event

    session = ConvoSession(store, backend="echo", profile="echo")
    session.append("user", "hi")
    channel._convo_session = session
    channel._on_bus_event(Event(type="backendError", data={"message": "boom"}))
    assert session.status == "failed"
    channel._convo_session = None


# --- review fixes (WIN-535) ----------------------------------------------------


@pytest.mark.asyncio
async def test_reload_before_speaking_still_carries_the_opened_convo(daemon):
    """A session opened in the UI but not spoken in yet survives a reload."""
    _, store, channel = daemon
    rec = _convo(store, ("user", "earlier"))
    session = ConvoSession(store, backend="echo", profile="echo", resume_into=rec.id)
    channel._convo_session = session
    channel._active_profile = "echo"

    await channel._teardown_locked(preserve_active_profile=True)

    assert channel.current_convo()["id"] == rec.id
    assert channel._take_pending_resume()["convo_id"] == rec.id


@pytest.mark.asyncio
async def test_rename_keeps_the_current_rows_status(daemon):
    import asyncio

    from talky.server.event_bus import event_bus

    _, store, channel = daemon
    rec = _convo(store, ("user", "hi"))
    session = ConvoSession(store, backend="echo", profile="echo", resume_into=rec.id)
    channel._convo_session = session
    async with event_bus.subscribe() as q:
        session.append("user", "busy")  # no listener wired: just sets status
        channel._emit_convo_changed(rec.id, "updated")
        ev = await asyncio.wait_for(q.get(), 2)
    channel._convo_session = None
    assert ev.type == "convoChanged" and ev.data["status"] == "working"


def test_patch_on_corrupt_record_is_a_clean_500(daemon):
    client, store, _ = daemon
    rec = _convo(store, ("user", "x"))
    (store.root / rec.id / "convo.json").write_text("{nope")
    r = client.patch(f"/api/convos/{rec.id}", json={"title": "y"})
    assert r.status_code == 500 and "could not update" in r.json()["error"]
