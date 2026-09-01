"""Publish to Discord THROUGH the existing mautrix-discord bridge.

Deliberately not a native Discord client. The design asks Phase A to try the
bridge first and record what it cannot express, so:

WHAT THIS CANNOT DO, and why a native adapter may eventually be worth it:
  - No embeds. The bridge relays a Matrix message; Discord embeds have no
    Matrix equivalent that survives the trip.
  - No threads or DMs — hence `post` only. Discord threads are not reachable
    by sending into the bridged room.
  - No per-message username/avatar override (webhook-style relay).
  - Edits and deletes are not exposed here even though Discord supports both.
  - Delivery is confirmed by MATRIX, not by Discord. A successful result means
    the bridge accepted it; if the bridge is logged out, this reports success
    for a message Discord never saw. That is the sharpest limitation and the
    strongest argument for a native adapter later.

WHAT IT BUYS: no second credential, no new dependency, and it works today.
"""

from __future__ import annotations

from src.publisher.adapters.matrix import MatrixPublisher
from src.publisher.protocols import Constraints
from src.publisher.schemas import MediaRef, ObjectRef, PublishResult, Violation
from src.watchers.schemas import Content

# Discord's per-message ceiling. The bridge relays rather than raises it, so
# an over-long message is truncated or dropped downstream where we cannot see.
_DISCORD_MAX_CHARS = 2000


class DiscordPublisher:
    platform = "discord"
    surface = "channel"

    def __init__(self, id: str, scope: str, room_id: str, client):
        self.id = id
        self.scope = scope
        self.room_id = room_id
        self.constraints = Constraints(max_length=_DISCORD_MAX_CHARS)
        # Composition, not inheritance: this IS a Matrix send, but it is not a
        # Matrix destination, and the ObjectRef must say Discord or a later
        # reply cannot be routed back to the right place.
        self._transport = MatrixPublisher(
            id=id, scope=scope, room_id=room_id, client=client,
        )

    def validate(self, verb: str, content: Content,
                 media: MediaRef | None = None) -> list[Violation]:
        violations = self._transport.validate(verb, content, media)
        length = len(content.title) + len(content.body)
        if length > _DISCORD_MAX_CHARS:
            violations.append(Violation(
                publisher_id=self.id,
                reason=f"{length} characters exceeds Discord's "
                       f"{_DISCORD_MAX_CHARS}-character limit",
            ))
        return violations

    async def post(self, content: Content,
                   media: MediaRef | None = None) -> PublishResult:
        result = await self._transport.post(content, media)
        if not result.ok or result.ref is None:
            return result
        return PublishResult(
            ok=True,
            ref=ObjectRef(
                publisher_id=self.id,
                platform=self.platform,
                surface=self.surface,
                object_type="post",
                object_id=result.ref.object_id,   # the Matrix event id
            ),
            error=None,
        )
