"""One call's link to its convo record (WIN-534).

The record is created lazily on the first real turn, so connects that never
say anything leave nothing on disk. When ``resume_into`` names an existing
record, turns append to that record instead of minting a new one.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from loguru import logger

from talky.convos.store import ConvoRecord, ConvoStore

NativeIdReader = Callable[[], Optional[str]]
# (convo_id, reason, status): reason is "created" | "turn" | "status".
ChangeListener = Callable[[str, str, str], None]

NATIVE_ID_RECHECK_SECS = 2.0


class ConvoSession:
    def __init__(
        self,
        store: ConvoStore,
        *,
        enabled: bool = True,
        backend: str,
        profile: str,
        resume_into: Optional[str] = None,
        native_id_reader: Optional[NativeIdReader] = None,
        on_change: Optional[ChangeListener] = None,
    ) -> None:
        self._store = store
        self.enabled = enabled
        self.backend = backend
        self.profile = profile
        self.resume_into = resume_into
        self._native_id_reader = native_id_reader
        self.record: Optional[ConvoRecord] = None
        self._ignored: list[str] = []
        self._recheck: Optional[asyncio.TimerHandle] = None
        self._on_change = on_change
        # "idle" | "working" (user spoke, reply pending) | "failed" (backend error).
        self.status = "idle"

    def ignore_assistant(self, text: str) -> None:
        """Don't record this exact bot utterance once (system cues like "Claude here.")."""
        self._ignored.append(" ".join(text.split()))

    @property
    def convo_id(self) -> Optional[str]:
        return self.record.id if self.record else self.resume_into

    def set_active(self, *, backend: str, profile: str, native_id_reader: Optional[NativeIdReader]) -> None:
        self.backend = backend
        self.profile = profile
        self._native_id_reader = native_id_reader
        if self.record is not None:
            self._sync_meta()

    def start_fresh(self) -> None:
        """Next turn opens a new record."""
        self.shutdown()
        self.resume_into = None
        self.record = None
        self.status = "idle"

    def bind(self, convo_id: str) -> None:
        """Point this call at an existing record (live resume)."""
        self.resume_into = convo_id
        self.record = None
        self.status = "idle"

    def append(self, role: str, content: str, timestamp: Optional[float] = None) -> None:
        if not self.enabled or not (content or "").strip():
            return
        normalized = " ".join(content.split())
        if role == "user":
            self._ignored.clear()  # an announcement that never played back doesn't linger
        elif normalized in self._ignored:
            self._ignored.remove(normalized)
            return
        try:
            created = self.record is None and not self._resumes_existing()
            if self.record is None:
                self._open()
            assert self.record is not None
            self._store.append_turn(self.record.id, role, content, timestamp=timestamp)
            self._sync_meta()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ConvoSession: failed to persist {role} turn: {e}")
            return
        self.status = "working" if role == "user" else "idle"
        self._notify("created" if created else "turn")
        if role == "assistant":
            # Backends like claude report their session id just after the reply
            # finishes; re-read shortly so a daemon kill right after still has it.
            if self._recheck is not None:
                self._recheck.cancel()
            try:
                self._recheck = asyncio.get_running_loop().call_later(NATIVE_ID_RECHECK_SECS, self.close)
            except RuntimeError:
                self._recheck = None

    def set_failed(self) -> None:
        """The backend errored mid-turn; the list shows the row as failed until the next turn."""
        self.status = "failed"
        self._notify("status")

    def _notify(self, reason: str) -> None:
        cid = self.convo_id
        if self._on_change is None or not cid:
            return
        try:
            self._on_change(cid, reason, self.status)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"ConvoSession: change listener failed: {e}")

    def _resumes_existing(self) -> bool:
        return bool(self.resume_into) and self._store.get(self.resume_into) is not None

    def close(self) -> None:
        """Final metadata sync — some backends report their session id after the last reply."""
        if self.record is not None:
            try:
                self._sync_meta()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"ConvoSession.close: {e}")

    def shutdown(self) -> None:
        """Call teardown: final sync, and no timer may touch the record afterwards."""
        if self._recheck is not None:
            self._recheck.cancel()
            self._recheck = None
        self.close()

    def _open(self) -> None:
        existing = self._store.get(self.resume_into) if self.resume_into else None
        if existing is not None:
            self.record = existing
            logger.info(f"ConvoSession: continuing convo {existing.id} on {self.backend}")
        else:
            self.record = self._store.create(backend=self.backend, profile=self.profile)
            logger.info(f"ConvoSession: opened convo {self.record.id} (backend={self.backend})")

    def _sync_meta(self) -> None:
        rec = self._store.get(self.record.id) if self.record else None
        if rec is None:
            return
        changed = False
        nsid = self._read_native_id()
        if (rec.backend, rec.profile) != (self.backend, self.profile):
            rec.backend, rec.profile = self.backend, self.profile
            # A session id only means something to the backend that issued it.
            rec.native_session_id = nsid
            changed = True
        elif nsid and nsid != rec.native_session_id:
            rec.native_session_id = nsid
            changed = True
        if changed:
            self._store.save(rec)
        self.record = rec

    def _read_native_id(self) -> Optional[str]:
        if self._native_id_reader is None:
            return None
        try:
            return self._native_id_reader()
        except Exception:  # noqa: BLE001
            return None


def native_session_id_of(service: Any) -> Optional[str]:
    """Best-effort read of a backend's own session id; names vary per backend."""
    if service is None:
        return None
    for name in ("session_id", "_session_id", "_last_session_id"):
        try:
            val = getattr(service, name, None)
        except Exception:  # noqa: BLE001
            val = None
        if val and isinstance(val, str):
            return val
    return None
