"""WIN-586: TTS must not read link URLs aloud; the transcript keeps full markdown."""

import pytest
from pipecat.frames.frames import (
    AggregatedTextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.tts_service import TTSService
from pipecat.tests.utils import run_test

from talky.shared.spoken_text import strip_links_for_speech, strip_links_transform


@pytest.mark.parametrize(
    "text,spoken",
    [
        ("See [WIN-585](https://example.com/issues/WIN-585) now.", "See WIN-585 now."),
        ("Look ![a diagram](https://example.com/d.png) here", "Look a diagram here"),
        ("Look ![](https://example.com/d.png) here", "Look  here"),
        ("Open <https://example.com/x?a=1> please", "Open link please"),
        ("Go to https://example.com/a/b?c=d. Then stop.", "Go to link. Then stop."),
        ("Plain sentence, no links at all.", "Plain sentence, no links at all."),
        ("**bold** and `code` stay", "**bold** and `code` stay"),
        ("[see [1] notes](https://example.com/n)", "see [1] notes"),
        ("[a](https://x.io/1) and [b](https://x.io/2)", "a and b"),
    ],
)
def test_strip_links_for_speech(text, spoken):
    assert strip_links_for_speech(text) == spoken


class RecordingTTS(TTSService):
    """Fake TTS: records the exact text it is asked to synthesize; emits no audio."""

    def __init__(self, **kw):
        super().__init__(sample_rate=16000, **kw)
        self.spoken: list[str] = []
        self.pushed: list = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.pushed.append(frame)
        await super().push_frame(frame, direction)

    def can_generate_metrics(self) -> bool:
        return False

    async def run_tts(self, text, context_id):
        self.spoken.append(text)
        return
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_pipeline_tts_text_has_no_url_but_transcript_keeps_markdown():
    tts = RecordingTTS()
    tts.add_text_transformer(strip_links_transform, "*")
    # Link split across raw LLM chunks, like a streamed reply.
    chunks = ["Check ", "[WIN-5", "85](https://exa", "mple.com/i/WIN-585) ", "for details."]
    frames = [LLMFullResponseStartFrame()]
    frames += [LLMTextFrame(c) for c in chunks]
    frames += [LLMFullResponseEndFrame()]
    await run_test(tts, frames_to_send=frames)

    assert "".join(tts.spoken).strip() == "Check WIN-585 for details."
    assert all("http" not in s and "](" not in s for s in tts.spoken)
    displayed = "".join(f.text for f in tts.pushed if isinstance(f, AggregatedTextFrame))
    assert "[WIN-585](https://example.com/i/WIN-585)" in displayed


def test_factory_registers_link_transform(monkeypatch):
    from talky.shared import service_factory

    class FakePM:
        def get_voice_backend_config(self, kind, provider):
            return {"x": 1}

    monkeypatch.setattr("talky.shared.profile_manager.get_profile_manager", lambda: FakePM())
    monkeypatch.setattr(
        service_factory, "_create_service_from_backend_config", lambda *a, **k: RecordingTTS()
    )
    svc = service_factory.create_tts_service_from_config("fake")
    assert ("*", strip_links_transform) in svc._text_transforms
