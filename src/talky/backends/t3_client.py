"""Minimal T3 Code orchestration client (HTTP + Effect-RPC-over-WebSocket, protocol 2).

Spike (WIN-563). Unary RPCs only — no streaming subscriptions; callers poll
``get_thread_projection``. Verified against T3 Code v0.1.0.

Auth: a scoped bearer session (``t3 auth session issue --scope orchestration:read
--scope orchestration:operate``). The token is read from an env var / file path by
the caller and is never logged here. A normal session is rejected by T3's ``/mcp``
endpoint (separate OAuth audience), so this goes over the app RPC surface instead.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
import uuid
from typing import Any

import httpx
import websockets

PROTOCOL_HEADER = {"x-t3-orchestration-protocol": "2"}
_CTRL = re.compile(r"[\x00-\x1f]")


class T3Error(RuntimeError):
    pass


class T3Client:
    def __init__(self, base_url: str, token: str):
        self._base = base_url.rstrip("/")
        self._token = token
        self._http = httpx.AsyncClient(base_url=self._base, timeout=30.0)
        self._ws: Any = None
        self._ids = itertools.count(1)
        self._pending: dict[str, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None

    def _auth(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._token}"}

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._ws:
            await self._ws.close()
        await self._http.aclose()

    # --- HTTP ---

    async def shell(self) -> dict:
        """Cross-project shell snapshot (projects + thread shells)."""
        r = await self._http.get(
            "/api/orchestration/shell", headers={**self._auth(), **PROTOCOL_HEADER}
        )
        r.raise_for_status()
        # The server emits raw control characters inside some strings; strict parsers reject it.
        data = json.loads(_CTRL.sub(" ", r.text))
        return data.get("snapshot", data)

    # --- WebSocket RPC ---

    async def _connect(self) -> None:
        r = await self._http.post("/api/auth/websocket-ticket", headers=self._auth())
        r.raise_for_status()
        ticket = r.json()["ticket"]
        ws_base = self._base.replace("http", "ws", 1)
        self._ws = await websockets.connect(
            f"{ws_base}/ws?wsTicket={ticket}&orchestrationProtocol=2", max_size=64 * 1024 * 1024
        )
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                msgs = json.loads(raw)
                for m in msgs if isinstance(msgs, list) else [msgs]:
                    tag = m.get("_tag")
                    if tag == "Ping":
                        await self._ws.send(json.dumps({"_tag": "Pong"}))
                    elif tag == "Exit":
                        fut = self._pending.pop(str(m.get("requestId")), None)
                        if fut and not fut.done():
                            exit_ = m.get("exit", {})
                            if exit_.get("_tag") == "Success":
                                fut.set_result(exit_.get("value"))
                            else:
                                fut.set_exception(T3Error(json.dumps(exit_)[:500]))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # connection dropped: fail everything in flight
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(T3Error(f"t3 socket lost: {e}"))
            self._pending.clear()
        finally:
            self._ws = None

    async def rpc(self, tag: str, payload: dict, timeout: float = 60.0) -> Any:
        if self._ws is None:
            await self._connect()
        rid = str(next(self._ids))
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._ws.send(
            json.dumps(
                {
                    "_tag": "Request",
                    "id": rid,
                    "tag": tag,
                    "payload": payload,
                    "headers": [],
                    "traceId": "0" * 32,
                    "spanId": "0" * 16,
                    "sampled": False,
                }
            )
        )
        return await asyncio.wait_for(fut, timeout)

    # --- Orchestration ---

    async def launch_thread(
        self,
        *,
        project_id: str,
        title: str,
        text: str,
        instance_id: str,
        model: str,
        runtime_mode: str = "approval-required",
        interaction_mode: str = "default",
    ) -> dict:
        return await self.rpc(
            "orchestration.launchThread",
            {
                "commandId": str(uuid.uuid4()),
                "projectId": project_id,
                "title": title,
                "modelSelection": {"instanceId": instance_id, "model": model},
                "runtimeMode": runtime_mode,
                "interactionMode": interaction_mode,
                "workspaceStrategy": {"type": "root"},
                "initialMessage": {"text": text, "attachments": []},
            },
        )

    async def send_message(self, thread_id: str, text: str) -> dict:
        return await self.rpc(
            "orchestration.dispatchCommand",
            {
                "type": "message.dispatch",
                "createdBy": "user",
                "creationSource": "web",
                "commandId": str(uuid.uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid.uuid4()),
                "text": text,
                "attachments": [],
                "deliveryIntent": "auto",
                "dispatchMode": {"type": "start_immediately"},
            },
        )

    async def interrupt(self, thread_id: str) -> None:
        """Interrupt the thread's active run (the stop command is not accepted from clients)."""
        p = await self.thread_projection(thread_id)
        active = [r for r in p["runs"] if r["status"] in ("preparing", "queued", "starting", "running", "waiting")]
        for r in active:
            await self.rpc(
                "orchestration.dispatchCommand",
                {"type": "run.interrupt", "commandId": str(uuid.uuid4()), "threadId": thread_id, "runId": r["id"]},
            )

    async def thread_projection(self, thread_id: str) -> dict:
        return await self.rpc("orchestration.getThreadProjection", {"threadId": thread_id})
