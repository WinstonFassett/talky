"""Transcript replay into backends that have no native resume (WIN-534).

A backend opts in by mixing in ``HistoryPreambleMixin`` and calling
``self._consume_preamble(text)`` on the text of the next outgoing user turn.
The stored transcript is prepended once, so the remote model sees the prior
conversation as context on its first turn after a resume.
"""

from __future__ import annotations

from loguru import logger

# Keep the preamble bounded: very long histories get their oldest turns dropped.
MAX_PREAMBLE_CHARS = 12_000


def format_history_preamble(turns: list[dict], max_chars: int = MAX_PREAMBLE_CHARS) -> str:
    lines = [
        f"{t.get('role', 'user')}: {t.get('content', '').strip()}"
        for t in turns or []
        if isinstance(t, dict) and (t.get("content") or "").strip()
    ]
    if not lines:
        return ""
    while len(lines) > 1 and sum(len(line) + 1 for line in lines) > max_chars:
        lines.pop(0)
    return (
        "[Resuming an earlier conversation. Transcript so far:]\n"
        + "\n".join(lines)
        + "\n[End transcript. Continue the conversation from here; the user's new message follows.]\n\n"
    )


class HistoryPreambleMixin:
    _replay_preamble: str = ""

    def set_history(self, turns: list[dict]) -> None:
        self._replay_preamble = format_history_preamble(turns)
        logger.info(
            f"{type(self).__name__}: seeded replay preamble "
            f"({len(turns or [])} turns, {len(self._replay_preamble)} chars)"
        )

    def _consume_preamble(self, text: str) -> str:
        preamble, self._replay_preamble = self._replay_preamble, ""
        return preamble + text if preamble else text
