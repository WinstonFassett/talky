"""Hermes-agent backend for Talky — wraps AIAgent via a thread bridge.

AIAgent.run_conversation() is synchronous and blocking. Same thread-bridge
pattern as claude_code.py: run it in a dedicated OS thread, bridge frames
to Pipecat's asyncio event loop via an asyncio.Queue.

Interrupt semantics:
  - InterruptionFrame → passed through (no hard stop)
  - UserTurnTextFrame mid-turn → agent.steer(text) — non-interrupting injection;
    hermes finishes the current tool batch then sees the new user message
  - UserTurnTextFrame between turns → queued as a new prompt

Model: configurable via profile config. Defaults to anthropic/claude-sonnet-4-6
via Hermes' built-in provider routing. Can point at any OpenAI-compatible endpoint.

Import path: AIAgent lives in ~/.hermes/hermes-agent/ (not installed as a normal
package). We insert that directory into sys.path at import time.
"""

import asyncio
import os
import queue
import sys
import threading
from typing import Literal, Optional

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

_HERMES_DIR = os.path.expanduser("~/.hermes/hermes-agent")
_HERMES_CONFIG = os.path.expanduser("~/.hermes/config.yaml")

try:
    if _HERMES_DIR not in sys.path:
        sys.path.insert(0, _HERMES_DIR)
    from run_agent import AIAgent  # type: ignore[import]
    _HERMES_AVAILABLE = True
except ImportError:
    _HERMES_AVAILABLE = False

_TURN_START = ("start",)
_TURN_END = ("end",)


class _HermesThread:
    """Runs AIAgent.run_conversation() in a dedicated OS thread.

    Communication:
    - prompt_queue (threading.Queue): asyncio side → thread (str prompts, None sentinel)
    - frame_queue (asyncio.Queue): thread → Pipecat event loop
    - _agent: AIAgent instance with callbacks wired to _put_frame
    """

    def __init__(self, agent: "AIAgent", loop: asyncio.AbstractEventLoop, frame_queue: asyncio.Queue):
        self._agent = agent
        self._loop = loop
        self._frame_queue = frame_queue
        self._prompt_queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._turn_active = False

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="hermes-agent")
        self._thread.start()

    def send_prompt(self, text: str, steer_mode: str = "steer"):
        """Route mid-turn input based on steer_mode ("steer" or "interrupt")."""
        if self._turn_active:
            if steer_mode == "interrupt":
                self._agent.interrupt()
                self._prompt_queue.put(text)
            else:
                self._agent.steer(text)
        else:
            self._prompt_queue.put(text)

    def stop(self):
        self._prompt_queue.put(None)
        if self._thread:
            self._thread.join(timeout=5)

    def _put_frame(self, item: tuple):
        asyncio.run_coroutine_threadsafe(self._frame_queue.put(item), self._loop)

    def _run(self):
        history = []
        while True:
            prompt = self._prompt_queue.get()
            if prompt is None:
                break
            self._agent.clear_interrupt()
            self._turn_active = True
            self._put_frame(_TURN_START)
            try:
                result = self._agent.run_conversation(prompt, conversation_history=history)
                history = result.get("messages") or history
            except Exception as e:
                logger.error(f"Hermes run_conversation error: {e}", exc_info=True)
            finally:
                self._turn_active = False
                self._put_frame(_TURN_END)


