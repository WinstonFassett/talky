"""Devin backend for Talky — REST-polling adapter for Devin's cloud agent API.

Devin's public API is REST-only (no WebSocket / SSE). Session lifecycle:

  POST /v1/organizations/{org}/sessions                  -> create
  POST /v1/organizations/{org}/sessions/{id}/messages    -> send prompt
  GET  /v1/organizations/{org}/sessions/{id}/messages    -> poll with cursor
  DELETE /v1/organizations/{org}/sessions/{id}           -> terminate

So this backend is a polling adapter: after each user turn we POST the message,
then long-poll the messages list with a cursor and stream assistant messages
into the Pipecat pipeline as TextFrames.

Barge-in semantics (voice reality vs Devin API):
  - Devin has no per-turn cancel — DELETE terminates the whole session.
  - InterruptionFrame stops our local poll for the in-flight turn and drops
    any buffered assistant text so TTS shuts up. Devin keeps working on the
    server side; when the next UserTurnTextFrame arrives we POST it and Devin
    auto-resumes into the new instruction. This is the same "steer, don't
    kill" shape that HermesLLMService uses.

Config (llm-backends.yaml):
  org_id:       Devin organization id (env: DEVIN_ORG_ID)
  api_key:      Bearer token (env: DEVIN_API_KEY, prefix cog_)
  base_url:     API root (default https://api.devin.ai, env: DEVIN_BASE_URL)
  snapshot_id:  Optional environment snapshot for new sessions
  poll_interval: Seconds between message polls (default 1.5)
  session_id:   Reuse an existing session instead of creating one

Auth: `Authorization: Bearer <api_key>`. Service-user tokens are `cog_`-prefixed.
"""

import asyncio
import os
import time
from typing import TYPE_CHECKING, Optional

import httpx

if TYPE_CHECKING:
    from talky.backends import BackendStatus

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

from talky.config.voice_prompts import format_voice_message
from talky.server.turn import UserTurnTextFrame


DEFAULT_BASE_URL = "https://api.devin.ai"
DEFAULT_POLL_INTERVAL = 1.5


