"""Talky backends — Pipecat-compatible adapters for agents, models, and receivers.

See UBIQUITOUS_LANGUAGE.md for the meaning of Backend, Backend Adapter,
and BackendStatus.
"""

from enum import Enum


class BackendStatus(str, Enum):
    """Config/install state of a Backend, reported by ``BackendAdapter.status()``.

    Orthogonal to runtime health (Health Probe) — Status answers
    "is this configured and installed?", not "is the endpoint reachable
    right now?". See UBIQUITOUS_LANGUAGE.md.

    Today's ``status()`` returns one of: Ready, Installable, Misconfigured,
    Blocked. Unknown / Installing / InstallFailed / Running are vocabulary
    used by the picker and surrounding orchestration; they are not values
    a Backend Adapter returns from ``status()``.
    """

    READY = "ready"
    INSTALLABLE = "installable"
    MISCONFIGURED = "misconfigured"
    BLOCKED = "blocked"


__all__ = ["BackendStatus"]
