"""Integration smoke test for OpencodeLLMService.

Spawns a real `opencode serve` on a random port, drives a turn end-to-end
through the backend (no Pipecat pipeline — we collect pushed frames into a
list), and verifies:

1. A text turn streams `TextFrame` deltas and ends with `LLMFullResponseEndFrame`.
2. A bash tool invocation in `ask` permission mode emits a `permissionRequest`
   on the event bus AND a `permission_request` AggregatedTextFrame, then
   `resolve_permission(allow=True)` lets the turn complete.

Skip conditions:
- `opencode` CLI not installed
- TALKY_SKIP_OPENCODE_TESTS=1 in env (for CI without auth tokens)

These tests need real opencode auth (provider login). If `opencode` is
installed but auth is missing, the model call returns session.error and the
text-deltas assertion will fail with a clear message.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pipecat.frames.frames import (  # noqa: E402
    AggregatedTextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
)
from pipecat_mcp_server.talky_turn import UserTurnTextFrame  # noqa: E402
from server.backends.opencode import OpencodeLLMService  # noqa: E402

OPENCODE_BIN = shutil.which("opencode")

pytestmark = [
    pytest.mark.skipif(OPENCODE_BIN is None, reason="opencode CLI not installed"),
    pytest.mark.skipif(
        os.environ.get("TALKY_SKIP_OPENCODE_TESTS") == "1",
        reason="TALKY_SKIP_OPENCODE_TESTS=1",
    ),
    pytest.mark.asyncio,
]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


@pytest.fixture
def opencode_server(tmp_path):
    """A short-lived opencode serve on a random port."""
    port = _free_port()
    assert OPENCODE_BIN is not None  # narrowed by the module-level skipif
    proc = subprocess.Popen(
        [OPENCODE_BIN, "serve", "--port", str(port), "--hostname", "127.0.0.1"],
        cwd=str(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not _wait_port("127.0.0.1", port, 10.0):
        proc.kill()
        pytest.skip("opencode serve failed to start")
    try:
        yield f"http://127.0.0.1:{port}", str(tmp_path)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


class FrameCollector:
    """Stand-in for the next FrameProcessor in the pipeline.

    OpencodeLLMService.push_frame is called from inside the backend; in real
    Pipecat that pushes to the next node. We intercept and collect into a list.
    """

    def __init__(self) -> None:
        self.frames: list = []

    async def __call__(self, frame, direction=None):
        self.frames.append(frame)


@pytest_asyncio.fixture
async def backend(opencode_server, monkeypatch):
    base_url, cwd = opencode_server

    # Disable on-demand extras and pipecat heavy machinery by neutering parents:
    # we don't actually run the pipeline; we just need start() to set up state
    # and process_frame to fire HTTP.
    from pipecat.services.llm_service import LLMService

    async def noop_async(self, *args, **kwargs):
        return None

    monkeypatch.setattr(LLMService, "start", noop_async, raising=True)
    monkeypatch.setattr(LLMService, "stop", noop_async, raising=True)
    monkeypatch.setattr(LLMService, "cancel", noop_async, raising=True)
    # process_frame on the LLMService base does various housekeeping; bypass it
    # so our subclass logic runs without dragging in scheduler bits.
    monkeypatch.setattr(LLMService, "process_frame", noop_async, raising=True)

    collector = FrameCollector()
    svc = OpencodeLLMService(
        base_url=base_url,
        auto_spawn=False,  # already running
        cwd=cwd,
        provider_id="opencode",
        model_id="claude-haiku-4-5",
        permissions=[{"permission": "bash", "pattern": "*", "action": "ask"}],
    )

    # Patch push_frame on the instance to the collector.
    async def push_frame(frame, direction=None):
        await collector(frame, direction)

    svc.push_frame = push_frame  # type: ignore[assignment]
    await svc.start(None)  # type: ignore[arg-type]
    try:
        yield svc, collector
    finally:
        await svc._shutdown()


async def _drive_turn(svc: OpencodeLLMService, collector: FrameCollector, text: str, timeout: float = 60.0):
    """Push a UserTurnTextFrame and wait until LLMFullResponseEndFrame appears."""
    from pipecat.processors.frame_processor import FrameDirection

    await svc.process_frame(UserTurnTextFrame(text=text), FrameDirection.DOWNSTREAM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(isinstance(f, LLMFullResponseEndFrame) for f in collector.frames):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(
        f"turn did not complete in {timeout}s. Frames seen: "
        f"{[type(f).__name__ for f in collector.frames]}"
    )


async def test_basic_text_turn(backend):
    svc, collector = backend
    await _drive_turn(svc, collector, "Reply with exactly the word: pong.")

    starts = [f for f in collector.frames if isinstance(f, LLMFullResponseStartFrame)]
    ends = [f for f in collector.frames if isinstance(f, LLMFullResponseEndFrame)]
    texts = [f.text for f in collector.frames if isinstance(f, TextFrame)]

    assert starts, "expected LLMFullResponseStartFrame"
    assert ends, "expected LLMFullResponseEndFrame"
    assert texts, "expected at least one TextFrame with assistant delta"
    joined = "".join(texts).lower()
    assert "pong" in joined, f"expected 'pong' in reply, got: {joined!r}"


async def test_permission_ask_and_approve(backend):
    """Bash with pattern='*', action='ask' must trigger permission.asked.

    We approve via resolve_permission(allow=True) and check the turn completes.
    """
    svc, collector = backend

    from pipecat.processors.frame_processor import FrameDirection
    from pipecat_mcp_server.event_bus import event_bus

    async with event_bus.subscribe() as q:
        await svc.process_frame(
            UserTurnTextFrame(text="Use the bash tool to run `echo opencode-pong`."),
            FrameDirection.DOWNSTREAM,
        )

        # Drain the queue until we see a permissionRequest event.
        perm_event = None
        deadline = time.time() + 30.0
        while time.time() < deadline:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if ev.type == "permissionRequest":
                perm_event = ev
                break

        assert perm_event is not None, "expected a permissionRequest event"
        assert "tool_name" in perm_event.data
        assert "tool_input" in perm_event.data

        # Approve.
        approved = await svc.resolve_permission(allow=True)
        assert approved, "resolve_permission should report a pending resolved"

        # Wait for the turn to end.
        deadline = time.time() + 60.0
        while time.time() < deadline:
            if any(isinstance(f, LLMFullResponseEndFrame) for f in collector.frames):
                break
            await asyncio.sleep(0.1)

        ends = [f for f in collector.frames if isinstance(f, LLMFullResponseEndFrame)]
        assert ends, "expected turn end after approving permission"

        perm_frames = [
            f
            for f in collector.frames
            if isinstance(f, AggregatedTextFrame) and f.aggregated_by == "permission_request"
        ]
        assert perm_frames, "expected a permission_request AggregatedTextFrame in the pipeline"
