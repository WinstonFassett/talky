"""Devin backend for Talky — drives `devin acp` over stdio (Agent Client Protocol).

`devin acp` is a JSON-RPC 2.0 server (newline-delimited JSON on stdin/stdout)
exposing the standard ACP surface: initialize, session/new, session/load,
session/prompt, session/cancel, plus streamed session/update notifications
(agent_message_chunk, agent_thought_chunk, tool_call, tool_call_update).
`devin acp --cloud` relays the same transport to Devin Cloud sessions, so one
adapter covers local and cloud Devin.

Turn loop:
  1. Handshake once at pipeline start: initialize → (authenticate if
     DEVIN_API_KEY is set) → session/new (or session/load on resume).
  2. Each user turn → session/prompt; streamed chunks → TextFrame /
     AggregatedTextFrame; the prompt response ends the turn.

Interrupt semantics:
  - InterruptionFrame → session/cancel notification → real barge-in. The
    in-flight session/prompt resolves with stopReason "cancelled" and queued
    user turns are dropped.
  - Queued turns: devin acp serializes session/prompt calls itself
    (acp_bridge_dispatch queue_wait), but voice semantics want the newest
    utterance only — InterruptionFrame drains the pending queue.

Permission modes are session config, not a CLI flag: `devin acp` ignores
--permission-mode; the mode is set via session/set_config_option
(configId/optionId "mode"). Values: accept-edits | smart | ask | plan | bypass.
Outside bypass, session/request_permission server requests are auto-answered
with the first allow-style option so a voice turn never hangs on a prompt the
user can't see.

Config (llm-backends.yaml):
  mode:   "accept-edits" | "smart" | "ask" | "plan" | "bypass" (default: accept-edits)
  model:  optional model override (e.g. "claude-sonnet-4", "opus")
  cwd:    working directory for the agent session
  binary: path to devin binary (default: PATH lookup)
  cloud:  true → spawn `devin acp --cloud` (relays to Devin Cloud sessions)
  resume: devin session id to reload via session/load (one-shot; also settable
          at runtime via set_resume())
"""

import asyncio
import json
import os
import shutil
from typing import TYPE_CHECKING, Any, Optional

# Env vars injected by the Devin desktop app (Windsurf fork) that poison the
# standalone devin CLI — it detects them, thinks it's running inside Windsurf,
# and rejects the Windsurf session token as invalid for standalone use.
_POISON_ENV_KEYS = frozenset({
    "ACP_BACKEND",
    "WINDSURF_IDE_TYPE",
    "WINDSURF_EXT_HOST_PID",
    "VSCODE_IPC_HOOK",
    "VSCODE_NLS_CONFIG",
    "VSCODE_CODE_CACHE_PATH",
    "VSCODE_DEBUGPY_ADAPTER_ENDPOINTS",
    "BUNDLED_DEBUGPY_PATH",
    "PYTHONSTARTUP",
    "TERM_PROGRAM_VERSION",
    "__CFBundleIdentifier",
})

if TYPE_CHECKING:
    from talky.backends import BackendStatus

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

from talky.config.voice_prompts import format_voice_message
from talky.server.turn import UserTurnTextFrame

# Devin emits large updates (available_commands_update carries the whole
# slash-command catalog); 64 KiB default line limit is not enough.
_STDOUT_LINE_LIMIT = 32 * 1024 * 1024

DEFAULT_MODE = "accept-edits"


