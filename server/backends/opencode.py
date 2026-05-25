"""Opencode (sst/opencode) backend for Talky.

Architecture
------------
Opencode is a headless HTTP server (`opencode serve --port 4096`) with a
typed SSE event stream at /event. There is no in-process SDK to embed — every
interaction is HTTP.

Unlike claude_code / hermes which need an OS-thread bridge because the SDK is
blocking, opencode's interface is fully async-friendly. We stay on asyncio:

  - one httpx.AsyncClient for control-plane calls (session create, prompt_async,
    permission reply, abort).
  - one background task consuming the /event SSE stream, dispatching frames.

Frame mapping
-------------
  message.part.updated (part.type=="text", delta!=None)  → TextFrame(delta)
  message.part.updated (part.type=="reasoning", delta)   → AggregatedTextFrame(thinking)
  message.part.updated (part.type=="tool", state new)    → AggregatedTextFrame(tool_start)
  permission.asked                                       → permission_request frame +
                                                            event_bus.emit("permissionRequest")
  session.idle                                           → LLMFullResponseEndFrame

Permission flow
---------------
On `permission.asked`, we park (request_id, session_id) and emit a
``permissionRequest`` SSE event in the same shape the claude-code backend uses
(``{tool_name, tool_input}``) so the existing PermissionBanner picks it up.
``resolve_permission(allow=...)`` calls
``POST /session/{sid}/permissions/{pid}`` with ``{"response": "once"|"reject"}``.

Interrupt
---------
``InterruptionFrame`` → ``POST /session/{id}/abort``. Mid-tool barge-in is
supported by the server.
"""

import asyncio
import json
import os
import socket
import subprocess
import time
from typing import Any, Optional

