"""Fake Hermes + Claude Code session stores for the WIN-582 rig (``drive_switch_backend.py``).

    python3 tests/browser/fake_tools.py DIR   # writes DIR/{hermes/state.db, claude/projects/..., sessions.json}

Start the rig daemon with ``HERMES_HOME=DIR/hermes CLAUDE_CONFIG_DIR=DIR/claude
TALKY_FAKE_SESSIONS=DIR/sessions.json`` (and the ``hermes`` / ``claude-code`` backends set to
``talky.backends.echo.SessionEchoLLMService``), so "In tools" lists these sessions and each fake
backend answers with its own session's marker.
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

HERMES_ID, CLAUDE_ID = "hermes-sess-0001", "claude-sess-0001"
HERMES_MARKER, CLAUDE_MARKER = "MARKER-HERMES-PINEAPPLE", "MARKER-CLAUDE-WALRUS"
HERMES_TITLE, CLAUDE_TITLE = "Hermes old slack pairing chat", "Claude old refactor chat"


def setup(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "hermes").mkdir(exist_ok=True)
    db = d / "hermes" / "state.db"
    db.unlink(missing_ok=True)
    c = sqlite3.connect(db)
    c.executescript(
        """CREATE TABLE sessions(id TEXT PRIMARY KEY, source TEXT, model TEXT, started_at REAL, ended_at REAL,
             title TEXT, hidden INTEGER DEFAULT 0, message_count INTEGER);
           CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT);"""
    )
    now = time.time()
    c.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,0,2)", (HERMES_ID, "slack", "fake", now - 60, now - 30, HERMES_TITLE))
    c.executemany(
        "INSERT INTO messages(session_id, role, content) VALUES (?,?,?)",
        [(HERMES_ID, "user", f"remember the word {HERMES_MARKER}"), (HERMES_ID, "assistant", f"I will remember {HERMES_MARKER}")],
    )
    c.commit()
    c.close()
    proj = d / "claude" / "projects" / "-tmp-fake-proj"
    proj.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "ai-title", "aiTitle": CLAUDE_TITLE},
        {"type": "user", "cwd": "/tmp/fake-proj", "message": {"content": f"remember the word {CLAUDE_MARKER}"}},
        {"type": "assistant", "cwd": "/tmp/fake-proj", "message": {"content": [{"type": "text", "text": f"I will remember {CLAUDE_MARKER}"}]}},
    ]
    (proj / f"{CLAUDE_ID}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    (d / "sessions.json").write_text(json.dumps({HERMES_ID: HERMES_MARKER, CLAUDE_ID: CLAUDE_MARKER}))


if __name__ == "__main__":
    setup(Path(sys.argv[1]).expanduser())
    print("ok")
