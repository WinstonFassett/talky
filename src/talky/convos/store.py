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
import shutil
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
    # One native session link per backend the Chat has used (WIN-584), so switching
    # backend and back reconnects to the original session. ``backend`` +
    # ``native_session_id`` stay the active pair; read links through ``links()``.
    native_sessions: dict[str, str] = field(default_factory=dict)
    pinned: bool = False
    archived: bool = False
    # A user turn was received and its reply never completed (daemon died
    # mid-turn). The daemon resumes this turn after a restart (WIN-536).
    in_progress: bool = False

    def links(self) -> dict[str, str]:
        """``backend -> native id`` for every backend this Chat is linked on; the active pair wins."""
        out = dict(self.native_sessions)
        if self.native_session_id:
            out[self.backend] = self.native_session_id
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["native_sessions"] = {k: v for k, v in self.links().items() if k != self.backend}  # parked links only
        return d

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
            native_sessions={str(k): str(v) for k, v in (d.get("native_sessions") or {}).items() if v},
            pinned=bool(d.get("pinned", False)),
            archived=bool(d.get("archived", False)),
            in_progress=bool(d.get("in_progress", False)),
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
        self._ensure_dir(rec.id)
        self._write_meta(rec)
        return rec

    def get(self, convo_id: str) -> Optional[ConvoRecord]:
        if not _is_convo_id(convo_id):
            return None
        meta = self._meta_path(convo_id)
        if not meta.exists():
            return None
        rec = ConvoRecord.from_dict(json.loads(meta.read_text()))
        return rec if rec.id == convo_id else None

    def list(self) -> list[ConvoRecord]:
        """Newest-first list of every convo on disk."""
        if not self.root.exists():
            return []
        out: list[ConvoRecord] = []
        for sub in self.root.iterdir():
            if not sub.is_dir():
                continue
            try:
                rec = self.get(sub.name)
            except Exception:  # noqa: BLE001
                continue
            if rec is not None:
                out.append(rec)
        out.sort(key=lambda r: r.last_active_at, reverse=True)
        return out

    def find_by_link(self, backend: str, native_id: str) -> Optional[ConvoRecord]:
        """The convo bound to ``native_id`` on ``backend`` (newest first), or None."""
        for rec in self.list():
            if rec.links().get(backend) == native_id:
                return rec
        return None

    def link_index(self, backend: str) -> dict[str, str]:
        """``native id -> convo id`` for ``backend``; one ``list()`` pass serves a whole page."""
        out: dict[str, str] = {}
        for rec in self.list():  # newest first: the first convo for an id wins
            nid = rec.links().get(backend)
            if nid:
                out.setdefault(nid, rec.id)
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

    def set_in_progress(self, convo_id: str, value: bool) -> None:
        """Mark that the convo's last turn is (not) awaiting its reply (WIN-536).

        Set when a user turn is received, cleared when its reply completes.
        Survives a daemon crash so the interrupted turn can be resumed.
        """
        rec = self.get(convo_id)
        if rec is None:
            return
        if rec.in_progress == value:
            return
        rec.in_progress = value
        self._write_meta(rec)

    def find_interrupted(self) -> list[ConvoRecord]:
        """Newest-first convos whose last turn never completed (daemon died mid-turn)."""
        return [rec for rec in self.list() if rec.in_progress]

    def set_title(self, convo_id: str, title: str) -> None:
        rec = self.get(convo_id)
        if rec is None:
            return
        rec.title = title
        rec.last_active_at = time.time()
        self._write_meta(rec)

    def update(
        self,
        convo_id: str,
        *,
        title: Optional[str] = None,
        pinned: Optional[bool] = None,
        archived: Optional[bool] = None,
    ) -> Optional[ConvoRecord]:
        """Rename / pin / archive. Leaves last_active_at alone so the list doesn't reorder."""
        rec = self.get(convo_id)
        if rec is None:
            return None
        if title is not None:
            rec.title = " ".join(title.split())[:200]
        if pinned is not None:
            rec.pinned = bool(pinned)
        if archived is not None:
            rec.archived = bool(archived)
        self._write_meta(rec)
        return rec

    def delete(self, convo_id: str) -> bool:
        """Remove a convo's directory; works on a corrupt record too."""
        if not _is_convo_id(convo_id) or not self._dir(convo_id).is_dir():
            return False
        shutil.rmtree(self._dir(convo_id))
        return True

    def search(self, query: str) -> list[tuple[ConvoRecord, Optional[str]]]:
        """Newest-first convos whose title or transcript contains ``query`` (case-insensitive).

        Each hit comes with a short snippet of the first matching turn, or None
        when only the title matched.
        """
        q = " ".join((query or "").split()).lower()
        out: list[tuple[ConvoRecord, Optional[str]]] = []
        for rec in self.list():
            if not q:
                out.append((rec, None))
                continue
            snippet = None
            for turn in self.read_transcript(rec.id):
                text = " ".join(turn.content.split())
                i = text.lower().find(q)
                if i >= 0:
                    snippet = _snippet(text, i, len(q))
                    break
            if snippet is not None or q in rec.title.lower():
                out.append((rec, snippet))
        return out

    def read_transcript_page(
        self, convo_id: str, *, before: Optional[int] = None, limit: int = 200
    ) -> tuple[list[TranscriptTurn], int, int]:
        """The ``limit`` turns just before index ``before`` (default: the end).

        Returns ``(turns, start, total)``; ``start`` is the index of the first
        returned turn, so ``start > 0`` means older turns remain.
        """
        turns = self.read_transcript(convo_id)
        total = len(turns)
        end = total if before is None else max(0, min(before, total))
        start = max(0, end - max(1, limit))
        return turns[start:end], start, total

    def append_turn(self, convo_id: str, role: str, content: str, timestamp: Optional[float] = None) -> None:
        """Append one user or assistant utterance to the transcript."""
        content = (content or "").strip()
        if not content:
            return
        ts = timestamp if timestamp is not None else time.time()
        turn = TranscriptTurn(role=role, content=content, timestamp=ts)
        self._ensure_dir(convo_id)
        fd = os.open(self._transcript_path(convo_id), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")

        # Touch last_active_at + auto-title if still empty.
        rec = self.get(convo_id)
        if rec is None:
            return
        rec.last_active_at = ts
        if not rec.title and role == "user":
            rec.title = _derive_title(content)
        self._write_meta(rec)

    def replace_transcript(self, convo_id: str, turns: list[TranscriptTurn]) -> None:
        """Rewrite the whole transcript atomically (a T3 sync that fills a gap in the middle, WIN-584)."""
        self._ensure_dir(convo_id)
        path = self._transcript_path(convo_id)
        tmp = path.with_suffix(".jsonl.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps(asdict(t), ensure_ascii=False) + "\n")
        os.replace(tmp, path)

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
        if not _is_convo_id(convo_id):
            raise ValueError(f"not a convo id: {convo_id!r}")
        return self.root / convo_id

    def _ensure_dir(self, convo_id: str) -> None:
        # Transcripts are private: owner-only dirs and files.
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._dir(convo_id).mkdir(mode=0o700, exist_ok=True)

    def _meta_path(self, convo_id: str) -> Path:
        return self._dir(convo_id) / "convo.json"

    def _transcript_path(self, convo_id: str) -> Path:
        return self._dir(convo_id) / "transcript.jsonl"

    def _write_meta(self, rec: ConvoRecord) -> None:
        p = self._meta_path(rec.id)
        self._ensure_dir(rec.id)
        tmp = p.with_suffix(".json.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec.to_dict(), ensure_ascii=False, indent=2))
        tmp.replace(p)


def _is_convo_id(value: object) -> bool:
    """Convo ids are uuid4 strings; anything else could escape the store root."""
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _derive_title(first_user_utterance: str, max_len: int = 60) -> str:
    s = " ".join(first_user_utterance.split())
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def _snippet(text: str, at: int, length: int, context: int = 40) -> str:
    start = max(0, at - context)
    end = min(len(text), at + length + context)
    return ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")


def default_store() -> ConvoStore:
    return ConvoStore()
