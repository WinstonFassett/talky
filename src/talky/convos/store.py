"""ConvoStore — Talky's cross-process conversation persistence (WIN-534, slice 1).

Layout under ``~/.talky/convos/<talky-id>/``:
  - ``convo.json``       metadata: id, backend, profile, native_session_id,
                         started_at, last_active_at, title
  - ``transcript.jsonl`` append-only one JSON object per utterance:
                         {role, content, timestamp}

Why both files: metadata is small and rewritten whenever any field changes;
the transcript is append-only so a crash mid-session leaves what we captured
intact. Both are plain text so an operator can tail them.

This module has NO pipecat dependency — it is pure persistence. The pipeline
wiring lives in ``talky.convos.transcript_sink``.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, Optional


def default_root() -> Path:
    """Where convo records live. Env override for tests."""
    env = os.environ.get("TALKY_CONVOS_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".talky" / "convos"


@dataclass
class TranscriptTurn:
    role: str  # "user" | "assistant"
    content: str
    timestamp: float  # unix seconds


@dataclass
class ConvoRecord:
    id: str
    backend: str
    profile: str
    started_at: float
    last_active_at: float
    title: str = ""
    native_session_id: Optional[str] = None  # backend's own session id, when it has one

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ConvoRecord":
        return cls(
            id=d["id"],
            backend=d["backend"],
            profile=d["profile"],
            started_at=float(d["started_at"]),
            last_active_at=float(d["last_active_at"]),
            title=d.get("title", "") or "",
            native_session_id=d.get("native_session_id") or None,
        )


class ConvoStore:
    """Reads and writes convo records on disk.

    One instance per process is fine; it holds no mutable state beyond the
    root path. Operations touch disk directly so they are safe across process
    boundaries (daemon restart, CLI reading what the daemon wrote).
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = (root or default_root()).expanduser()

    # --- creation / lookup -------------------------------------------------

    def create(
        self,
        *,
        backend: str,
        profile: str,
        native_session_id: Optional[str] = None,
        title: str = "",
        convo_id: Optional[str] = None,
    ) -> ConvoRecord:
        """Mint a new convo record and persist its metadata."""
        now = time.time()
        rec = ConvoRecord(
            id=convo_id or str(uuid.uuid4()),
            backend=backend,
            profile=profile,
            started_at=now,
            last_active_at=now,
            title=title,
            native_session_id=native_session_id,
        )
        self._dir(rec.id).mkdir(parents=True, exist_ok=True)
        self._write_meta(rec)
        return rec

    def get(self, convo_id: str) -> Optional[ConvoRecord]:
        meta = self._meta_path(convo_id)
        if not meta.exists():
            return None
        return ConvoRecord.from_dict(json.loads(meta.read_text()))

    def list(self) -> list[ConvoRecord]:
        """Newest-first list of every convo on disk."""
        if not self.root.exists():
            return []
        out: list[ConvoRecord] = []
        for sub in self.root.iterdir():
            if not sub.is_dir():
                continue
            meta = sub / "convo.json"
            if not meta.exists():
                continue
            try:
                out.append(ConvoRecord.from_dict(json.loads(meta.read_text())))
            except Exception:  # noqa: BLE001
                continue
        out.sort(key=lambda r: r.last_active_at, reverse=True)
        return out

    # --- updates -----------------------------------------------------------

    def save(self, rec: ConvoRecord) -> None:
        self._write_meta(rec)

    def set_native_session_id(self, convo_id: str, native_session_id: str) -> None:
        rec = self.get(convo_id)
        if rec is None:
            return
        rec.native_session_id = native_session_id
        rec.last_active_at = time.time()
        self._write_meta(rec)

    def set_title(self, convo_id: str, title: str) -> None:
        rec = self.get(convo_id)
        if rec is None:
            return
        rec.title = title
        rec.last_active_at = time.time()
        self._write_meta(rec)

    def append_turn(self, convo_id: str, role: str, content: str, timestamp: Optional[float] = None) -> None:
        """Append one user or assistant utterance to the transcript."""
        content = (content or "").strip()
        if not content:
            return
        ts = timestamp if timestamp is not None else time.time()
        turn = TranscriptTurn(role=role, content=content, timestamp=ts)
        self._dir(convo_id).mkdir(parents=True, exist_ok=True)
        with self._transcript_path(convo_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")

        # Touch last_active_at + auto-title if still empty.
        rec = self.get(convo_id)
        if rec is None:
            return
        rec.last_active_at = ts
        if not rec.title and role == "user":
            rec.title = _derive_title(content)
        self._write_meta(rec)

    def read_transcript(self, convo_id: str) -> list[TranscriptTurn]:
        p = self._transcript_path(convo_id)
        if not p.exists():
            return []
        out: list[TranscriptTurn] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                out.append(TranscriptTurn(role=d["role"], content=d["content"], timestamp=float(d["timestamp"])))
            except Exception:  # noqa: BLE001
                continue
        return out

    def iter_transcript(self, convo_id: str) -> Iterator[TranscriptTurn]:
        return iter(self.read_transcript(convo_id))

    # --- paths -------------------------------------------------------------

    def _dir(self, convo_id: str) -> Path:
        return self.root / convo_id

    def _meta_path(self, convo_id: str) -> Path:
        return self._dir(convo_id) / "convo.json"

    def _transcript_path(self, convo_id: str) -> Path:
        return self._dir(convo_id) / "transcript.jsonl"

    def _write_meta(self, rec: ConvoRecord) -> None:
        p = self._meta_path(rec.id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)


def _derive_title(first_user_utterance: str, max_len: int = 60) -> str:
    s = " ".join(first_user_utterance.split())
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def default_store() -> ConvoStore:
    return ConvoStore()
