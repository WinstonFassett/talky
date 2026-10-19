"""T3 projects/threads API pieces (WIN-576): grouping, paging, link join, shell memo, ETag."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from talky.backends import t3_threads
from talky.convos.store import ConvoStore

SNAP = json.loads((Path(__file__).parent / "fakes" / "t3_shell.json").read_text())


def _alpha(snap=SNAP):
    return next(p["id"] for p in snap["projects"] if p["title"] == "alpha")


def test_group_projects_excludes_archived_and_orders():
    out = t3_threads.group_projects(SNAP)
    by = {p["title"]: p for p in out["projects"]}
    assert out["seq"] == SNAP["snapshotSequence"]
    assert by["alpha"]["thread_count"] == 28  # 30 minus 2 archived
    assert by["alpha"]["running"] >= 1
    assert by["empty"]["thread_count"] == 0
    titles = [p["title"] for p in out["projects"]]
    assert titles[-1] == "empty", "empty projects sort last"
    assert titles.index("alpha") < titles.index("beta") or by["beta"]["running"]


def test_group_projects_running_first():
    snap = {
        "snapshotSequence": 1,
        "projects": [{"id": "p1", "title": "old"}, {"id": "p2", "title": "busy"}],
        "threads": [
            {"id": "t1", "projectId": "p1", "title": "a", "status": "completed", "updatedAt": "2026-10-18T10:00:00Z"},
            {"id": "t2", "projectId": "p2", "title": "b", "status": "idle", "activeRunId": "r", "updatedAt": "2026-01-01T00:00:00Z"},
        ],
    }
    assert [p["title"] for p in t3_threads.group_projects(snap)["projects"]] == ["busy", "old"]


def test_project_threads_paging_and_cursor():
    pid = _alpha()
    p1 = t3_threads.project_threads(SNAP, pid, {}, 20, None)
    assert len(p1["threads"]) == 20 and p1["next_cursor"] == "o:20"
    p2 = t3_threads.project_threads(SNAP, pid, {}, 20, p1["next_cursor"])
    assert len(p2["threads"]) == 8 and p2["next_cursor"] is None
    ids = [t["id"] for t in p1["threads"] + p2["threads"]]
    assert len(set(ids)) == 28
    statuses = [t["status"] != "running" for t in p1["threads"]]
    assert statuses == sorted(statuses), "running rows first"
    assert all(t["project_id"] == pid for t in p1["threads"])


def test_project_threads_unknown_project():
    with pytest.raises(LookupError):
        t3_threads.project_threads(SNAP, "project:nope", {}, 20, None)


def test_convo_id_joined_from_link_index_with_one_list_call(tmp_path):
    store = ConvoStore(root=tmp_path)
    pid = _alpha()
    tid = t3_threads.project_threads(SNAP, pid, {}, 5, None)["threads"][2]["id"]
    rec = store.create(backend="t3", profile="t3", native_session_id=tid)
    calls = []
    orig = store.list
    store.list = lambda: (calls.append(1), orig())[1]  # type: ignore[method-assign]

    page = t3_threads.project_threads(SNAP, pid, store.link_index("t3"), 5, None)

    assert len(calls) == 1
    assert {t["id"]: t["convo_id"] for t in page["threads"]}[tid] == rec.id
    assert sum(1 for t in page["threads"] if t["convo_id"]) == 1


def test_find_by_link(tmp_path):
    store = ConvoStore(root=tmp_path)
    rec = store.create(backend="t3", profile="t3", native_session_id="thread-X")
    store.create(backend="claude-code", profile="claude", native_session_id="thread-Y")
    assert store.find_by_link("t3", "thread-X").id == rec.id
    assert store.find_by_link("t3", "nope") is None
    assert store.find_by_link("t3", "thread-Y") is None, "other backends ignored"
    assert store.link_index("t3") == {"thread-X": rec.id}


def test_cached_shell_memoizes_for_ttl(monkeypatch):
    t3_threads._shell_memo.clear()
    hits = []

    class C:
        _base = "http://x"

        async def shell(self):
            hits.append(1)
            return {"snapshotSequence": len(hits)}

    c = C()
    clock = [100.0]
    monkeypatch.setattr(t3_threads.time, "monotonic", lambda: clock[0])

    async def go():
        a = await t3_threads.cached_shell(c)
        b = await t3_threads.cached_shell(c)
        clock[0] += 3.5
        d = await t3_threads.cached_shell(c)
        return a, b, d

    a, b, d = asyncio.run(go())
    assert a is b and len(hits) == 2 and d["snapshotSequence"] == 2
    t3_threads._shell_memo.clear()


def test_etag_matching():
    assert t3_threads.etag_for(61605) == '"61605"'
    assert t3_threads.etag_matches('"61605"', 61605)
    assert t3_threads.etag_matches('W/"61605"', 61605)
    assert not t3_threads.etag_matches('"61606"', 61605)
    assert not t3_threads.etag_matches(None, 61605)


def test_no_destructive_rpc_strings():
    src = Path(t3_threads.__file__).read_text().lower()
    for bad in ("thread.delete", "thread.archive", "thread.stop", "settle"):
        assert bad not in src


def test_flat_threads_pinned_first_then_newest_with_convo_join():
    out = t3_threads.flat_threads(SNAP, {}, None, 100)
    rows = out["threads"]
    assert out["total"] == len(rows) == sum(p["thread_count"] for p in t3_threads.group_projects(SNAP)["projects"])
    pinned = [r for r in rows if r["pinned"]]
    assert pinned, "fixture pins some threads"
    assert all(r["pinned"] for r in rows[: len(pinned)]), "pinned rows lead"
    rest = [r["last_activity"] for r in rows[len(pinned):]]
    assert rest == sorted(rest, reverse=True)
    tid = rows[5]["id"]
    assert t3_threads.flat_threads(SNAP, {tid: "convo-1"}, None, 100)["threads"][5]["convo_id"] == "convo-1"


def test_flat_threads_project_filter_and_paging():
    pid = _alpha()
    p1 = t3_threads.flat_threads(SNAP, {}, pid, 20)
    assert len(p1["threads"]) == 20 and p1["next_cursor"] == "o:20" and p1["total"] == 28
    assert {r["project_id"] for r in p1["threads"]} == {pid}
    p2 = t3_threads.flat_threads(SNAP, {}, pid, 20, p1["next_cursor"])
    assert len(p2["threads"]) == 8 and p2["next_cursor"] is None
    with pytest.raises(LookupError):
        t3_threads.flat_threads(SNAP, {}, "project:nope")
