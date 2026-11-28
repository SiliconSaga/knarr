"""Data types for the outbound publish path.

Mirrors src/watchers/schemas.py on the inbound side. Content is reused
verbatim rather than redefined: a thing you publish and a thing you watched
have the same shape, and duplicating it would guarantee they drift.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from src.watchers.schemas import Content


@dataclass(frozen=True)
class ObjectRef:
    """What a platform called something we created.

    Composite on purpose. A bare object id cannot select the adapter or the
    credentials a later reply needs, and two Pages can produce
    indistinguishable comment ids.
    """
    publisher_id: str
    platform: str
    surface: str
    object_type: str      # post | comment | message | media
    object_id: str


@dataclass
class PublishResult:
    ok: bool
    ref: ObjectRef | None
    error: str | None


@dataclass
class Violation:
    """A reason a target cannot be published as written."""
    publisher_id: str
    reason: str


@dataclass
class MediaRef:
    """A durable reference, NOT a fetchable URL.

    A signed URL written into a replayable record is dead by the time the
    record is replayed. Publishers mint a URL per delivery attempt from this.
    """
    kind: str             # garage-object | public-url
    locator: str
    content_type: str
    checksum: str = ""


@dataclass
class Target:
    publisher_id: str
    verb: str                        # post | comment | message
    content: Content
    parent: ObjectRef | None = None  # required for comment/message
    media: MediaRef | None = None


@dataclass
class PublishRequest:
    request_id: str
    scope: str
    targets: list[Target]
    origin: str                      # git-merge | cli | matrix
    approved_by: str
    approved_at: str
    media: MediaRef | None = None
    # Who asked, e.g. "borgr/hit-list". Recorded with the result; it never
    # changes routing and is not part of the request id.
    source: str | None = None

    def __post_init__(self) -> None:
        if self.source is not None:
            validate_source(self.source)


# Lowercase path segments, e.g. "borgr/hit-list". Narrow on purpose: the tag
# lands in result records and log lines, so no spaces or control characters.
_SOURCE_RE = re.compile(r"[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*")
_SOURCE_MAX = 64


def validate_source(source: str) -> None:
    """Raise ValueError unless ``source`` is a well-formed source tag."""
    if len(source) > _SOURCE_MAX or not _SOURCE_RE.fullmatch(source):
        raise ValueError(
            f"source {source!r} must be lowercase segments separated by '/', "
            f"e.g. 'borgr/hit-list', at most {_SOURCE_MAX} characters"
        )


def content_digest(content: Content) -> str:
    """Stable digest of a Content payload."""
    blob = json.dumps(asdict(content), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def derive_request_id(scope: str, targets: list[Target], digest: str) -> str:
    """Deterministic id derived from immutable input.

    Never generated fresh on retry: the whole point is that the same input
    yields the same id, so a retry can look up what already succeeded instead
    of republishing it. Target order is sorted out because order carries no
    meaning — leaving it in would make a re-resolved request look new.
    """
    # The parent is part of identity: a reply to one post is not a retry of a
    # reply to another. "-" marks no parent, which no object id can equal.
    parts = sorted(
        f"{t.publisher_id}:{t.verb}:"
        + (f"{t.parent.platform}/{t.parent.surface}/{t.parent.object_id}"
           if t.parent else "-")
        for t in targets
    )
    blob = json.dumps(
        {"scope": scope, "targets": parts, "digest": digest},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
