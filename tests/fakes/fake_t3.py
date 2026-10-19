"""Fake T3 Code server for the test rig (WIN-574). Never touches a real T3.

    .venv/bin/python tests/fakes/fake_t3.py --port 18782 [--fixture tests/fakes/t3_shell.json]

Speaks just enough of T3's orchestration surface for ``talky.backends.t3_client``:
shell snapshot (HTTP), websocket ticket, and the Effect-RPC-over-WS tags the client
uses. Any ``Authorization: Bearer fake-...`` is accepted; anything else is 401.
``GET /__log`` lists every RPC with its threadId (proof of which thread got a message).
``POST /__stop`` exits.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import WSMsgType, web

PROTOCOL = "2"
DEFAULT_FIXTURE = Path(__file__).with_name("t3_shell.json")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class State:
    def __init__(self, fixture: Path):
        data = json.loads(fixture.read_text())
        self.projects = data["projects"]
        self.threads = data["threads"]
        self.messages = data["messages"]
        self.seq = int(data.get("snapshotSequence", 1))
        self.log: list[dict] = []
        self.runs: dict[str, list[dict]] = {}

    def snapshot(self) -> dict:
        return {"projects": self.projects, "threads": self.threads, "snapshotSequence": self.seq}

    def thread(self, thread_id: str) -> dict | None:
        return next((t for t in self.threads if t["id"] == thread_id), None)

    def append_turn(self, thread_id: str, text: str) -> None:
        run = f"run-{uuid.uuid4().hex[:8]}"
        now = _now()
        msgs = self.messages.setdefault(thread_id, [])
        msgs.append({"id": str(uuid.uuid4()), "role": "user", "text": text, "createdAt": now, "runId": run})
        reply = {"id": str(uuid.uuid4()), "role": "assistant", "text": f"ack: {text}", "createdAt": now, "runId": run}
        runs = self.runs.setdefault(thread_id, [])
        run_rec = {"id": run, "status": "completed"}
        # FAKE_T3_REPLY_DELAY_FILE (WIN-582): seconds a reply takes, read per turn, so a drive can
        # switch chats while a T3 turn is still in flight.
        try:
            delay = float(Path(os.environ["FAKE_T3_REPLY_DELAY_FILE"]).read_text().strip() or 0)
        except (KeyError, OSError, ValueError):
            delay = 0.0
        if delay > 0:
            run_rec["status"] = "running"

            def finish() -> None:
                msgs.append(reply)
                run_rec["status"] = "completed"

            asyncio.get_running_loop().call_later(delay, finish)
        else:
            msgs.append(reply)
        runs.append(run_rec)
        t = self.thread(thread_id)
        if t:
            t["updatedAt"] = now
        self.seq += 1



def _authed(request: web.Request) -> bool:
    return re.fullmatch(r"Bearer fake-\S+", request.headers.get("Authorization", "")) is not None


def _raw_json(obj) -> str:
    """JSON with control chars left raw inside strings, as the real server emits them."""
    return re.sub(r"\\u00([01][0-9a-f])", lambda m: chr(int(m.group(1), 16)), json.dumps(obj))


async def h_root(request: web.Request) -> web.Response:
    return web.Response(text="fake t3")


async def h_shell(request: web.Request) -> web.Response:
    if not _authed(request):
        return web.Response(status=401, text="unauthorized")
    if request.headers.get("x-t3-orchestration-protocol") != PROTOCOL:
        return web.Response(status=400, text="missing x-t3-orchestration-protocol: 2")
    st: State = request.app["state"]
    return web.Response(text=_raw_json({"snapshot": st.snapshot()}), content_type="application/json")


async def h_ticket(request: web.Request) -> web.Response:
    if not _authed(request):
        return web.Response(status=401, text="unauthorized")
    return web.json_response({"ticket": "t"})


async def h_log(request: web.Request) -> web.Response:
    return web.json_response(request.app["state"].log)


async def h_stop(request: web.Request) -> web.Response:
    asyncio.get_running_loop().call_later(0.1, request.app["stop"].set)
    return web.json_response({"stopping": True})


def _rpc(st: State, tag: str, payload: dict):
    thread_id = payload.get("threadId")
    typ = payload.get("type")
    st.log.append({"tag": tag, "threadId": thread_id, "text": payload.get("text"), "type": typ})
    if tag == "orchestration.getThreadProjection":
        return {"runs": st.runs.get(thread_id, []), "messages": st.messages.get(thread_id, [])}
    if tag == "orchestration.launchThread":
        tid = f"thread:{payload['projectId'].split(':', 1)[-1]}:{uuid.uuid4()}:{uuid.uuid4()}"
        st.threads.append(
            {
                "id": tid, "projectId": payload["projectId"], "title": payload.get("title") or "(untitled)",
                "status": "completed", "activeRunId": None, "activityRunStatus": "completed",
                "createdAt": _now(), "updatedAt": _now(),
                "modelSelection": payload.get("modelSelection") or {},
                "runtimeMode": payload.get("runtimeMode") or "", "archivedAt": None, "deletedAt": None,
            }
        )
        st.log[-1]["threadId"] = tid
        st.append_turn(tid, (payload.get("initialMessage") or {}).get("text", ""))
        return {"threadId": tid}
    if tag == "orchestration.dispatchCommand":
        if typ == "message.dispatch":
            st.append_turn(thread_id, payload.get("text", ""))
            return {}
        if typ == "run.interrupt":
            return {}
    raise ValueError(f"unsupported rpc {tag} {typ}")


async def h_ws(request: web.Request) -> web.WebSocketResponse:
    if request.query.get("wsTicket") != "t" or request.query.get("orchestrationProtocol") != PROTOCOL:
        return web.Response(status=401, text="bad ticket")
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    st: State = request.app["state"]

    async def pinger():
        while True:
            await asyncio.sleep(15)
            await ws.send_str(json.dumps([{"_tag": "Ping"}]))

    ping = asyncio.create_task(pinger())
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if m.get("_tag") != "Request":
                continue  # Pong etc.
            try:
                value = _rpc(st, m["tag"], m.get("payload") or {})
                exit_ = {"_tag": "Success", "value": value}
            except Exception as e:  # noqa: BLE001
                exit_ = {"_tag": "Failure", "cause": str(e)}
            await ws.send_str(json.dumps([{"_tag": "Exit", "requestId": m["id"], "exit": exit_}]))
    finally:
        ping.cancel()
    return ws


async def main(port: int, fixture: Path) -> None:
    app = web.Application()
    app["state"] = State(fixture)
    app["stop"] = asyncio.Event()
    app.add_routes(
        [
            web.get("/", h_root),
            web.get("/api/orchestration/shell", h_shell),
            web.post("/api/auth/websocket-ticket", h_ticket),
            web.get("/ws", h_ws),
            web.get("/__log", h_log),
            web.post("/__stop", h_stop),
        ]
    )
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    print(f"fake t3 listening on 127.0.0.1:{port}", flush=True)
    await app["stop"].wait()
    await runner.cleanup()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    a = ap.parse_args()
    asyncio.run(main(a.port, a.fixture))
