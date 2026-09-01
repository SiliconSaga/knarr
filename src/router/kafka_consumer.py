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
    except (ValueError, KeyError, TypeError) as e:
        # ValueError covers json.JSONDecodeError AND UnicodeDecodeError —
        # invalid UTF-8 (b"\xff") raises the latter, which the original
        # narrower catch let through and which would kill the consumer loop.
        # TypeError covers a payload that is valid JSON of the wrong SHAPE:
        # b"[]" decodes fine, then data["content"] subscripts a list.
        # One malformed record must never take the router down.
        logger.warning("Failed to deserialize alert: %s: %s", type(e).__name__, e)
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


def format_alert_html(alert: WatchAlert) -> str:
    """Format a WatchAlert as Matrix `formatted_body` — HTML, escaped.

    Separate from format_alert_message because the two outputs have
    different rules and only one of them is markup. The router previously
    derived formatted_body by running `.replace("\\n", "<br>")` over the
    plain text, which pushed **attacker-controlled content straight into
    an HTML field**: alert bodies come from Reddit and GitHub, so a post
    containing markup was rendered as markup in every operator's client.

    Everything interpolated here is escaped first; the only tags in the
    output are the ones this function adds.
    """
    from html import escape

    emoji = PLATFORM_EMOJI.get(alert.platform, "\U0001f514")
    platform = escape(alert.platform.capitalize())
    instance = escape(alert.instance_id)
    body = escape(alert.content.body).replace("\n", "<br>")

    parts = [
        f"{emoji} <strong>{platform}</strong> — {instance}",
        body,
    ]
    if alert.raw_post_ref:
        # Escaped in both the href and the text: a crafted ref must not be
        # able to close the attribute and open another.
        ref = escape(alert.raw_post_ref, quote=True)
        parts.append(f'\U0001f517 <a href="{ref}">{ref}</a>')
    return "<br>".join(parts)
