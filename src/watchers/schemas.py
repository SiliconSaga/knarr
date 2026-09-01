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
    platform: str                    # "reddit" | "github" | "discord" | ...
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
        """Reconstruct a WatchAlert from a Kafka message dict.

        Strict by design: assumes the current envelope shape and raises
        KeyError on a missing field. We deliberately do NOT fabricate
        defaults for absent fields — that would mask a corrupt or
        legacy (pre-WatcherInstance `Source` envelope) message. The
        router's consumer (src/router/kafka_consumer.py, Task 8) wraps
        this in try/except and skips messages that fail to decode.
        """
        def _str(mapping: dict, key: str, where: str) -> str:
            """Require a string. Type-checking the NESTED scalars matters.

            A missing key already raises KeyError, which the router catches.
            A key present with the wrong type did not: `content.title` as a
            list sailed through here and then blew up in `"\\n".join(...)`
            during formatting — outside the deserialize guard, so it killed
            the consumer loop instead of skipping one record.
            """
            value = mapping[key]
            if not isinstance(value, str):
                raise TypeError(
                    f"{where}.{key} must be a string, "
                    f"got {type(value).__name__}"
                )
            return value

        content_raw = data["content"]
        if not isinstance(content_raw, dict):
            raise TypeError(
                f"content must be an object, got {type(content_raw).__name__}"
            )
        attachments_raw = content_raw.get("attachments", [])
        if not isinstance(attachments_raw, list):
            raise TypeError(
                f"content.attachments must be a list, "
                f"got {type(attachments_raw).__name__}"
            )

        content = Content(
            type=_str(content_raw, "type", "content"),
            title=_str(content_raw, "title", "content"),
            body=_str(content_raw, "body", "content"),
            author=_str(content_raw, "author", "content"),
            attachments=[Attachment(**a) for a in attachments_raw],
        )
        return cls(
            event_id=_str(data, "event_id", "alert"),
            instance_id=_str(data, "instance_id", "alert"),
            scope=_str(data, "scope", "alert"),
            access_path=_str(data, "access_path", "alert"),
            platform=_str(data, "platform", "alert"),
            raw_post_ref=_str(data, "raw_post_ref", "alert"),
            content=content,
            timestamp=_str(data, "timestamp", "alert"),
            extracted_at=_str(data, "extracted_at", "alert"),
        )
