"""Router-side Kafka consumer helpers.

Phase 1: thin pass-through — read one event, format one Matrix message,
post it. Phase 2 introduces presentation_mode (summarised vs raw-threaded)
along with the AI summarize stage.

Phase 1 preserves today's user-visible behaviour as closely as the new
envelope allows: emoji + bold platform header, full message body, link
on its own line with the link emoji. The legacy `source.channel`
sub-header (e.g. `r/Terasology`, `MovingBlocks/Terasology`) becomes
the instance_id (e.g. `reddit-terasology`, `github-terasology`) because
the new envelope dropped the `Source` dataclass — the routing identity
is now scope + instance_id, not platform + channel.
"""

import json
import logging

from src.watchers.schemas import WatchAlert

logger = logging.getLogger(__name__)

PLATFORM_EMOJI = {
    "reddit": "\U0001f4e2",
    "github": "\U0001f4bb",
    "steam": "\U0001f3ae",
    "twitter": "\U0001f426",
    "youtube": "\U0001f3ac",
    "bluesky": "\U0001f98b",
    "facebook": "\U0001f4d8",
    "nextdoor": "\U0001f3d8️",
}


def deserialize_alert(raw: bytes) -> WatchAlert | None:
    """Deserialize a Kafka message value into a WatchAlert.

    Accepts the raw bytes that `msg.value()` hands the consumer in
    src/router/main.py; JSON-decodes internally; returns None on
    malformed input so the consumer loop can log + continue rather
    than crash on one bad event.
    """
    try:
        data = json.loads(raw)
        return WatchAlert.from_kafka_dict(data)
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to deserialize alert: %s", e)
        return None


def format_alert_message(alert: WatchAlert) -> str:
    """Format a WatchAlert as a single Matrix message (Phase 1 behaviour).

    Preserves today's shape: emoji + bold platform header with the
    instance id as the sub-identifier (legacy `source.channel`
    equivalent), full message body, link on its own line with the link
    emoji. Body is not truncated — Phase 2's `summarized` presentation
    mode is where headline trimming arrives.
    """
    emoji = PLATFORM_EMOJI.get(alert.platform, "\U0001f514")
    platform = alert.platform.capitalize()

    lines = [
        f"{emoji} **{platform}** — {alert.instance_id}",
        f"{alert.content.body}",
    ]
    if alert.raw_post_ref:
        lines.append(f"\U0001f517 {alert.raw_post_ref}")
    return "\n".join(lines)