class DevinLLMService(LLMService):
    """Devin cloud agent as a Pipecat LLM backend (REST + polling)."""

    @staticmethod
    def status() -> tuple["BackendStatus", str]:
        """Backend Status — see UBIQUITOUS_LANGUAGE.md.

        Cheap pre-construction check: are DEVIN_API_KEY and DEVIN_ORG_ID set?
        No network call. Missing values are user-fixable — Misconfigured.
        """
        from talky.backends import BackendStatus
        if not os.getenv("DEVIN_API_KEY"):
            return BackendStatus.MISCONFIGURED, "DEVIN_API_KEY not set"
        if not os.getenv("DEVIN_ORG_ID"):
            return BackendStatus.MISCONFIGURED, "DEVIN_ORG_ID not set"
        return BackendStatus.READY, ""

    def __init__(
        self,
        *,
        org_id: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        snapshot_id: Optional[str] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        session_id: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._org_id = org_id or os.getenv("DEVIN_ORG_ID")
        self._api_key = api_key or os.getenv("DEVIN_API_KEY")
        self._base_url = (base_url or os.getenv("DEVIN_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._snapshot_id = snapshot_id
        self._poll_interval = float(poll_interval)
        self._session_id: Optional[str] = session_id

        self._client: Optional[httpx.AsyncClient] = None
        self._interrupt = asyncio.Event()
        self._turn_lock = asyncio.Lock()

        logger.info(f"✅ DevinLLMService initialized (org={self._org_id}, base={self._base_url})")

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            if not self._api_key or not self._org_id:
                raise RuntimeError("Devin backend requires DEVIN_API_KEY and DEVIN_ORG_ID")
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    def _sessions_path(self) -> str:
        return f"/v1/organizations/{self._org_id}/sessions"

    async def _create_session(self, first_prompt: str) -> str:
        body: dict = {"name": f"talky-voice-{int(time.time())}"}
        if self._snapshot_id:
            body["snapshot_id"] = self._snapshot_id
        # Devin accepts an initial prompt at creation on some org tiers; we
        # send it separately via /messages to keep the flow uniform.
        client = self._ensure_client()
        r = await client.post(self._sessions_path(), json=body)
        r.raise_for_status()
        data = r.json()
        sid = data.get("id") or data.get("session_id")
        if not sid:
            raise RuntimeError(f"Devin session create returned no id: {data}")
        logger.info(f"🌱 Created Devin session {sid}")
        return sid

    async def _post_message(self, text: str) -> None:
        client = self._ensure_client()
        url = f"{self._sessions_path()}/{self._session_id}/messages"
        r = await client.post(url, json={"message": text})
        r.raise_for_status()

    async def _poll_messages(self, cursor: Optional[str]) -> tuple[list[dict], Optional[str], bool]:
        """Return (new_messages, next_cursor, session_done)."""
        client = self._ensure_client()
        url = f"{self._sessions_path()}/{self._session_id}/messages"
        params: dict = {"limit": 50}
        if cursor:
            params["cursor"] = cursor
        r = await client.get(url, params=params)
        r.raise_for_status()
        payload = r.json()
        # Devin's message list shape isn't perfectly stable across docs
        # revisions; be tolerant.
        msgs = payload.get("messages") or payload.get("data") or []
        next_cursor = payload.get("next_cursor") or payload.get("cursor")
        # A session is "settled" when Devin reports status running -> idle/completed.
        session_status = payload.get("session_status")
        done = session_status in {"idle", "completed", "blocked", "stopped"}
        return msgs, next_cursor, done

    async def _session_status(self) -> str:
        client = self._ensure_client()
        r = await client.get(f"{self._sessions_path()}/{self._session_id}")
        r.raise_for_status()
        return r.json().get("status", "unknown")

    # ------------------------------------------------------------------
    # Turn processing
    # ------------------------------------------------------------------

    async def _process_user_text(self, user_text: str) -> None:
        # Serialize turns so a fast re-prompt can't race a still-draining poll.
        async with self._turn_lock:
            self._interrupt.clear()
            await self.push_frame(LLMFullResponseStartFrame())
            try:
                if not self._session_id:
                    self._session_id = await self._create_session(user_text)

                prompt = format_voice_message(user_text)
                logger.info(f"🗣️  User → Devin: {user_text[:100]}")
                await self._post_message(prompt)

                cursor: Optional[str] = None
                # We only want assistant messages that arrived *after* our
                # prompt. First poll establishes the baseline cursor; from
                # there anything new is Devin's reply.
                baseline_msgs, cursor, _ = await self._poll_messages(cursor)
                seen_ids = {m.get("id") for m in baseline_msgs if m.get("id")}

                idle_polls = 0
                while not self._interrupt.is_set():
                    await asyncio.sleep(self._poll_interval)
                    if self._interrupt.is_set():
                        break
                    try:
                        msgs, cursor, done = await self._poll_messages(cursor)
                    except httpx.HTTPError as e:
                        logger.warning(f"Devin poll error: {e}")
                        continue

                    new_assistant_text = []
                    for m in msgs:
                        mid = m.get("id")
                        if mid and mid in seen_ids:
                            continue
                        if mid:
                            seen_ids.add(mid)
                        role = m.get("role") or m.get("sender") or ""
                        if role not in ("assistant", "devin", "agent"):
                            continue
                        # Devin messages are often {content: str} or
                        # {content: [{type: text, text: ...}]}.
                        content = m.get("content") or m.get("message") or ""
                        if isinstance(content, list):
                            for chunk in content:
                                if isinstance(chunk, dict) and chunk.get("type") == "text":
                                    new_assistant_text.append(chunk.get("text", ""))
                        elif isinstance(content, str) and content:
                            new_assistant_text.append(content)

                    for piece in new_assistant_text:
                        if piece:
                            await self.push_frame(TextFrame(piece))

                    if new_assistant_text:
                        idle_polls = 0
                    else:
                        idle_polls += 1

                    if done or idle_polls >= 20:  # ~30s of quiet -> assume turn over
                        break

            except Exception as e:
                logger.error(f"Devin turn error: {e}", exc_info=True)
                await self.push_frame(TextFrame("Sorry, I hit an error talking to Devin."))
            finally:
                await self.push_frame(LLMFullResponseEndFrame())

    # ------------------------------------------------------------------
    # Pipecat plumbing
    # ------------------------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            # No server-side cancel primitive; stop local streaming so TTS
            # goes quiet. Devin keeps thinking on its side and the next
            # UserTurnTextFrame steers it via a follow-up message.
            self._interrupt.set()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserTurnTextFrame):
            await self._process_user_text(frame.text)
            return

        await self.push_frame(frame, direction)

    async def stop(self, frame):
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
        if hasattr(super(), "stop"):
            await super().stop(frame)
