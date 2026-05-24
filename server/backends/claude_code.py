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

Permission prompts: when permission_mode="default", can_use_tool callback is wired.
The SDK thread blocks on a threading.Event; the asyncio side emits an SSE event so
the chat UI can surface an Allow/Deny banner. resolve_permission(allow) unblocks it.
Timeout: 120s → auto-deny.
"""

import asyncio
import queue
import threading
from typing import Any, Optional

from loguru import logger
from pipecat.frames.frames import (
    AggregatedTextFrame,
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
        ThinkingBlock,
        ToolUseBlock,
        ClaudeCodeOptions,
        query,
    )
    from claude_code_sdk._errors import MessageParseError
    from claude_code_sdk.types import (
        PermissionResultAllow,
        PermissionResultDeny,
        ToolPermissionContext,
    )
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False

    class MessageParseError(Exception):  # type: ignore[no-redef]
        pass


# Frame tuple sentinels passed through the asyncio queue
_TURN_START = ("start",)
_TURN_END = ("end",)

# Seconds to wait for user grant/deny before auto-denying.
_PERMISSION_TIMEOUT = 120


class _ClaudeSDKThread:
    """Runs the Claude Agent SDK in a dedicated anyio thread.

    Communication:
    - prompt_queue (threading.Queue): main thread → SDK thread (str prompts or None sentinel)
    - frame_queue (asyncio.Queue): SDK thread → Pipecat event loop (frame tuples)
    - interrupt_event (threading.Event): main thread → SDK thread (abort current turn)
    - _perm_event / _perm_allow: permission handshake (SDK thread blocks; asyncio side resolves)
    """

    def __init__(self, options: "ClaudeCodeOptions", loop: asyncio.AbstractEventLoop, frame_queue: asyncio.Queue):
        self._options = options
        self._loop = loop
        self._frame_queue = frame_queue
        self._prompt_queue: queue.Queue = queue.Queue()
        self._interrupt_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_session_id: Optional[str] = None

        # Permission handshake state (guarded by _perm_lock)
        self._perm_lock = threading.Lock()
        self._perm_event: Optional[threading.Event] = None
        self._perm_allow: bool = False

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
        # Also unblock any pending permission with a deny so the turn ends cleanly.
        self.resolve_permission(allow=False)

    def stop(self):
        self._prompt_queue.put(None)  # sentinel
        self.resolve_permission(allow=False)  # unblock any pending permission
        if self._thread:
            self._thread.join(timeout=5)

    def resolve_permission(self, *, allow: bool) -> bool:
        """Called from the asyncio side to grant or deny a pending permission.

        Returns True if there was a pending permission to resolve, False otherwise.
        """
        with self._perm_lock:
            if self._perm_event is None:
                return False
            self._perm_allow = allow
            self._perm_event.set()
            return True

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
                aiter = query(prompt=self._make_prompt(prompt), options=self._options)
                while True:
                    if self._interrupt_event.is_set():
                        logger.info("Claude SDK: interrupt detected, breaking")
                        break
                    try:
                        msg = await aiter.__anext__()
                    except StopAsyncIteration:
                        break
                    except MessageParseError as e:  # type: ignore[possibly-unbound]
                        if "Unknown message type" in str(e):
                            logger.debug(f"Claude SDK: skipping unknown message type: {e}")
                            continue
                        logger.error(f"Claude SDK parse error: {e}", exc_info=True)
                        self._put_frame(("error", str(e)))
                        break
                    self._route_message(msg)
            except Exception as e:
                logger.error(f"Claude SDK error: {e}", exc_info=True)
                self._put_frame(("error", str(e)))
            finally:
                self._put_frame(_TURN_END)

    def _make_prompt(self, text: str):
        """Return the prompt in the form the SDK requires.

        When can_use_tool is set the SDK requires an AsyncIterable[dict] (streaming
        mode). We always use that form so the code path is consistent regardless of
        whether the callback is wired.
        """
        async def _gen():
            yield {
                "type": "user",
                "message": {"role": "user", "content": text},
                "parent_tool_use_id": None,
                "session_id": None,
            }
        return _gen()

    def _route_message(self, msg):
        if not _SDK_AVAILABLE:
            return
        if isinstance(msg, AssistantMessage):  # type: ignore[possibly-unbound]
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text:
                    self._put_frame(("text", block.text))
                elif isinstance(block, ThinkingBlock) and block.thinking:
                    self._put_frame(("thinking", block.thinking))
                elif isinstance(block, ToolUseBlock):
                    self._put_frame(("tool_start", block.name))
        elif isinstance(msg, ResultMessage):
            if msg.session_id:
                self._last_session_id = msg.session_id

    def _permission_callback_sync(self, tool_name: str, tool_input: dict[str, Any]) -> bool:
        """Block the SDK thread until asyncio side resolves the permission. Returns True=allow."""
        evt = threading.Event()
        with self._perm_lock:
            self._perm_event = evt
            self._perm_allow = False

        # Signal the asyncio side to surface the prompt.
        self._put_frame(("permission_request", tool_name, tool_input))

        granted = evt.wait(timeout=_PERMISSION_TIMEOUT)
        with self._perm_lock:
            result = self._perm_allow
            self._perm_event = None
        if not granted:
            logger.warning(f"Permission request timed out for tool {tool_name!r} — denying")
        return result


class ClaudeCodeLLMService(LLMService):
    """Claude Code backend via Agent SDK thread bridge.

    Profile name: claude-code
    Switch via: talky claude-code

    permission_mode options:
      "acceptEdits"       — auto-accept all (default, no UI prompt)
      "default"           — surface each tool use to chat UI; user grants/denies
      "bypassPermissions" — skip all permission checks entirely (dangerous)
      "plan"              — read-only planning mode
    """

    def __init__(
        self,
        *,
        cwd: Optional[str] = None,
        model: Optional[str] = None,
        permission_mode: str = "acceptEdits",
        max_turns: int = 10,
        resume: Optional[str] = None,
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
        self._resume = resume
        self._bridge: Optional[_ClaudeSDKThread] = None
        self._frame_queue: asyncio.Queue = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None

    def _build_options(self) -> "ClaudeCodeOptions":
        opts = ClaudeCodeOptions(
            permission_mode=self._permission_mode,
            max_turns=self._max_turns,
            include_partial_messages=True,  # enables text_delta streaming
            continue_conversation=not self._resume,  # skip if explicit resume given
        )
        if self._resume:
            opts.resume = self._resume
        if self._model:
            opts.model = self._model
        if self._cwd:
            opts.cwd = self._cwd
        return opts

    def _attach_permission_callback(self, opts: "ClaudeCodeOptions", bridge: "_ClaudeSDKThread") -> None:
        """Wire can_use_tool only when permission_mode="default" (interactive mode)."""
        if self._permission_mode != "default":
            return
        if not _SDK_AVAILABLE:
            return

        async def can_use_tool(tool_name: str, tool_input: dict[str, Any], ctx: "ToolPermissionContext"):  # type: ignore[possibly-unbound]
            allow = await asyncio.get_event_loop().run_in_executor(
                None, bridge._permission_callback_sync, tool_name, tool_input
            )
            if allow:
                return PermissionResultAllow()  # type: ignore[possibly-unbound]
            return PermissionResultDeny(message="User denied via chat UI")  # type: ignore[possibly-unbound]

        opts.can_use_tool = can_use_tool

    def set_resume(self, session_id: Optional[str]) -> None:
        """Set a session ID to resume on the next pipeline start. One-shot: clears after use."""
        self._resume = session_id

    def resolve_permission(self, *, allow: bool) -> bool:
        """Resolve a pending permission prompt. Returns True if one was pending."""
        if self._bridge is None:
            return False
        return self._bridge.resolve_permission(allow=allow)

    async def start(self, frame: StartFrame):
        await super().start(frame)
        if not _SDK_AVAILABLE:
            return
        loop = asyncio.get_running_loop()
        self._frame_queue = asyncio.Queue()
        opts = self._build_options()
        self._bridge = _ClaudeSDKThread(opts, loop, self._frame_queue)
        self._attach_permission_callback(opts, self._bridge)
        self._resume = None  # one-shot: clear after building options
        self._bridge.start()
        self._reader_task = asyncio.create_task(self._drain_frames())
        logger.info(f"ClaudeCodeLLMService started (thread bridge, permission_mode={self._permission_mode!r})")

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
        from pipecat_mcp_server.event_bus import event_bus

        while True:
            item = await self._frame_queue.get()
            kind = item[0]
            if kind == "start":
                await self.push_frame(LLMFullResponseStartFrame())
            elif kind == "text":
                await self.push_frame(TextFrame(item[1]))
            elif kind == "thinking":
                await self.push_frame(AggregatedTextFrame(text=item[1], aggregated_by="thinking"))
            elif kind == "tool_start":
                logger.info(f"Claude tool: {item[1]}")
                await self.push_frame(AggregatedTextFrame(text=item[1], aggregated_by="tool_start"))
            elif kind == "permission_request":
                tool_name = item[1]
                tool_input = item[2]
                logger.info(f"Claude permission request: {tool_name!r}")
                await self.push_frame(AggregatedTextFrame(
                    text=f"Permission required: {tool_name}",
                    aggregated_by="permission_request",
                ))
                await event_bus.emit("permissionRequest", {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                })
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
