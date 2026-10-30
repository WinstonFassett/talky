"""List and attach to existing T3 Code threads (WIN-564).

Reads the cross-project shell snapshot (read-only) and normalises each thread for
pickers: title, project, status (running / idle / completed / failed), last activity,
model. ``attach_thread`` mints (or reuses) a Talky convo bound to a T3 thread, seeded
with the thread's recent messages so the transcript shows context.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from talky.backends import t3_auth
from talky.backends.t3_auth import T3AuthError, ensure_token
from talky.backends.t3_client import T3Client
from talky.convos.store import TranscriptTurn

DEFAULT_BASE_URL = "http://127.0.0.1:3773"
CONTEXT_MESSAGES = 12

_STATUS = {"running": "running", "idle": "idle", "completed": "completed", "failed": "failed"}


def base_url() -> str:
    return os.environ.get("T3_BASE_URL") or DEFAULT_BASE_URL


async def _shell(c: T3Client) -> dict:
    """The shell snapshot; a 401 (revoked/expired session) re-issues once and retries once."""
    try:
        return await c.shell()
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 401:
            raise
    rec = await asyncio.to_thread(t3_auth.issue)
    c._token = rec["token"]  # noqa: SLF001
    try:
        return await c.shell()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise T3AuthError("T3 rejected the new session", "denied") from e
        raise


def short_id(thread_id: str) -> str:
    """T3 ids can look like ``thread:project:<uuid>:<uuid>``; the last uuid's head is unique enough to show."""
    return thread_id.rsplit(":", 1)[-1][:8]


def _status(t: dict) -> str:
    if t.get("activeRunId") or t.get("activityRunStatus") in ("running", "starting", "preparing"):
        return "running"
    s = t.get("status")
    if s in ("cancelled", "interrupted"):
        return "idle"
    return _STATUS.get(s, "idle")


def normalise(snapshot: dict) -> list[dict]:
    """Thread rows, running first then newest activity first. Archived/deleted threads dropped."""
    projects = {p["id"]: p.get("title") or "No project" for p in snapshot.get("projects", [])}
    rows = []
    for t in snapshot.get("threads", []):
        if t.get("archivedAt") or t.get("deletedAt"):
            continue
        m = t.get("modelSelection") or {}
        rows.append(
            {
                "id": t["id"],
                "short": short_id(t["id"]),
                "title": t.get("title") or "(untitled)",
                "project": projects.get(t.get("projectId"), "No project"),
                "project_id": t.get("projectId"),
                "status": _status(t),
                "last_activity": t.get("updatedAt") or t.get("createdAt"),
                "model": m.get("model") or "",
                "instance": m.get("instanceId") or "",
                "runtime_mode": t.get("runtimeMode") or "",
                "pinned": bool(t.get("pinnedAt")),
            }
        )
    rows.sort(key=lambda r: r["last_activity"] or "", reverse=True)
    rows.sort(key=lambda r: r["status"] != "running")  # stable: running floats to the top
    return rows


_SHELL_TTL = 3.0
_shell_memo: dict[str, tuple[float, dict]] = {}


async def cached_shell(client: T3Client, ttl: float = _SHELL_TTL) -> dict:
    """``_shell`` memoized per base URL for ``ttl`` seconds (the 10 s client poll + page loads share one fetch)."""
    key = client._base  # noqa: SLF001
    hit = _shell_memo.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < ttl:
        return hit[1]
    snap = await _shell(client)
    _shell_memo[key] = (time.monotonic(), snap)
    return snap