class HermesLLMService(LLMService):
    """Hermes-agent (NousResearch) as a Pipecat LLM backend.

    Config keys (from llm-backends.yaml):
      model:    model string passed to AIAgent (e.g. "anthropic/claude-sonnet-4-6")
      provider: Hermes provider name (optional, auto by default)
      cwd:      working directory for the agent (optional)
      max_turns: max tool-calling iterations (default 90)
      yolo:     skip dangerous command prompts (default False)
    """

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        cwd: Optional[str] = None,
        max_turns: int = 90,
        resume: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        if not _HERMES_AVAILABLE:
            raise RuntimeError(
                f"hermes-agent not found at {_HERMES_DIR}. "
                "Install via: hermes update  or  pip install hermes-agent"
            )
        self._model = model
        self._provider = provider
        self._cwd = cwd
        self._max_turns = max_turns
        self._resume = resume
        self._steer_mode: Literal["steer", "interrupt"] = "steer"
        self._frame_queue: asyncio.Queue = asyncio.Queue()
        self._bridge: Optional[_HermesThread] = None
        self._drain_task: Optional[asyncio.Task] = None

    @staticmethod
    def _hermes_defaults() -> tuple[str, str]:
        """Read model/provider defaults from ~/.hermes/config.yaml."""
        try:
            import yaml  # type: ignore[import]
            with open(_HERMES_CONFIG) as f:
                cfg = yaml.safe_load(f) or {}
            model_cfg = cfg.get("model", {}) or {}
            return model_cfg.get("default", ""), model_cfg.get("provider", "")
        except Exception:
            return "", ""

    def _build_agent(self) -> "AIAgent":
        default_model, default_provider = self._hermes_defaults()
        kwargs: dict = dict(
            max_iterations=self._max_turns,
            quiet_mode=True,
        )
        if self._model or default_model:
            kwargs["model"] = self._model or default_model
        if self._provider or default_provider:
            kwargs["provider"] = self._provider or default_provider
        if self._resume:
            kwargs["session_id"] = self._resume

        agent = AIAgent(**kwargs)

        if self._cwd:
            agent.cwd = self._cwd

        # Wire callbacks to push frames into the queue.
        agent.stream_delta_callback = self._on_text_delta
        agent.reasoning_callback = self._on_reasoning
        agent.tool_start_callback = self._on_tool_start
        agent.tool_complete_callback = self._on_tool_complete

        return agent

    def set_steer_mode(self, mode: Literal["steer", "interrupt"]) -> None:
        self._steer_mode = mode

    def get_steer_mode(self) -> Literal["steer", "interrupt"]:
        return self._steer_mode

    # --- Callbacks (called from agent thread) ---

    def _on_text_delta(self, delta: Optional[str]):
        if delta is not None:
            asyncio.run_coroutine_threadsafe(
                self._frame_queue.put(("text", delta)), self._loop
            )

    def _on_reasoning(self, text: str):
        if text:
            asyncio.run_coroutine_threadsafe(
                self._frame_queue.put(("reasoning", text)), self._loop
            )

    def _on_tool_start(self, tool_id: str, name: str, args: dict):
        hint = ""
        if "path" in args:
            hint = f": {args['path']}"
        elif "command" in args:
            cmd = str(args.get("command", ""))
            hint = f": {cmd[:60]}{'…' if len(cmd) > 60 else ''}"
        elif "pattern" in args:
            hint = f": {args['pattern']}"
        asyncio.run_coroutine_threadsafe(
            self._frame_queue.put(("tool_start", f"▶ {name}{hint}")), self._loop
        )

    def _on_tool_complete(self, tool_id: str, name: str, result: dict, is_error: bool = False):
        symbol = "✗" if is_error else "✓"
        asyncio.run_coroutine_threadsafe(
            self._frame_queue.put(("tool_end", f"{symbol} {name}")), self._loop
        )

    # --- Pipecat lifecycle ---

    async def start(self, frame: StartFrame):
        await super().start(frame)
        self._loop = asyncio.get_event_loop()
        agent = self._build_agent()
        self._bridge = _HermesThread(agent, self._loop, self._frame_queue)
        self._bridge.start()
        self._drain_task = asyncio.create_task(self._drain_frames())

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._shutdown()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._shutdown()

    async def _shutdown(self):
        if self._drain_task:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except (asyncio.CancelledError, Exception):
                pass
            self._drain_task = None
        if self._bridge:
            self._bridge.stop()
            self._bridge = None

    # --- Frame drain loop ---

    async def _drain_frames(self):
        """Pull items from frame_queue and push Pipecat frames."""
        try:
            while True:
                item = await self._frame_queue.get()
                if item is _TURN_START:
                    await self.push_frame(LLMFullResponseStartFrame())
                elif item is _TURN_END:
                    await self.push_frame(LLMFullResponseEndFrame())
                else:
                    kind, payload = item
                    if kind == "text":
                        await self.push_frame(TextFrame(payload))
                    elif kind == "reasoning":
                        await self.push_frame(AggregatedTextFrame(text=payload, aggregated_by="thinking"))
                    elif kind == "tool_start":
                        await self.push_frame(AggregatedTextFrame(text=payload, aggregated_by="tool_start"))
                    elif kind == "tool_end":
                        await self.push_frame(AggregatedTextFrame(text=payload, aggregated_by="tool_end"))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Hermes frame drain error: {e}", exc_info=True)

    # --- process_frame ---

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            if self._bridge:
                self._bridge.send_prompt(frame.text, steer_mode=self._steer_mode)
            return

        await self.push_frame(frame, direction)
