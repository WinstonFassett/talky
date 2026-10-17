"""Talky-owned conversation records (WIN-534).

A Talky conversation is a backend-agnostic record of one voice session:
profile, backend, optional native backend session id, timestamps, title,
and a transcript. It survives daemon restart and lives at
``~/.talky/convos/<talky-id>/`` so ``talky convos`` can list, and later
``talky resume <id>`` can reopen (slice 2) or replay into any backend
(slice 3).

Pipecat already ships ``UserTranscriptProcessor`` /
``AssistantTranscriptProcessor`` that emit ``TranscriptionMessage``s on an
``on_transcript_update`` event. Talky uses its own ``UserTurnTextFrame``
(not pipecat's ``TranscriptionFrame``), so we provide a thin user-side
processor; the assistant-side processor is pipecat's directly. The
cross-process persistence layer — the part pipecat does NOT provide — is
``ConvoStore``.
"""

from talky.convos.store import ConvoRecord, ConvoStore, default_store

__all__ = ["ConvoRecord", "ConvoStore", "default_store"]
