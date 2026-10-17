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

NATIVE_ID_RECHECK_SECS = 2.0


class ConvoSession:
    def __init__(
        self,
        store: ConvoStore,
        *,
        backend: str,
        profile: str,
        resume_into: Optional[str] = None,
        native_id_reader: Optional[NativeIdReader] = None,
    ) -> None:
        self._store = store
        self.backend = backend
        self.profile = profile
        self.resume_into = resume_into
        self._native_id_reader = native_id_reader
        self.record: Optional[ConvoRecord] = None
        self._ignored: list[str] = []

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

    def bind(self, convo_id: str) -> None:
        """Point this call at an existing record (live resume)."""
        self.resume_into = convo_id
        self.record = None

    def append(self, role: str, content: str, timestamp: Optional[float] = None) -> None:
        if not (content or "").strip():
            return
        normalized = " ".join(content.split())
        if role == "assistant" and normalized in self._ignored:
            self._ignored.remove(normalized)
            return
        try:
            if self.record is None:
                self._open()
            assert self.record is not None
            self._store.append_turn(self.record.id, role, content, timestamp=timestamp)
            self._sync_meta()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ConvoSession: failed to persist {role} turn: {e}")
            return
        if role == "assistant":
            # Backends like claude report their session id just after the reply
            # finishes; re-read shortly so a daemon kill right after still has it.
            try:
                asyncio.get_running_loop().call_later(NATIVE_ID_RECHECK_SECS, self.close)
            except RuntimeError:
                pass

    def close(self) -> None:
        """Final metadata sync — some backends report their session id after the last reply."""
        if self.record is not None:
            try:
                self._sync_meta()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"ConvoSession.close: {e}")

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
        if (rec.backend, rec.profile) != (self.backend, self.profile):
            rec.backend, rec.profile = self.backend, self.profile
            changed = True
        nsid = self._read_native_id()
        if nsid and nsid != rec.native_session_id:
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
