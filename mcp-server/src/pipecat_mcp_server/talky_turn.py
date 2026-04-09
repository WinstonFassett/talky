#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Talky user-turn detector — ticket 76a3.

Drops LLMContext aggregation from the voice pipeline. Talky's use cases
never read the accumulated context history:

- Remote LLM backends (openclaw, moltis) extract only the latest user
  message and send it to a gateway that owns its own session state.
- The MCP-driver passthrough pushes the latest user text onto a queue
  for ``convo_listen`` to read. No history.

So carrying an ``LLMContext`` through the pipeline and emitting
``LLMContextFrame`` every turn is ceremony. This module replaces the
``LLMContextAggregatorPair`` with a minimal shim: reuse pipecat's
``LLMUserAggregator`` for VAD + turn detection + transcription
aggregation (the "we need this" half) but override the emission path to
push a lightweight ``UserTurnTextFrame`` instead of accumulating into
a context.

Pairs with refactors in ``mcp_driver_llm_service.py``,
``server/backends/openclaw.py``, ``server/backends/moltis.py`` to
consume ``UserTurnTextFrame`` instead of ``LLMContextFrame``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from pipecat.frames.frames import DataFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMUserAggregator,
    LLMUserAggregatorParams,
)


@dataclass
class UserTurnTextFrame(DataFrame):
    """A user turn's final transcribed text, emitted on turn-stop.

    This is the minimal replacement for ``LLMContextFrame``: just the
    text and a timestamp, no conversation history, no context object.
    Flows downstream through the pipeline and is consumed by whichever
    LLM service is currently active in the ``LLMSwitcher``.
    """

    text: str = ""
    timestamp: float = 0.0

    def __str__(self) -> str:
        preview = self.text[:60] + ("…" if len(self.text) > 60 else "")
        return f"{self.name}(text={preview!r})"


class TalkyUserTurnDetector(LLMUserAggregator):
    """User-turn detector that emits ``UserTurnTextFrame``.

    Subclasses pipecat's ``LLMUserAggregator`` to reuse its VAD
    controller, user-turn controller, transcription aggregation, mute
    strategies, and idle controller — all the machinery talky needs.
    Overrides only the emission path so the pipeline no longer carries
    an ``LLMContext``.

    The parent's ``_context`` is set to a permanently-empty
    ``LLMContext`` and never mutated. ``LLMContextFrame`` is never
    pushed downstream.
    """

    def __init__(
        self,
        *,
        params: LLMUserAggregatorParams | None = None,
        **kwargs,
    ) -> None:
        """Initialize with a permanently-empty context.

        The parent requires an ``LLMContext``. We give it an empty one
        and never touch it again — ``push_aggregation`` below bypasses
        both ``add_message`` and ``push_context_frame``.
        """
        super().__init__(context=LLMContext([]), params=params, **kwargs)

    async def push_aggregation(self) -> str:
        """Emit the current aggregation as a ``UserTurnTextFrame``.

        Called by ``LLMUserAggregator._maybe_emit_user_turn_stopped``
        when a user turn ends. Parent's version adds the text to
        ``_context`` and pushes an ``LLMContextFrame``. We skip both —
        push a minimal ``UserTurnTextFrame`` instead.
        """
        if not self._aggregation:
            return ""

        text = self.aggregation_string()
        await self.reset()

        await self.push_frame(
            UserTurnTextFrame(text=text, timestamp=time.time())
        )

        return text