import httpx
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


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class OpencodeLLMService(LLMService):
    """Opencode HTTP backend.

    Profile name: opencode.

    Config keys (from llm-backends.yaml):
      base_url:     where opencode serve is listening
      auto_spawn:   if true, spawn `opencode serve` when nothing answers
      cwd:          working directory passed to opencode serve (and ?directory= on session create)
      provider_id:  e.g. "opencode" or "anthropic" — leave None to use opencode default
      model_id:     model within the provider — leave None to use opencode default
      permissions:  list of {permission, pattern, action} ruleset entries (per session)
      resume:       existing session id to reuse instead of creating a new one
    """

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:4096",
        auto_spawn: bool = True,
        cwd: Optional[str] = None,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
        permissions: Optional[list[dict]] = None,
        resume: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._base_url = base_url.rstrip("/")
        self._auto_spawn = auto_spawn
        self._cwd = cwd
        self._provider_id = provider_id
        self._model_id = model_id
        self._permissions = permissions or []
        self._resume = resume

        self._client: Optional[httpx.AsyncClient] = None
        self._session_id: Optional[str] = None
        self._event_task: Optional[asyncio.Task] = None
        self._spawned_proc: Optional[subprocess.Popen] = None

        # Permission state — guarded by _perm_lock.
        self._perm_lock = asyncio.Lock()
        self._pending_permission: Optional[tuple[str, str]] = None  # (perm_id, session_id)

        # Tracks whether we've emitted LLMFullResponseStartFrame for the current turn
        # so we don't emit duplicates between message.part.updated events.
        self._turn_started = False

    # --- Public control ---

    def set_resume(self, session_id: Optional[str]) -> None:
        """One-shot: use this session id on next start instead of creating a new one."""
        self._resume = session_id

    async def resolve_permission(self, *, allow: bool) -> bool:
        """Reply to a pending opencode permission ask.

        Returns True if there was a pending request to resolve.
        """
        async with self._perm_lock:
            pending = self._pending_permission
            self._pending_permission = None
        if not pending or not self._client:
            return False
        perm_id, session_id = pending
        response = "once" if allow else "reject"
        try:
            r = await self._client.post(
                f"/session/{session_id}/permissions/{perm_id}",
                json={"response": response},
            )
            r.raise_for_status()
        except Exception as e:
            logger.error(f"opencode permission reply failed: {e}")
            return False
        return True

    # --- Lifecycle ---

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self._ensure_server_running()
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=httpx.Timeout(60.0, read=None))
        await self._ensure_session()
        self._event_task = asyncio.create_task(self._consume_events())
        logger.info(
            f"OpencodeLLMService started — session={self._session_id} provider={self._provider_id}/{self._model_id}"
        )

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._shutdown()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._shutdown()

    async def _shutdown(self):
        if self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except (asyncio.CancelledError, Exception):
                pass
            self._event_task = None
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
        # Note: we deliberately do NOT kill the spawned opencode process here.
        # It costs ~3s to cold-start and the user may switch back to this profile;
        # let it keep running in the background. It binds 127.0.0.1 only.

    # --- Server bring-up ---

    async def _ensure_server_running(self) -> None:
        host, port = self._parse_host_port()
        if _port_open(host, port):
            return
        if not self._auto_spawn:
            raise RuntimeError(
                f"opencode serve not running at {self._base_url} and auto_spawn=False"
            )
        cwd = self._cwd or os.getcwd()
        logger.info(f"opencode serve not running — spawning at port {port} in {cwd}")
        # opencode reads OPENCODE_SERVER_PASSWORD; we leave it unset for local-only.
        self._spawned_proc = subprocess.Popen(
            ["opencode", "serve", "--port", str(port), "--hostname", host],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait up to 10s for port to open.
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if _port_open(host, port):
                return
            await asyncio.sleep(0.2)
        raise RuntimeError(f"opencode serve did not start within 10s on {self._base_url}")

    def _parse_host_port(self) -> tuple[str, int]:
        # crude parser: http://host:port
        rest = self._base_url.split("://", 1)[-1]
        host_port = rest.split("/", 1)[0]
        if ":" in host_port:
            h, p = host_port.rsplit(":", 1)
            return h, int(p)
        return host_port, 80

    async def _ensure_session(self) -> None:
        assert self._client is not None
        if self._resume:
            # Verify the resumed session still exists; if not, fall through to create.
            r = await self._client.get("/session")
            if r.status_code == 200:
                ids = [s.get("id") for s in r.json() if isinstance(s, dict)]
                if self._resume in ids:
                    self._session_id = self._resume
                    self._resume = None
                    return
            self._resume = None  # one-shot
        body: dict = {}
        if self._permissions:
            body["permission"] = self._permissions
        params: dict = {}
        if self._cwd:
            params["directory"] = self._cwd
        r = await self._client.post("/session", json=body, params=params)
        r.raise_for_status()
        self._session_id = r.json()["id"]

    # --- Event loop ---

    async def _consume_events(self) -> None:
        """Stream /event SSE forever; route to frames."""
        assert self._client is not None
        try:
            async with self._client.stream("GET", "/event", timeout=None) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    await self._dispatch(ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"opencode event stream error: {e}", exc_info=True)

    async def _dispatch(self, ev: dict) -> None:
        t = ev.get("type")
        props = ev.get("properties", {}) or {}

        # Most events scope to a session — filter.
        sid = props.get("sessionID")
        if sid and self._session_id and sid != self._session_id:
            return

        if t == "message.part.updated":
            await self._on_part_updated(props)
        elif t == "session.idle":
            if self._turn_started:
                self._turn_started = False
                await self.push_frame(LLMFullResponseEndFrame())
        elif t == "session.error":
            err = props.get("error", {}) or {}
            logger.error(f"opencode session.error: {err}")
            if self._turn_started:
                self._turn_started = False
                await self.push_frame(LLMFullResponseEndFrame())
        elif t == "permission.asked":
            await self._on_permission_asked(props)
        elif t == "permission.replied":
            # Already cleared in resolve_permission; no-op.
            pass

    async def _on_part_updated(self, props: dict) -> None:
        part = props.get("part", {}) or {}
        ptype = part.get("type")
        delta = props.get("delta")
        # Start a turn lazily on the first emission of the assistant response.
        if not self._turn_started and ptype in ("text", "reasoning", "tool"):
            self._turn_started = True
            await self.push_frame(LLMFullResponseStartFrame())

        if ptype == "text" and delta:
            await self.push_frame(TextFrame(delta))
        elif ptype == "reasoning" and delta:
            await self.push_frame(AggregatedTextFrame(text=delta, aggregated_by="thinking"))
        elif ptype == "tool":
            state = part.get("state", {}) or {}
            state_type = state.get("type") or state.get("status")
            tool_name = part.get("tool", "")
            # Only emit on the initial running state, not every subsequent update.
            if state_type in ("running", "pending"):
                hint = ""
                input_ = state.get("input") or {}
                if isinstance(input_, dict):
                    if "command" in input_:
                        cmd = str(input_["command"])
                        hint = f": {cmd[:60]}{'…' if len(cmd) > 60 else ''}"
                    elif "path" in input_:
                        hint = f": {input_['path']}"
                await self.push_frame(
                    AggregatedTextFrame(
                        text=f"▶ {tool_name}{hint}",
                        aggregated_by="tool_start",
                    )
                )

    async def _on_permission_asked(self, props: dict) -> None:
        from pipecat_mcp_server.event_bus import event_bus

        perm_id = props.get("id")
        sid = props.get("sessionID")
        if not perm_id or not sid:
            return
        async with self._perm_lock:
            self._pending_permission = (perm_id, sid)

        tool_name = props.get("permission", "tool")
        patterns = props.get("patterns") or []
        metadata = props.get("metadata") or {}
        # The PermissionBanner expects {tool_name, tool_input}; pack patterns/metadata
        # into tool_input so the user sees what's being asked.
        tool_input: dict[str, Any] = {}
        if patterns:
            tool_input["pattern"] = patterns[0] if len(patterns) == 1 else patterns
        if metadata:
            for k, v in metadata.items():
                if k not in tool_input:
                    tool_input[k] = v

        logger.info(f"opencode permission.asked: {tool_name} {patterns}")

        await self.push_frame(
            AggregatedTextFrame(
                text=f"Permission required: {tool_name} {patterns}",
                aggregated_by="permission_request",
            )
        )
        await event_bus.emit(
            "permissionRequest",
            {"tool_name": tool_name, "tool_input": tool_input},
        )

    # --- Prompt submission ---

    async def _send_prompt(self, text: str) -> None:
        assert self._client is not None and self._session_id is not None
        body: dict[str, Any] = {
            "parts": [{"type": "text", "text": text}],
        }
        if self._provider_id and self._model_id:
            body["model"] = {"providerID": self._provider_id, "modelID": self._model_id}
        try:
            r = await self._client.post(
                f"/session/{self._session_id}/prompt_async", json=body
            )
            if r.status_code not in (200, 204):
                logger.error(f"opencode prompt_async {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"opencode prompt_async error: {e}", exc_info=True)

    async def _abort(self) -> None:
        if not self._client or not self._session_id:
            return
        try:
            await self._client.post(f"/session/{self._session_id}/abort")
        except Exception as e:
            logger.warning(f"opencode abort failed: {e}")
        # Also drop any pending permission so a stale ask doesn't block the next turn.
        async with self._perm_lock:
            self._pending_permission = None

    # --- process_frame ---

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            await self._abort()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            if not self._client or not self._session_id:
                logger.warning("opencode backend not started — dropping turn")
                return
            logger.info(f"opencode ← user: {frame.text[:80]}")
            await self._send_prompt(frame.text)
            return

        await self.push_frame(frame, direction)
