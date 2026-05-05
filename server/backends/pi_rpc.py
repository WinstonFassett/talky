"""Pi RPC backend for Talky — spawns `pi --mode rpc` as subprocess, streams text_delta events."""

import asyncio
import json
import shutil
from typing import Optional

from loguru import logger
from pipecat.frames.frames import (
    AggregatedTextFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    StartFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat_mcp_server.talky_turn import UserTurnTextFrame


def _format_tool_start(data: dict) -> str:
    name = data.get("toolName", "?")
    args = data.get("args") or {}
    hint = ""
    if "path" in args:
        hint = f": {args['path']}"
    elif "command" in args:
        cmd = str(args["command"])
        hint = f": {cmd[:60]}{'…' if len(cmd) > 60 else ''}"
    elif "pattern" in args:
        hint = f": {args['pattern']}"
    return f"▶ {name}{hint}"


def _format_tool_end(data: dict) -> str:
    name = data.get("toolName", "?")
    if data.get("isError"):
        return f"✗ {name}"
    result = data.get("result") or {}
    lines = None
    if isinstance(result, dict):
        content = result.get("content", [])
        if isinstance(content, list) and content:
            text = next((c.get("text", "") for c in content if c.get("type") == "text"), "")
            if text:
                lines = len(text.splitlines())
    suffix = f" ({lines} lines)" if lines else ""
    return f"✓ {name}{suffix}"


class PiRPCLLMService(LLMService):
    """Pi coding agent via --mode rpc. Interruptible: sends {"type":"abort"} on InterruptionFrame."""

    def __init__(
        self,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        cwd: Optional[str] = None,
        extra_args: Optional[list] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._provider = provider
        self._model = model
        self._cwd = cwd
        self._extra_args = extra_args or []
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None

    def _build_cmd(self) -> list[str]:
        cmd = ["pi", "--mode", "rpc", "--no-extensions"]
        if self._provider:
            cmd += ["--provider", self._provider]
        if self._model:
            cmd += ["--model", self._model]
        cmd.extend(self._extra_args)
        return cmd

    async def start(self, frame: StartFrame):
        await super().start(frame)
        pi_bin = shutil.which("pi")
        if not pi_bin:
            raise RuntimeError("pi command not found in PATH")
        cmd = self._build_cmd()
        logger.info(f"Starting Pi RPC: {' '.join(cmd)}")
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._log_stderr())
        logger.info("Pi RPC process started")

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
        proc = self._proc
        self._proc = None
        if proc:
            exc_to_reraise = None
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3)
            except BaseException as e:
                # CancelledError, TimeoutError, ProcessLookupError — all mean
                # we must kill the child before propagating.
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                if isinstance(e, asyncio.CancelledError):
                    exc_to_reraise = e
            if exc_to_reraise is not None:
                raise exc_to_reraise

    async def _log_stderr(self):
        try:
            while self._proc and self._proc.stderr:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                logger.debug(f"Pi stderr: {line.decode().rstrip()}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Pi stderr reader error: {e}")

    async def _write(self, msg: dict):
        if self._proc and self._proc.stdin:
            line = json.dumps(msg) + "\n"
            logger.info(f"Pi RPC → stdin: {line.rstrip()}")
            self._proc.stdin.write(line.encode())
            await self._proc.stdin.drain()

    async def _read_stdout(self):
        """Read Pi's stdout JSONL events and push Pipecat frames."""
        try:
            while self._proc and self._proc.stdout:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    data = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue

                event_type = data.get("type")

                if event_type == "extension_ui_request":
                    continue

                if event_type == "agent_start":
                    await self.push_frame(LLMFullResponseStartFrame())

                elif event_type == "message_update":
                    evt = data.get("assistantMessageEvent", {})
                    evt_type = evt.get("type")
                    if evt_type == "text_delta":
                        delta = evt.get("delta", "")
                        if delta:
                            await self.push_frame(TextFrame(delta))
                    elif evt_type == "thinking_delta":
                        delta = evt.get("delta", "")
                        if delta:
                            await self.push_frame(
                                AggregatedTextFrame(text=delta, aggregated_by="thinking")
                            )

                elif event_type == "tool_execution_start":
                    text = _format_tool_start(data)
                    await self.push_frame(
                        AggregatedTextFrame(text=text, aggregated_by="tool_start")
                    )

                elif event_type == "tool_execution_end":
                    text = _format_tool_end(data)
                    await self.push_frame(
                        AggregatedTextFrame(text=text, aggregated_by="tool_end")
                    )

                elif event_type == "agent_end":
                    # Surface any error message from the last assistant turn.
                    msgs = data.get("messages") or []
                    for msg in reversed(msgs):
                        if msg.get("role") == "assistant" and msg.get("stopReason") == "error":
                            err = msg.get("errorMessage", "unknown error")
                            logger.error(f"Pi RPC LLM error: {err}")
                            break
                    await self.push_frame(LLMFullResponseEndFrame())

                elif event_type == "response" and not data.get("success", True):
                    logger.warning(f"Pi RPC error: {data.get('error')}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Pi RPC stdout reader error: {e}", exc_info=True)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            await self._write({"type": "abort"})
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            await self._write({"type": "prompt", "message": frame.text})
            return

        if isinstance(frame, LLMMessagesAppendFrame) and frame.run_llm:
            for msg in frame.messages:
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str) and content:
                        await self._write({"type": "prompt", "message": content})
            return

        await self.push_frame(frame, direction)
