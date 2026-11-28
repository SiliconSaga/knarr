"""Publish into a Matrix room.

Implements Broadcaster and Commenter. Not Conversational — a room is a feed,
and DMs would be a separate publisher with its own target.
"""

from __future__ import annotations

import asyncio
import logging
from html import escape

from src.publisher.protocols import Constraints
from src.publisher.schemas import MediaRef, ObjectRef, PublishResult, Violation
from src.watchers.schemas import Content

logger = logging.getLogger(__name__)


class MatrixPublisher:
    platform = "matrix"
    surface = "room"

    def __init__(self, id: str, scope: str, room_id: str, client):
        self.id = id
        self.scope = scope
        self.room_id = room_id
        self.client = client
        self.constraints = Constraints()   # Matrix imposes none of ours
        self._resolved_room_id: str | None = None
        self._joined = False

    def validate(self, verb: str, content: Content,
                 media: MediaRef | None = None) -> list[Violation]:
        """Pure and local — no network."""
        if not content.body.strip() and not content.title.strip():
            return [Violation(
                publisher_id=self.id,
                reason="nothing to send: title and body are both empty",
            )]
        return []

    def _payload(self, content: Content) -> dict:
        text = "\n".join(p for p in (content.title, content.body) if p)
        # formatted_body is escaped separately rather than derived from the
        # plain text. Deriving it is how the router shipped unescaped
        # attacker-controlled HTML into every operator's client.
        html = "<br>".join(escape(p).replace("\n", "<br>")
                           for p in (content.title, content.body) if p)
        return {
            "msgtype": "m.text",
            "body": text,
            "format": "org.matrix.custom.html",
            "formatted_body": html,
        }

    async def _resolve_room(self) -> str:
        """Turn a #alias into a !room_id, once, and remember it.

        Config names rooms by alias because an alias is portable across
        rebuilds and a room id is not — the router still carries a hardcoded
        one, which is why it broke every time the cluster was rebuilt.
        """
        if not self.room_id.startswith("#"):
            return self.room_id
        if self._resolved_room_id is None:
            resolved = await asyncio.to_thread(
                self.client.resolve_alias, self.room_id,
            )
            if not resolved:
                raise LookupError(f"alias {self.room_id} does not resolve")
            self._resolved_room_id = resolved
        return self._resolved_room_id

    async def _ensure_joined(self, room_id: str) -> None:
        """Join before sending, once per process.

        The reconciler INVITES the publishing user; it does not accept on its
        behalf, and an invited-but-unjoined user gets 403 on every send. The
        router hit exactly this and reported phantom successes for a week.
        Joining here makes a publisher self-sufficient after a cluster
        rebuild instead of needing someone to accept an invite by hand.

        Idempotent — joining a room you are already in succeeds.
        """
        if self._joined:
            return
        await asyncio.to_thread(self.client.join_room, room_id)
        self._joined = True

    async def _send(self, payload: dict, object_type: str) -> PublishResult:
        try:
            room_id = await self._resolve_room()
            await self._ensure_joined(room_id)
            # The client is synchronous httpx; running it on a worker thread
            # keeps a slow homeserver from stalling the other targets in the
            # same fan-out.
            event_id = await asyncio.to_thread(
                self.client.send_event, room_id, payload,
            )
        except Exception as exc:  # noqa: BLE001 — transport errors are varied
            return PublishResult(ok=False, ref=None,
                                 error=f"{type(exc).__name__}: {exc}")

        # Never report a success without the id that proves it. A publisher
        # that claims a post nothing can find is the silent-success bug this
        # codebase keeps producing, pointed outward.
        if not event_id:
            return PublishResult(
                ok=False, ref=None,
                error="homeserver returned no event_id",
            )

        return PublishResult(
            ok=True,
            ref=ObjectRef(publisher_id=self.id, platform=self.platform,
                          surface=self.surface, object_type=object_type,
                          object_id=event_id),
            error=None,
        )

    async def post(self, content: Content,
                   media: MediaRef | None = None) -> PublishResult:
        return await self._send(self._payload(content), "post")

    async def comment(self, parent: ObjectRef, content: Content,
                      media: MediaRef | None = None) -> PublishResult:
        payload = self._payload(content)
        payload["m.relates_to"] = {
            "rel_type": "m.thread",
            "event_id": parent.object_id,
        }
        return await self._send(payload, "comment")