def _home_rel(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if home != "~" and path.startswith(home) else path


RECENT_MAX = 5
RECENT_WINDOW_S = 3 * 3600


def group_projects(snapshot: dict, link_index: Optional[dict] = None) -> dict:  # noqa: ARG001
    """``{"seq", "projects": [...]}`` with live (non-archived, non-deleted) thread counts.

    Running projects first, then newest activity; empty projects last.
    """
    rows = normalise(snapshot)
    by_project: dict[Optional[str], list[dict]] = {}
    for r in rows:
        by_project.setdefault(r["project_id"], []).append(r)
    out = []
    for p in snapshot.get("projects", []):
        if p.get("archivedAt") or p.get("deletedAt"):
            continue
        ts = by_project.get(p["id"], [])
        root = p.get("workspaceRoot") or p.get("root") or ""
        out.append(
            {
                "id": p["id"],
                "title": p.get("title") or "No project",
                "root": _home_rel(root),
                "root_name": os.path.basename(root.rstrip("/")) if root else "",
                "thread_count": len(ts),
                "running": sum(1 for r in ts if r["status"] == "running"),
                "last_activity": max((r["last_activity"] or "" for r in ts), default="") or None,
            }
        )
    out.sort(key=lambda x: x["last_activity"] or "", reverse=True)
    out.sort(key=lambda x: x["running"] == 0)
    out.sort(key=lambda x: x["thread_count"] == 0)
    return {"seq": snapshot.get("snapshotSequence"), "projects": out}


def project_threads(
    snapshot: dict, project_id: str, link_index: Optional[dict] = None, limit: int = 20, cursor: Optional[str] = None
) -> dict:
    """One page of a project's threads (running first, newest first) with ``convo_id`` joined from ``link_index``."""
    if not any(p["id"] == project_id for p in snapshot.get("projects", [])):
        raise LookupError(f"T3 project {project_id!r} not found")
    link_index = link_index or {}
    rows = [r for r in normalise(snapshot) if r["project_id"] == project_id]
    try:
        offset = int(cursor[2:]) if cursor and cursor.startswith("o:") else 0
    except ValueError:
        offset = 0
    offset = max(offset, 0)
    page = rows[offset : offset + limit]
    nxt = f"o:{offset + limit}" if offset + limit < len(rows) else None
    return {
        "seq": snapshot.get("snapshotSequence"),
        "threads": [{**r, "convo_id": link_index.get(r["id"])} for r in page],
        "next_cursor": nxt,
    }


def flat_threads(
    snapshot: dict,
    link_index: Optional[dict] = None,
    project_id: Optional[str] = None,
    limit: int = 20,
    cursor: Optional[str] = None,
    q: Optional[str] = None,
) -> dict:
    """One page of threads across all projects (or one): pinned first, then newest activity first.

    ``q`` keeps rows whose title contains it (case-insensitive). ``recent`` is the up-to-5 newest rows that are
    running or active within ``RECENT_WINDOW_S`` (computed over all matching rows, not just this page).

    This is the "In tools" list (WIN-577): no project grouping, ``project`` is just a tag on each row.
    """
    if project_id is not None and not any(p["id"] == project_id for p in snapshot.get("projects", [])):
        raise LookupError(f"T3 project {project_id!r} not found")
    link_index = link_index or {}
    rows = [r for r in normalise(snapshot) if project_id is None or r["project_id"] == project_id]
    if q and q.strip():
        needle = q.strip().lower()
        rows = [r for r in rows if needle in (r["title"] or "").lower()]
    rows.sort(key=lambda r: r["last_activity"] or "", reverse=True)
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=RECENT_WINDOW_S)).strftime("%Y-%m-%dT%H:%M:%S")
    recent = [r for r in rows if r["status"] == "running" or (r["last_activity"] or "") >= cutoff][:RECENT_MAX]
    rows.sort(key=lambda r: not r["pinned"])
    try:
        offset = int(cursor[2:]) if cursor and cursor.startswith("o:") else 0
    except ValueError:
        offset = 0
    offset = max(offset, 0)
    page = rows[offset : offset + limit]
    nxt = f"o:{offset + limit}" if offset + limit < len(rows) else None
    return {
        "seq": snapshot.get("snapshotSequence"),
        "total": len(rows),
        "recent": [{**r, "convo_id": link_index.get(r["id"])} for r in recent],
        "threads": [{**r, "convo_id": link_index.get(r["id"])} for r in page],
        "next_cursor": nxt,
    }


async def fetch_shell(*, client: Optional[T3Client] = None) -> dict:
    """The (3 s memoized) shell snapshot, opening and closing its own client unless one is given."""
    own = client is None
    c = client or T3Client(base_url(), await asyncio.to_thread(ensure_token))
    try:
        return await cached_shell(c)
    finally:
        if own:
            await c.close()


