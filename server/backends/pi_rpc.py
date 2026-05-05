"""Pi RPC backend for Talky — spawns `pi --mode rpc` as subprocess, streams text_delta events."""

import asyncio
import json
import shutil
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
        cmd = ["pi", "--mode", "rpc"]
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
            stderr=asyncio.subprocess.DEVNULL,
            cwd=self._cwd,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
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
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
            self._proc = None

    async def _write(self, msg: dict):
        if self._proc and self._proc.stdin:
            line = json.dumps(msg) + "\n"
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
                    if evt.get("type") == "text_delta":
                        delta = evt.get("delta", "")
                        if delta:
                            await self.push_frame(TextFrame(delta))

                elif event_type == "agent_end":
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

        await self.push_frame(frame, direction)
