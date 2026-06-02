"""Kafka event envelope schemas for Knarr watchers.

Schema corresponds to the `knarr.watch.alerts` topic. See
docs/plans/2026-05-30-knarr-source-identity-design.md § Kafka schemas.
"""

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime


@dataclass
class Attachment:
    """A non-text resource attached to a post (image, video, file, etc.)."""
    url: str
    mime_hint: str = ""   # best-guess; may be empty if upstream didn't say


@dataclass
class Content:
    """The human-facing payload of a watched event."""
    type: str                        # "post" | "comment" | "release" | "reaction" | ...
    title: str                       # platform's title; may be empty
    body: str                        # full text
    author: str                      # platform-handle (best-effort)
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class WatchAlert:
    """A single event emitted by a WatcherInstance to Kafka.

    Carries enough context for the router to make presentation/routing
    decisions and to link back to the platform original.
    """
    event_id: str                    # platform-stable, used for dedupe
    instance_id: str                 # which WatcherInstance produced this
    scope: str                       # identity scope ("community/terasology", "user/cervator", etc.)
    access_path: str                 # how it was sourced ("api", "scrape", ...)
    platform: str
    raw_post_ref: str                # canonical URL on the source platform
    content: Content
    timestamp: str                   # when the event was created upstream (ISO 8601)
    extracted_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def to_kafka_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_kafka_dict(cls, data: dict) -> "WatchAlert":
        content_raw = data["content"]
        content = Content(
            type=content_raw["type"],
            title=content_raw["title"],
            body=content_raw["body"],
            author=content_raw["author"],
            attachments=[
                Attachment(**a) for a in content_raw.get("attachments", [])
            ],
        )
        return cls(
            event_id=data["event_id"],
            instance_id=data["instance_id"],
            scope=data["scope"],
            access_path=data["access_path"],
            platform=data["platform"],
            raw_post_ref=data["raw_post_ref"],
            content=content,
            timestamp=data["timestamp"],
            extracted_at=data["extracted_at"],
        )
