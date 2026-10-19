"""List and attach to existing T3 Code threads (WIN-564).

Reads the cross-project shell snapshot (read-only) and normalises each thread for
pickers: title, project, status (running / idle / completed / failed), last activity,
model. ``attach_thread`` mints (or reuses) a Talky convo bound to a T3 thread, seeded
with the thread's recent messages so the transcript shows context.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from talky.backends.t3_auth import ensure_token
from talky.backends.t3_client import T3Client

DEFAULT_BASE_URL = "http://127.0.0.1:3773"
CONTEXT_MESSAGES = 12

_STATUS = {"running": "running", "idle": "idle", "completed": "completed", "failed": "failed"}


def base_url() -> str:
    return os.environ.get("T3_BASE_URL") or DEFAULT_BASE_URL


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
                "status": _status(t),
                "last_activity": t.get("updatedAt") or t.get("createdAt"),
                "model": m.get("model") or "",
                "instance": m.get("instanceId") or "",
                "runtime_mode": t.get("runtimeMode") or "",
            }
        )
    rows.sort(key=lambda r: r["last_activity"] or "", reverse=True)
    rows.sort(key=lambda r: r["status"] != "running")  # stable: running floats to the top
    return rows


async def list_threads(limit: Optional[int] = None, *, client: Optional[T3Client] = None) -> list[dict]:
    own = client is None
    c = client or T3Client(base_url(), ensure_token())
    try:
        rows = normalise(await c.shell())
    finally:
        if own:
            await c.close()
    return rows[:limit] if limit else rows


async def recent_messages(thread_id: str, n: int = CONTEXT_MESSAGES, *, client: Optional[T3Client] = None) -> list[dict]:
    """The thread's last ``n`` user/assistant messages as ``{role, text, created_at}``."""
    own = client is None
    c = client or T3Client(base_url(), ensure_token())
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
    c = T3Client(base_url(), ensure_token())
    try:
        rows = normalise(await c.shell())
        thread = next((r for r in rows if r["id"] == thread_id), None)
        if thread is None:
            raise LookupError(f"T3 thread {thread_id!r} not found")
        existing = next(
            (r for r in store.list() if r.backend == "t3" and r.native_session_id == thread_id), None
        )
        if existing is not None:
            return {"convo_id": existing.id, "thread": thread, "created": False}
        msgs = await recent_messages(thread_id, client=c)
    finally:
        await c.close()
    rec = store.create(backend="t3", profile="t3", native_session_id=thread_id, title=f"T3 · {thread['title']}")
    for m in msgs:
        store.append_turn(rec.id, m["role"], m["text"])
    return {"convo_id": rec.id, "thread": thread, "created": True, "context_messages": len(msgs)}
