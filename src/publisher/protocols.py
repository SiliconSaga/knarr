"""Capability protocols for outbound destinations.

One interface with every verb on it would promise that every destination can
do everything, and a `capabilities:` list in YAML would drift from the code
the first time an adapter changed. So a publisher implements only the
protocols its surface supports, and its capabilities are READ OFF the class.

The verbs are deliberately three, not two. Responding to a feed comment and
sending into a conversation obey different rules — the second has delivery
windows — and one word for both would hide exactly the constraint that
matters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.publisher.schemas import MediaRef, ObjectRef, PublishResult, Violation
from src.watchers.schemas import Content


@dataclass
class Constraints:
    """What a destination will refuse, expressed so validate() can check it."""
    media_required: bool = False
    media_formats: tuple[str, ...] = ()      # empty means "any"
    max_length: int | None = None
    send_window_hours: int | None = None     # None means "no window"


class Publisher(Protocol):
    """Every publisher, regardless of what it can do."""
    id: str
    scope: str
    platform: str
    surface: str
    constraints: Constraints

    def validate(
        self, verb: str, content: Content, media: MediaRef | None = None
    ) -> list[Violation]:
        """Local, pure, no network. Fetchability belongs in preflight()."""
        ...


@runtime_checkable
class Broadcaster(Protocol):
    async def post(
        self, content: Content, media: MediaRef | None = None
    ) -> PublishResult: ...


@runtime_checkable
class Commenter(Protocol):
    async def comment(
        self, parent: ObjectRef, content: Content, media: MediaRef | None = None
    ) -> PublishResult: ...


@runtime_checkable
class Conversational(Protocol):
    async def message(
        self, thread: ObjectRef, content: Content, media: MediaRef | None = None
    ) -> PublishResult: ...


_VERB_METHODS = {
    "post": "post",
    "comment": "comment",
    "message": "message",
}


def capabilities_of(publisher: object) -> frozenset[str]:
    """Which verbs this publisher actually implements.

    Derived from the object rather than declared beside it, so the answer
    cannot disagree with the code.
    """
    return frozenset(
        verb for verb, method in _VERB_METHODS.items()
        if callable(getattr(publisher, method, None))
    )
