"""Kafka consumer that reads watcher alerts and formats them for Matrix."""

import json
import logging

from ..watchers.schemas import WatchAlert

logger = logging.getLogger(__name__)

PLATFORM_EMOJI = {
    "reddit": "\U0001f4e2",
    "github": "\U0001f4bb",
    "steam": "\U0001f3ae",
    "twitter": "\U0001f426",
    "youtube": "\U0001f3ac",
}


def format_alert_message(alert: WatchAlert) -> str:
    """Format a WatchAlert into a human-readable Matrix message."""
    emoji = PLATFORM_EMOJI.get(alert.source.platform, "\U0001f514")
    platform = alert.source.platform.capitalize()

    lines = [
        f"{emoji} **{platform}** \u2014 {alert.source.channel}",
        f"{alert.content.body}",
    ]
    if alert.content.url:
        lines.append(f"\U0001f517 {alert.content.url}")

    return "\n".join(lines)


def deserialize_alert(raw: bytes) -> WatchAlert | None:
    """Deserialize a Kafka message value into a WatchAlert."""
    try:
        data = json.loads(raw)
        return WatchAlert.from_kafka_dict(data)
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to deserialize alert: %s", e)
        return None