def etag_for(seq: Any) -> str:
    return f'"{seq}"'


def etag_matches(header: Optional[str], seq: Any) -> bool:
    return bool(header) and any(p.strip().removeprefix("W/") == etag_for(seq) for p in header.split(","))


async def list_threads(limit: Optional[int] = None, *, client: Optional[T3Client] = None) -> list[dict]:
    own = client is None
    c = client or T3Client(base_url(), await asyncio.to_thread(ensure_token))
    try:
        rows = normalise(await _shell(c))
    finally:
        if own:
            await c.close()
    return rows[:limit] if limit else rows


async def thread_state(thread_id: str, *, client: Optional[T3Client] = None) -> Optional[str]:
    """``ok`` / ``archived`` / ``deleted`` / ``missing`` from the shell snapshot; ``None`` if T3 can't be asked (WIN-584)."""
    own = client is None
    try:
        c = client or T3Client(base_url(), await asyncio.to_thread(ensure_token))
        try:
            snap = await _shell(c)
        finally:
            if own:
                await c.close()
    except Exception:  # noqa: BLE001 -- down/auth: say nothing rather than call a live thread dead
        return None
    t = next((t for t in snap.get("threads", []) if t["id"] == thread_id), None)
    if t is None:
        return "missing"
    return "deleted" if t.get("deletedAt") else "archived" if t.get("archivedAt") else "ok"


async def recent_messages(thread_id: str, n: int = CONTEXT_MESSAGES, *, client: Optional[T3Client] = None) -> list[dict]:
    """The thread's last ``n`` user/assistant messages as ``{role, text, created_at}``."""
    own = client is None
    c = client or T3Client(base_url(), await asyncio.to_thread(ensure_token))
    try:
        proj: dict[str, Any] = await c.thread_projection(thread_id)
    finally:
        if own:
            await c.close()
    msgs = [
        {"role": m["role"], "text": (m.get("text") or "").strip(), "created_at": m.get("createdAt")}
        for m in proj.get("messages", [])
        if m.get("role") in ("user", "assistant") and (m.get("text") or "").strip()
    ]
    return msgs[-n:]


def resolve_thread(rows: list[dict], ref: str) -> dict:
    """Pick a row by full id, short id / id prefix, or case-insensitive title substring; ambiguity is an error."""
    exact = [r for r in rows if r["id"] == ref]
    hits = (
        exact
        or [r for r in rows if r.get("short", short_id(r["id"])).startswith(ref) or r["id"].startswith(ref)]
        or [r for r in rows if ref.lower() in r["title"].lower()]
    )
    if not hits:
        raise LookupError(f"no T3 thread matches {ref!r}")
    if len(hits) > 1:
        raise LookupError(f"{ref!r} matches {len(hits)} threads; use more of the id: " + ", ".join(h.get("short", short_id(h["id"])) for h in hits[:5]))
    return hits[0]


async def attach_thread(thread_id: str, store=None) -> dict:  # noqa: ANN001
    """Create (or reuse) the Talky convo bound to ``thread_id``; returns ``{convo_id, thread, created}``."""
    from talky.convos import default_store

    store = store or default_store()
    c = T3Client(base_url(), await asyncio.to_thread(ensure_token))
    try:
        rows = normalise(await _shell(c))
        thread = next((r for r in rows if r["id"] == thread_id), None)
        if thread is None:
            raise LookupError(f"T3 thread {thread_id!r} not found")
        existing = store.find_by_link("t3", thread_id)
        if existing is not None:
            await _sync_with(store, existing.id, thread_id, c, thread)
            return {"convo_id": existing.id, "thread": thread, "created": False}
        msgs = await recent_messages(thread_id, client=c)
    finally:
        await c.close()
    rec = store.create(backend="t3", profile="t3", native_session_id=thread_id, title=f"T3 · {thread['title']}")
    for m in msgs:
        store.append_turn(rec.id, m["role"], m["text"])
    return {"convo_id": rec.id, "thread": thread, "created": True, "context_messages": len(msgs)}


def _ts(iso: Optional[str]) -> Optional[float]:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else None
    except ValueError:
        return None


