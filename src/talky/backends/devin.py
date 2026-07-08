"""Devin CLI backend for Talky — wraps the local `devin` terminal agent.

Devin's CLI (`devin -p "prompt"`) runs the agent on the local machine: model
inference is remote, but tool execution (file edits, shell) hits the local
filesystem. Output streams to stdout. This mirrors how claude_code.py wraps a
local agent — it is NOT the cloud REST API (api.devin.ai/v3).

Turn loop:
  1. Spawn `devin -p "<prompt>"` (with --continue for multi-turn continuity).
  2. Stream stdout lines → TextFrame.
  3. Process exits → turn done.

Interrupt semantics:
  - InterruptionFrame → kill the subprocess → real cancel. The agent stops
    immediately, unlike the cloud REST path which has no cancel primitive.
  - Next UserTurnTextFrame spawns `devin -p "..." --continue` which resumes
    the same conversation session.

Config (llm-backends.yaml):
  permission_mode: "auto" | "accept-edits" | "smart" | "dangerous"
                   (default: "accept-edits" — agent can edit files without asking)
  model:           Optional model override (e.g. "claude-sonnet-4")
  cwd:             Optional working directory for the agent
  binary:          Path to devin binary (default: auto-discovered via PATH)
"""

import asyncio
import os
import shutil
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from talky.backends import BackendStatus

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

from talky.config.voice_prompts import format_voice_message
from talky.server.turn import UserTurnTextFrame

DEFAULT_PERMISSION_MODE = "accept-edits"


class DevinLLMService(LLMService):
    """Devin CLI as a Pipecat LLM backend (subprocess, streaming stdout)."""

    @staticmethod
    def status() -> tuple["BackendStatus", str]:
        """Backend Status — see UBIQUITOUS_LANGUAGE.md.

        Cheap pre-construction check: is the `devin` binary on PATH?
        No network call. Missing binary is user-fixable — Installable.
        """
        from talky.backends import BackendStatus
        if not shutil.which("devin"):
            return BackendStatus.INSTALLABLE, "devin CLI not found on PATH"
        return BackendStatus.READY, ""

    def __init__(
        self,
        *,
        permission_mode: str = DEFAULT_PERMISSION_MODE,
        model: Optional[str] = None,
        cwd: Optional[str] = None,
        binary: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._binary = binary or shutil.which("devin") or "devin"
        self._permission_mode = permission_mode
        self._model = model
        self._cwd = cwd
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._turn_lock = asyncio.Lock()
        self._has_prior_turn = False

        logger.info(f"DevinLLMService initialized (binary={self._binary}, permission_mode={self._permission_mode!r})")

    def _build_args(self, prompt: str, *, continue_session: bool) -> list[str]:
        args = [self._binary, "-p", prompt]
        if continue_session:
            args.append("--continue")
        args.extend(["--permission-mode", self._permission_mode])
        if self._model:
            args.extend(["--model", self._model])
        return args

    async def _process_user_text(self, user_text: str) -> None:
        async with self._turn_lock:
            await self.push_frame(LLMFullResponseStartFrame())
            try:
                prompt = format_voice_message(user_text)
                continue_session = self._has_prior_turn
                args = self._build_args(prompt, continue_session=continue_session)
                logger.info(f"Devin ← user: {user_text[:100]} (continue={continue_session})")

                self._proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self._cwd,
                )

                # Stream stdout lines → TextFrame
                assert self._proc.stdout is not None
                while True:
                    line = await self._proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").rstrip("\n\r")
                    if text:
                        await self.push_frame(TextFrame(text))

                await self._proc.wait()
                self._has_prior_turn = True

                # Log stderr if non-empty (warnings, not fatal)
                if self._proc.stderr:
                    stderr_data = await self._proc.stderr.read()
                    if stderr_data:
                        stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
                        if stderr_text:
                            logger.warning(f"Devin stderr: {stderr_text[:500]}")

                if self._proc.returncode and self._proc.returncode != 0:
                    logger.error(f"Devin exited with code {self._proc.returncode}")

            except Exception as e:
                logger.error(f"Devin turn error: {e}", exc_info=True)
                await self.push_frame(TextFrame("Sorry, I hit an error talking to Devin."))
            finally:
                self._proc = None
                await self.push_frame(LLMFullResponseEndFrame())

    async def _kill_proc(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.kill()
                await self._proc.wait()
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.warning(f"Error killing Devin process: {e}")
        self._proc = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            await self._kill_proc()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            await self._process_user_text(frame.text)
            return

        await self.push_frame(frame, direction)

    async def stop(self, frame):
        await self._kill_proc()
        if hasattr(super(), "stop"):
            await super().stop(frame)
