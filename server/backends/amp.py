"""Amp coding agent backend for Talky — streams JSONL via subprocess."""

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


class AmpLLMService(LLMService):
    """Amp coding agent via --execute --stream-json.

    Interruptible: writes {"steer": true} to stdin on InterruptionFrame.
    Session continuity: thread_id persisted across turns, resumed via --continue.
    """

    def __init__(
        self,
        *,
        cwd: Optional[str] = None,
        mode: str = "smart",
        extra_args: Optional[list] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._cwd = cwd
        self._mode = mode
        self._extra_args = extra_args or []
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._thread_id: Optional[str] = None
        self._pending_start_frame = False

    def _build_cmd(self) -> list[str]:
        amp_bin = shutil.which("amp")
        if not amp_bin:
            raise RuntimeError("amp command not found in PATH — install from https://ampcode.com")
        cmd = [amp_bin, "--execute", "--stream-json", "--stream-json-input", "--mode", self._mode]
        if self._thread_id:
            cmd += ["--continue", self._thread_id]
        cmd.extend(self._extra_args)
        return cmd

    async def start(self, frame: StartFrame):
        await super().start(frame)
        cmd = self._build_cmd()
        logger.info(f"Starting Amp: {' '.join(cmd)}")
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._log_stderr())
        logger.info("Amp process started")

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
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stderr_task = None
        proc = self._proc
        self._proc = None
        if proc:
            exc_to_reraise = None
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3)
            except BaseException as e:
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
                logger.debug(f"Amp stderr: {line.decode().rstrip()}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Amp stderr reader error: {e}")

    async def _write(self, msg: dict):
        if self._proc and self._proc.stdin:
            line = json.dumps(msg) + "\n"
            logger.info(f"Amp → stdin: {line.rstrip()}")
            self._proc.stdin.write(line.encode())
            await self._proc.stdin.drain()

    async def _read_stdout(self):
        """Read Amp's stdout JSONL and push Pipecat frames."""
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

                if event_type == "system":
                    # Session established — extract thread_id for resume
                    msg = data.get("message", {})
                    if session_id := msg.get("sessionId"):
                        self._thread_id = session_id
                        logger.info(f"Amp session: {session_id}")
                    await self.push_frame(LLMFullResponseStartFrame())
                    self._pending_start_frame = False

                elif event_type == "assistant":
                    content = data.get("message", {}).get("content", [])
                    for block in content:
                        if block.get("type") == "text":
                            delta = block.get("text", "")
                            if delta:
                                await self.push_frame(TextFrame(delta))

                elif event_type == "result":
                    usage = data.get("message", {}).get("usage", {})
                    if usage:
                        logger.info(
                            f"Amp turn complete — "
                            f"input: {usage.get('input_tokens', '?')} "
                            f"output: {usage.get('output_tokens', '?')} tokens"
                        )
                    await self.push_frame(LLMFullResponseEndFrame())

                elif event_type == "error":
                    err = data.get("message", {}).get("error", "unknown error")
                    logger.error(f"Amp error: {err}")
                    await self.push_frame(LLMFullResponseEndFrame())

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Amp stdout reader error: {e}", exc_info=True)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            # Steer with interrupt signal — Amp queues this at next interruption point
            logger.info("Amp ← InterruptionFrame: sending steer")
            await self._write({
                "type": "user",
                "message": {"role": "user", "content": "[interrupted by user]"},
                "steer": True,
            })
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            if self._pending_start_frame:
                # Session didn't emit system event yet — emit start frame ourselves
                await self.push_frame(LLMFullResponseStartFrame())
                self._pending_start_frame = False
            await self._write({
                "type": "user",
                "message": {"role": "user", "content": frame.text},
            })
            # New session: next event should be system, which emits StartFrame
            # Resumed session: system may not re-emit; mark pending
            if self._thread_id:
                self._pending_start_frame = True
            return

        await self.push_frame(frame, direction)
