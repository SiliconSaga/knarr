"""Publish into a Matrix room.

Implements Broadcaster and Commenter. Not Conversational — a room is a feed,
and DMs would be a separate publisher with its own target.
"""

from __future__ import annotations

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

    async def _send(self, payload: dict, object_type: str) -> PublishResult:
        try:
            response = await self.client.room_send(
                self.room_id, message_type="m.room.message", content=payload,
            )
        except Exception as exc:  # noqa: BLE001 — transport errors are varied
            return PublishResult(ok=False, ref=None,
                                 error=f"{type(exc).__name__}: {exc}")

        # nio RETURNS errors rather than raising them, so an unchecked send
        # reports a success that never happened.
        event_id = getattr(response, "event_id", None)
        if not event_id:
            return PublishResult(
                ok=False, ref=None,
                error=str(getattr(response, "message", response)),
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