class DevinACPLLMService(LLMService):
    """Devin via `devin acp` — interruptible, structured, resumable."""

    @staticmethod
    def status() -> tuple["BackendStatus", str]:
        """Backend Status — see UBIQUITOUS_LANGUAGE.md.

        Cheap pre-construction check: is the `devin` binary on PATH? No
        subprocess, no network. No Python extra for devin — Misconfigured,
        not Installable.
        """
        from talky.backends import BackendStatus
        if shutil.which("devin") is None:
            return BackendStatus.MISCONFIGURED, "devin CLI not found on PATH"
        return BackendStatus.READY, ""

    def __init__(
        self,
        *,
        mode: str = DEFAULT_MODE,
        model: Optional[str] = None,
        cwd: Optional[str] = None,
        binary: Optional[str] = None,
        cloud: bool = False,
        resume: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._binary = binary or "devin"
        self._mode = mode
        self._model = model
        self._cwd = cwd
        self._cloud = cloud
        self._resume = resume  # one-shot: consumed by the next session/open
        self._session_id: Optional[str] = None

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._prompt_worker: Optional[asyncio.Task] = None
        self._prompt_queue: asyncio.Queue[str] = asyncio.Queue()
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._dead = False

    # ------------------------------------------------------------------
    # lifecycle

    def _build_cmd(self) -> list[str]:
        cmd = [self._binary]
        if self._model:
            cmd += ["--model", self._model]
        cmd.append("acp")
        if self._cloud:
            cmd.append("--cloud")
        return cmd

    async def start(self, frame: StartFrame):
        await super().start(frame)
        self._dead = False
        env = {k: v for k, v in os.environ.items() if k not in _POISON_ENV_KEYS}
        cmd = self._build_cmd()
        logger.info(f"Starting Devin ACP: {' '.join(cmd)}")
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=env,
            limit=_STDOUT_LINE_LIMIT,
            start_new_session=True,  # own process group for clean tree kill
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._log_stderr())
        asyncio.create_task(self._watchdog())
        self._prompt_worker = asyncio.create_task(self._prompt_loop())
        try:
            await self._handshake()
        except Exception as e:
            logger.error(f"Devin ACP handshake failed: {e}")
            await self._handle_subprocess_death()
            return
        logger.info(f"Devin ACP ready, session={self._session_id}")

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._shutdown()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._shutdown()

    async def _shutdown(self):
        if self._prompt_worker:
            self._prompt_worker.cancel()
            try:
                await self._prompt_worker
            except (asyncio.CancelledError, Exception):
                pass
            self._prompt_worker = None
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
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

    async def _watchdog(self):
        """Catch exits where stdout doesn't close promptly (child holds pipes)."""
        try:
            while self._proc and not self._dead:
                await asyncio.sleep(2)
                if self._proc and self._proc.returncode is not None:
                    await self._handle_subprocess_death()
                    break
        except asyncio.CancelledError:
            pass

    async def _log_stderr(self):
        try:
            while self._proc and self._proc.stderr:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                logger.debug(f"devin acp stderr: {line.decode(errors='replace').rstrip()}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"devin acp stderr reader error: {e}")

    async def _handle_subprocess_death(self):
        if self._dead:
            return
        self._dead = True
        logger.error("devin acp subprocess died — marking backend dead")
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("devin acp process exited"))
        try:
            from talky.server.event_bus import event_bus
            await event_bus.emit("backendError", {
                "profile": "devin",
                "message": "Devin channel lost — the agent process exited. Reconnect to restart.",
            })
        except Exception:
            pass

    # ------------------------------------------------------------------
    # ACP handshake

    async def _handshake(self):
        await self._request("initialize", {
            "protocolVersion": 1,
            "clientInfo": {"name": "talky", "version": "0.1.0"},
            # No fs/terminal capabilities — devin runs tools itself; outside
            # bypass mode it may still ask via session/request_permission.
            "clientCapabilities": {},
        })

        api_key = os.environ.get("DEVIN_API_KEY")
        if api_key:
            await self._request("authenticate", {
                "methodId": "windsurf-api-key",
                "credentials": {"apiKey": api_key},
            })

        cwd = self._cwd or os.getcwd()
        resume_id, self._resume = self._resume, None  # one-shot
        if resume_id:
            result = await self._request("session/load", {
                "cwd": cwd,
                "sessionId": resume_id,
                "mcpServers": [],
            })
            self._session_id = (result or {}).get("sessionId") or resume_id
        else:
            result = await self._request("session/new", {
                "cwd": cwd,
                "mcpServers": [],
            })
            self._session_id = (result or {}).get("sessionId")
            if not self._session_id:
                raise RuntimeError("devin session/new returned no sessionId")

        if self._mode:
            try:
                await self._request("session/set_config_option", {
                    "sessionId": self._session_id,
                    "configId": "mode",
                    "optionId": "mode",
                    "value": self._mode,
                })
            except Exception as e:
                logger.warning(f"devin set_config_option mode={self._mode} failed: {e}")

    # ------------------------------------------------------------------
    # JSON-RPC plumbing

    async def _request(self, method: str, params: dict, timeout: float = 120) -> Any:
        if self._dead or not self._proc or not self._proc.stdin:
            raise RuntimeError("devin acp not running")
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        line = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            self._proc.stdin.write((line + "\n").encode())
            await self._proc.stdin.drain()
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    def _notify(self, method: str, params: dict):
        if self._dead or not self._proc or not self._proc.stdin:
            return
        line = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        try:
            self._proc.stdin.write((line + "\n").encode())
        except Exception as e:
            logger.warning(f"devin acp notify {method} failed: {e}")

    def _respond(self, rid: Any, result: Optional[dict] = None, error: Optional[dict] = None):
        if not self._proc or not self._proc.stdin:
            return
        msg: dict = {"jsonrpc": "2.0", "id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result if result is not None else {}
        try:
            self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        except Exception as e:
            logger.warning(f"devin acp respond failed: {e}")

    async def _read_stdout(self):
        try:
            while self._proc and self._proc.stdout:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                await self._dispatch(msg)
            await self._handle_subprocess_death()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"devin acp stdout reader error: {e}", exc_info=True)
            await self._handle_subprocess_death()

    async def _dispatch(self, msg: dict):
        method = msg.get("method")
        if "id" in msg and not method:
            # Response to one of our requests.
            fut = self._pending.get(msg["id"])
            if fut and not fut.done():
                if "error" in msg:
                    err = msg["error"] or {}
                    fut.set_exception(RuntimeError(f"{err.get('message', 'rpc error')} (code {err.get('code')})"))
                else:
                    fut.set_result(msg.get("result"))
            return
        if method == "session/update":
            await self._handle_update(msg.get("params") or {})
            return
        if "id" in msg and method:
            # Server→client request. Only permission asks get a real answer;
            # everything else (fs/*, terminal/*) — we advertised no caps.
            if method == "session/request_permission":
                self._answer_permission(msg)
            else:
                logger.debug(f"devin acp unhandled server request: {method}")
                self._respond(msg["id"], error={"code": -32601, "message": f"talky: {method} not implemented"})

    def _answer_permission(self, msg: dict):
        params = msg.get("params") or {}
        options = params.get("options") or []
        pick = None
        for opt in options:
            ident = f"{opt.get('optionId', '')} {opt.get('name', '')}".lower()
            if "always" in ident or "allow" in ident:
                pick = opt
                break
        if pick is None and options:
            pick = options[0]
        if pick is None:
            self._respond(msg["id"], result={"outcome": {"outcome": "cancelled"}})
            return
        logger.info(f"devin acp permission auto-approved: {pick.get('optionId')}")
        self._respond(msg["id"], result={
            "outcome": {"outcome": "selected", "optionId": pick.get("optionId")},
        })

    # ------------------------------------------------------------------
    # session/update → Pipecat frames

    @staticmethod
    def _update_type(update: dict) -> str:
        raw = update.get("sessionUpdate") or update.get("type") or ""
        key = raw.replace("_", "").replace("-", "").lower()
        return {
            "agentmessagechunk": "agent_message_chunk",
            "agentthoughtchunk": "agent_thought_chunk",
            "toolcall": "tool_call",
            "toolcallupdate": "tool_call_update",
        }.get(key, raw)

    async def _handle_update(self, params: dict):
        update = params.get("update") or {}
        kind = self._update_type(update)

        if kind == "agent_message_chunk":
            text = ((update.get("content") or {}).get("text"))
            if text:
                await self.push_frame(TextFrame(text))
        elif kind == "agent_thought_chunk":
            text = ((update.get("content") or {}).get("text"))
            if text:
                await self.push_frame(AggregatedTextFrame(text=text, aggregated_by="thinking"))
        elif kind == "tool_call":
            await self.push_frame(AggregatedTextFrame(
                text=self._format_tool_call(update), aggregated_by="tool_start"))
        elif kind == "tool_call_update":
            status = update.get("status")
            if status in ("completed", "failed"):
                name = update.get("title") or update.get("name") or "tool"
                mark = "✗" if status == "failed" else "✓"
                await self.push_frame(AggregatedTextFrame(
                    text=f"{mark} {name}", aggregated_by="tool_end"))
        # usage_update / config_option_update / available_commands_update /
        # session_info_update / _cognition.ai/* — not surfaced to voice.

    @staticmethod
    def _format_tool_call(update: dict) -> str:
        name = update.get("title") or update.get("name") or update.get("kind") or "tool"
        args = update.get("rawInput") or update.get("input") or {}
        hint = ""
        if isinstance(args, dict):
            if "path" in args:
                hint = f": {args['path']}"
            elif "command" in args:
                cmd = str(args["command"])
                hint = f": {cmd[:60]}{'…' if len(cmd) > 60 else ''}"
        return f"▶ {name}{hint}"

    # ------------------------------------------------------------------
    # turns

    async def _prompt_loop(self):
        """Serializes session/prompt calls; one Start/End pair per turn."""
        try:
            while True:
                text = await self._prompt_queue.get()
                await self.push_frame(LLMFullResponseStartFrame())
                try:
                    result = await self._request("session/prompt", {
                        "sessionId": self._session_id,
                        "prompt": [{"type": "text", "text": text}],
                    }, timeout=600)
                    if (result or {}).get("stopReason") == "cancelled":
                        logger.info("devin turn cancelled")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"devin session/prompt failed: {e}")
                    await self.push_frame(TextFrame("Sorry, I hit an error talking to Devin."))
                finally:
                    await self.push_frame(LLMFullResponseEndFrame())
        except asyncio.CancelledError:
            pass

    def _drain_prompt_queue(self):
        while not self._prompt_queue.empty():
            try:
                self._prompt_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _enqueue_prompt(self, text: str):
        if self._dead:
            logger.warning("devin acp dead — dropping prompt")
            return
        if not self._session_id:
            logger.warning("devin acp: no session yet — dropping prompt")
            return
        await self._prompt_queue.put(format_voice_message(text))

    # ------------------------------------------------------------------
    # resume hooks (convo records read `session_id`; /api/resume calls set_resume)

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def set_resume(self, session_id: Optional[str]) -> None:
        """Set a devin session id to session/load on next start. One-shot."""
        self._resume = session_id

    # ------------------------------------------------------------------
    # frames

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            self._drain_prompt_queue()
            if self._session_id:
                self._notify("session/cancel", {"sessionId": self._session_id})
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            await self._enqueue_prompt(frame.text)
            return

        if isinstance(frame, LLMMessagesAppendFrame) and frame.run_llm:
            for msg in frame.messages:
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str) and content:
                        await self._enqueue_prompt(content)
            return

        await self.push_frame(frame, direction)
