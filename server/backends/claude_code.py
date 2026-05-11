"""Claude Code backend for Talky — wraps the Agent SDK via a thread bridge.

The Agent SDK (claude-code-sdk) uses anyio task groups internally. Pipecat
dispatches process_frame across different asyncio task contexts. Mixing these
causes receive_response() to deadlock (anyio memory channel cross-task issue).

Fix: run the SDK in a dedicated OS thread. asyncio.Queue + run_coroutine_threadsafe
bridge frames between the SDK thread and Pipecat's event loop.

Interrupt semantics:
  - Pipecat VAD fires → InterruptionFrame → bridge.interrupt() sets a threading.Event
  - SDK thread checks the event between text_delta iterations, breaks out of async for
  - TTS stops in ~200ms (Pipecat barge-in); LLM abort follows async (acceptable)
  - Next UserTurnTextFrame is sent as a new prompt to the SDK thread

Limitation: Claude has no `--mode rpc` equivalent. Interrupt is turn-boundary only,
not mid-token. Pi's RPC abort is still the gold standard; this is the best available
for Claude.

Session resume: uses continue_conversation=True (last session) rather than explicit
session_id to avoid the $1+/turn uncached token replay cost.
"""

import asyncio
import queue
import threading
from typing import Optional

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    StartFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat_mcp_server.talky_turn import UserTurnTextFrame

try:
    import anyio
    from claude_code_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
        ClaudeCodeOptions,
        query,
    )
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False


# Frame tuple sentinels passed through the asyncio queue
_TURN_START = ("start",)
_TURN_END = ("end",)


class _ClaudeSDKThread:
    """Runs the Claude Agent SDK in a dedicated anyio thread.

    Communication:
    - prompt_queue (threading.Queue): main thread → SDK thread (str prompts or None sentinel)
    - frame_queue (asyncio.Queue): SDK thread → Pipecat event loop (frame tuples)
    - interrupt_event (threading.Event): main thread → SDK thread (abort current turn)
    """

    def __init__(self, options: "ClaudeCodeOptions", loop: asyncio.AbstractEventLoop, frame_queue: asyncio.Queue):
        self._options = options
        self._loop = loop
        self._frame_queue = frame_queue
        self._prompt_queue: queue.Queue = queue.Queue()
        self._interrupt_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_session_id: Optional[str] = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="claude-sdk")
        self._thread.start()

    def send_prompt(self, text: str):
        self._interrupt_event.clear()
        self._prompt_queue.put(text)

    def interrupt(self):
        """Signal current turn to abort. Clears stale prompts."""
        self._interrupt_event.set()
        # Drain any queued prompts that arrived before the interrupt
        while True:
            try:
                self._prompt_queue.get_nowait()
            except queue.Empty:
                break

    def stop(self):
        self._prompt_queue.put(None)  # sentinel
        if self._thread:
            self._thread.join(timeout=5)

    def _put_frame(self, item: tuple):
        """Thread-safe: schedule frame_queue.put on the asyncio event loop."""
        asyncio.run_coroutine_threadsafe(self._frame_queue.put(item), self._loop)

    def _run(self):
        """SDK thread entry — runs anyio event loop."""
        anyio.run(self._async_run)

    async def _async_run(self):
        """Main SDK loop: block on prompt_queue, run query, push frames."""
        while True:
            # Block until a prompt arrives (poll every 100ms so interrupt can drain queue)
            prompt = None
            while prompt is None:
                try:
                    prompt = self._prompt_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

            if prompt is None:  # stop sentinel
                break

            self._interrupt_event.clear()
            self._put_frame(_TURN_START)

            try:
                async for msg in query(prompt, options=self._options):
                    if self._interrupt_event.is_set():
                        logger.info("Claude SDK: interrupt detected, breaking")
                        break
                    self._route_message(msg)
            except Exception as e:
                logger.error(f"Claude SDK error: {e}", exc_info=True)
                self._put_frame(("error", str(e)))
            finally:
                self._put_frame(_TURN_END)

    def _route_message(self, msg):
        if not _SDK_AVAILABLE:
            return
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text:
                    self._put_frame(("text", block.text))
                elif isinstance(block, ToolUseBlock):
                    self._put_frame(("tool_start", block.name))
        elif isinstance(msg, ResultMessage):
            if msg.session_id:
                self._last_session_id = msg.session_id


class ClaudeCodeLLMService(LLMService):
    """Claude Code backend via Agent SDK thread bridge.

    Profile name: claude-code
    Switch via: talky claude-code
    """

    def __init__(
        self,
        *,
        cwd: Optional[str] = None,
        model: Optional[str] = None,
        permission_mode: str = "acceptEdits",
        max_turns: int = 10,
        **kwargs,
    ):
        super().__init__(**kwargs)
        if not _SDK_AVAILABLE:
            logger.warning(
                "claude-code-sdk not installed — ClaudeCodeLLMService will not function. "
                "Install with: uv add claude-code-sdk"
            )
        self._cwd = cwd
        self._model = model
        self._permission_mode = permission_mode
        self._max_turns = max_turns
        self._bridge: Optional[_ClaudeSDKThread] = None
        self._frame_queue: asyncio.Queue = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None

    def _build_options(self) -> "ClaudeCodeOptions":
        opts = ClaudeCodeOptions(
            permission_mode=self._permission_mode,
            max_turns=self._max_turns,
            include_partial_messages=True,  # enables text_delta streaming
            continue_conversation=True,     # resume last session without re-tokenizing
        )
        if self._model:
            opts.model = self._model
        if self._cwd:
            opts.cwd = self._cwd
        return opts

    async def start(self, frame: StartFrame):
        await super().start(frame)
        if not _SDK_AVAILABLE:
            return
        loop = asyncio.get_running_loop()
        self._frame_queue = asyncio.Queue()
        self._bridge = _ClaudeSDKThread(self._build_options(), loop, self._frame_queue)
        self._bridge.start()
        self._reader_task = asyncio.create_task(self._drain_frames())
        logger.info("ClaudeCodeLLMService started (thread bridge)")

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._shutdown()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._shutdown()

    async def _shutdown(self):
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        if self._bridge:
            self._bridge.stop()
            self._bridge = None

    async def _drain_frames(self):
        """Read frame tuples from the queue and push to Pipecat."""
        while True:
            item = await self._frame_queue.get()
            kind = item[0]
            if kind == "start":
                await self.push_frame(LLMFullResponseStartFrame())
            elif kind == "text":
                await self.push_frame(TextFrame(item[1]))
            elif kind == "tool_start":
                # Brief status without being too chatty
                logger.info(f"Claude tool: {item[1]}")
            elif kind == "end":
                await self.push_frame(LLMFullResponseEndFrame())
            elif kind == "error":
                logger.error(f"Claude SDK error: {item[1]}")
                await self.push_frame(LLMFullResponseEndFrame())

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            if self._bridge:
                self._bridge.interrupt()
                # Flush stale frames (partial response being buffered)
                while not self._frame_queue.empty():
                    try:
                        self._frame_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            if not self._bridge:
                logger.warning("Claude bridge not started — ignoring turn")
                return
            logger.info(f"Claude ← user: {frame.text[:80]}...")
            self._bridge.send_prompt(frame.text)
            return

        await self.push_frame(frame, direction)