def _missing_tail(stored: list, t3_msgs: list[dict]) -> list[dict]:
    """T3 messages after the newest one matching a recent stored turn (role+text); [] if none match."""
    if not stored:
        return t3_msgs
    for turn in reversed(stored[-4:]):
        for j in range(len(t3_msgs) - 1, -1, -1):
            if t3_msgs[j]["role"] == turn.role and t3_msgs[j]["text"] == turn.content.strip():
                return t3_msgs[j + 1 :]
    return []


def _merge_gaps(stored: list, t3_msgs: list[dict]) -> Optional[list]:
    """The T3 messages from the first stored turn on, reusing each stored turn; ``None`` unless every stored turn is in T3 in order.

    A quick switch abandons an in-flight turn, so the reply can be missing from the MIDDLE of the transcript
    (user turns after it were stored), which ``_missing_tail`` cannot see.
    """
    if not stored:
        return None
    out: list = []
    j = 0
    for m in t3_msgs:
        hit = j < len(stored) and stored[j].role == m["role"] and stored[j].content == m["text"]
        if not out and not hit:
            continue  # T3 history older than what this Chat ever stored
        if hit:
            out.append(stored[j])
            j += 1
        else:
            ts = _ts(m["created_at"])
            out.append(TranscriptTurn(role=m["role"], content=m["text"], timestamp=ts if ts is not None else stored[j - 1].timestamp))
    return out if j == len(stored) else None


async def _sync_with(store, convo_id: str, thread_id: str, c: T3Client, thread: dict) -> int:  # noqa: ANN001
    """Append T3 turns the stored transcript lacks and set ``in_progress`` from T3's run status."""
    msgs = await recent_messages(thread_id, 10_000, client=c)
    stored = store.read_transcript(convo_id)
    merged = _merge_gaps(stored, msgs)
    if merged is not None and len(merged) > len(stored):
        store.replace_transcript(convo_id, merged)
        added = len(merged) - len(stored)
    else:
        tail = _missing_tail(stored, msgs)
        for m in tail:
            store.append_turn(convo_id, m["role"], m["text"], timestamp=_ts(m["created_at"]))
        added = len(tail)
        if not tail:
            added = _append_newer_than_tail(store, convo_id, stored, msgs)
    store.set_in_progress(convo_id, thread["status"] == "running")
    return added


def _append_newer_than_tail(store, convo_id: str, stored: list, t3_msgs: list[dict]) -> int:  # noqa: ANN001
    """Append T3 messages strictly newer than the stored tail (compaction recovery, SWE-743).

    ``_merge_gaps`` / ``_missing_tail`` find the junction between the stored transcript and
    T3 by role+text. T3 compaction rewrites history, so the stored tail is gone from the
    projection and no junction exists — yet messages whose ``created_at`` is newer than the
    last stored turn are continuations of the same thread, not rewrites. Messages without a
    parseable ``created_at`` are left alone.
    """
    if not stored or not t3_msgs:
        return 0
    last_ts = stored[-1].timestamp
    added = 0
    for m in t3_msgs:
        ts = _ts(m.get("created_at"))
        if ts is not None and ts > last_ts:
            store.append_turn(convo_id, m["role"], m["text"], timestamp=ts)
            added += 1
    return added


async def sync_convo(store, convo_id: str) -> int:  # noqa: ANN001
    """Refresh a T3-linked Chat from its thread (T3 is truth, the transcript a cache); idempotent.

    Returns the number of turns appended. T3 being unreachable is logged and leaves the stored data alone.
    """
    from loguru import logger

    rec = store.get(convo_id)
    if rec is None or rec.backend != "t3" or not rec.native_session_id:
        return 0
    thread_id = rec.native_session_id
    try:
        c = T3Client(base_url(), await asyncio.to_thread(ensure_token))
        try:
            thread = next((r for r in normalise(await _shell(c)) if r["id"] == thread_id), None)
            if thread is None:
                return 0
            return await _sync_with(store, convo_id, thread_id, c, thread)
        finally:
            await c.close()
    except Exception as e:  # noqa: BLE001 — T3 down/auth: keep the cache as is
        logger.warning(f"t3 sync {convo_id}: {e}")
        return 0
