"""Saved sessions of the tools Talky can drive directly: Claude Code and Hermes ("In tools" tabs).

Neither optional SDK is imported: Claude Code sessions are read from ``~/.claude/projects/<cwd>/<id>.jsonl``
and Hermes sessions from its SQLite ``state.db`` (opened read-only). Attaching creates (or reuses) a Talky
Chat whose ``native_session_id`` is the tool's session id, so the next turn resumes that session via the
backend's own ``resume=`` (``claude_code.py`` / ``hermes.py``).
"""

from __future__ import annotations

import json
import os
import pwd
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

CONTEXT_MESSAGES = 12
LIMIT = 60

# tool id -> (convo backend, talky profile, row label)
TOOLS = {
    "claude": ("claude-code", "claude", "Claude Code"),
    "hermes": ("hermes", "hermes", "Hermes"),
}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _home() -> Path:
    """The login user's home (not ``$HOME``, which isolated daemons override)."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _claude_root() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or _home() / ".claude") / "projects"


def _hermes_db() -> Path:
    return Path(os.environ.get("HERMES_HOME") or _home() / ".hermes") / "state.db"


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _is_noise(text: str) -> bool:
    t = text.lstrip()
    return not t or t.startswith("<") or t.startswith("Caveat:")


def _claude_turns(path: Path, limit: Optional[int] = None) -> tuple[list[dict], Optional[str]]:
    """``(turns, cwd)`` of one session file: real user/assistant text only (no tool results)."""
    turns: list[dict] = []
    cwd = None
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                cwd = cwd or d.get("cwd")
                if d.get("type") not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta"):
                    continue
                text = _text_of((d.get("message") or {}).get("content")).strip()
                if _is_noise(text):
                    continue
                turns.append({"role": d["type"], "text": text})
                if limit and len(turns) >= limit and limit == 1:
                    break
    except OSError:
        pass
    return turns, cwd


def _list_claude(limit: int) -> list[dict]:
    files = [p for p in _claude_root().glob("*/*.jsonl") if not p.name.startswith("agent-")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    rows = []
    for p in files:
        if len(rows) >= limit:
            break
        turns, cwd = _claude_turns(p, 1)
        if not turns:
            continue
        title = turns[0]["text"].replace("\n", " ")[:90]
        project = Path(cwd).name if cwd else p.parent.name
        rows.append({"id": p.stem, "title": title, "project": project, "project_id": project,
                     "last_activity": _iso(p.stat().st_mtime), "model": ""})
    return rows


def _hermes_conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{_hermes_db()}?mode=ro", uri=True)


def _list_hermes(limit: int) -> list[dict]:
    if not _hermes_db().exists():
        return []
    c = _hermes_conn()
    try:
        q = (
            "SELECT s.id, s.source, s.model, COALESCE(s.ended_at, s.started_at), "
            "(SELECT content FROM messages m WHERE m.session_id = s.id AND m.role = 'user' "
            " AND m.content IS NOT NULL AND m.content != '' ORDER BY m.id LIMIT 1) "
            "FROM sessions s WHERE s.message_count > 0 ORDER BY 4 DESC LIMIT ?"
        )
        rows = []
        for sid, source, model, ts, first in c.execute(q, (limit,)):
            if not first:
                continue
            rows.append({"id": sid, "title": first.replace("\n", " ")[:90], "project": source or "hermes",
                         "project_id": source or "hermes", "last_activity": _iso(ts), "model": model or ""})
        return rows
    finally:
        c.close()


def list_sessions(tool: str, store=None, limit: int = LIMIT) -> list[dict]:  # noqa: ANN001
    """Newest-first rows shaped like the T3 thread rows the sidebar already renders."""
    if tool not in TOOLS:
        raise LookupError(f"unknown tool {tool!r}")
    from talky.convos import default_store

    store = store or default_store()
    rows = _list_claude(limit) if tool == "claude" else _list_hermes(limit)
    links = store.link_index(TOOLS[tool][0])
    for r in rows:
        r.update(short=r["id"][:8], status="idle", pinned=False, convo_id=links.get(r["id"]))
    return rows


def _recent(tool: str, session_id: str) -> tuple[list[dict], Optional[str]]:
    if tool == "claude":
        for p in _claude_root().glob(f"*/{session_id}.jsonl"):
            turns, cwd = _claude_turns(p)
            return turns[-CONTEXT_MESSAGES:], cwd
        raise LookupError(f"Claude session {session_id!r} not found")
    c = _hermes_conn()
    try:
        if not c.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone():
            raise LookupError(f"Hermes session {session_id!r} not found")
        cur = c.execute(
            "SELECT role, content FROM messages WHERE session_id = ? AND role IN ('user','assistant') "
            "AND content IS NOT NULL AND content != '' ORDER BY id DESC LIMIT ?",
            (session_id, CONTEXT_MESSAGES),
        )
        return [{"role": r, "text": t} for r, t in reversed(cur.fetchall())], None
    finally:
        c.close()


def attach_session(tool: str, session_id: str, store=None) -> dict:  # noqa: ANN001
    """Create (or reuse) the Talky Chat linked to the tool's session; returns ``{convo_id, created}``."""
    if tool not in TOOLS:
        raise LookupError(f"unknown tool {tool!r}")
    from talky.convos import default_store

    store = store or default_store()
    backend, profile, label = TOOLS[tool]
    existing = store.find_by_link(backend, session_id)
    if existing is not None:
        return {"convo_id": existing.id, "created": False}
    msgs, _cwd = _recent(tool, session_id)
    first = next((m["text"] for m in msgs if m["role"] == "user"), session_id[:8])
    rec = store.create(backend=backend, profile=profile, native_session_id=session_id,
                       title=f"{label} · {first.replace(chr(10), ' ')[:60]}")
    for m in msgs:
        store.append_turn(rec.id, m["role"], m["text"])
    return {"convo_id": rec.id, "created": True, "context_messages": len(msgs)}
