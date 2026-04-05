"""Kafka event envelope schemas for Knarr watchers."""

import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Source:
    platform: str
    channel: str
    community: str


@dataclass
class Content:
    type: str
    body: str
    url: Optional[str] = None


@dataclass
class WatchAlert:
    source: Source
    content: Content
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_kafka_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_kafka_dict(cls, data: dict) -> "WatchAlert":
        return cls(
            source=Source(**data["source"]),
            content=Content(**data["content"]),
            event_id=data["event_id"],
            timestamp=data["timestamp"],
        )
