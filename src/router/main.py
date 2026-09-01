"""Knarr router bot — connects to Matrix and posts watcher alerts from Kafka.

Usage:
    python -m src.router.main

Environment variables:
    MATRIX_HOMESERVER   - Synapse URL (e.g. http://synapse:8008)
    MATRIX_USER         - Bot user ID (e.g. @knarr-router:knarr.local)
    MATRIX_PASSWORD     - Bot user password
    MATRIX_ROOM_ID      - Room ID for #social-watch
    KAFKA_BOOTSTRAP     - Kafka bootstrap servers
    KAFKA_TOPIC         - Topic to consume (default: knarr.watch.alerts)
"""

import asyncio
import logging
import os
import signal

from confluent_kafka import Consumer, KafkaError
from nio import AsyncClient, JoinResponse, LoginResponse, RoomSendResponse

from .kafka_consumer import (
    deserialize_alert,
    format_alert_html,
    format_alert_message,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Bounded retry for transient Matrix failures. Small and finite on purpose:
# the offset is not committed until a send is confirmed, so exhausting these
# stops the loop and the alert is retried on restart rather than lost.
_SEND_ATTEMPTS = 3
_SEND_BACKOFF_SECONDS = 2.0


async def _send_with_retry(client, room_id: str, alert):
    """Post one alert, retrying transient failures. Returns the response or None.

    None means every attempt failed — the caller must NOT commit the offset.
    """
    for attempt in range(1, _SEND_ATTEMPTS + 1):
        formatted = format_alert_message(alert)
        send = await client.room_send(
            room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": formatted,
                # Escaped separately rather than derived from the plain text.
                # Alert bodies are attacker-controlled (Reddit, GitHub), and
                # the old `.replace("\n", "<br>")` put them into an HTML field
                # unescaped.
                "format": "org.matrix.custom.html",
                "formatted_body": format_alert_html(alert),
            },
        )
        # nio RETURNS errors rather than raising them, so an unchecked
        # room_send logs a success that never happened. This bit us for real:
        # the router reported "Posted alert" for every message while Synapse
        # rejected all of them, because the bot had been invited to the room
        # but never joined. A router that lies about delivery is worse than
        # one that crashes.
        if isinstance(send, RoomSendResponse):
            return send

        logger.warning(
            "Matrix REJECTED alert %s from %s/%s (attempt %d/%d): %s",
            alert.event_id, alert.platform, alert.instance_id,
            attempt, _SEND_ATTEMPTS, send,
        )
        if attempt < _SEND_ATTEMPTS:
            await asyncio.sleep(_SEND_BACKOFF_SECONDS * attempt)

    return None


async def main():
    homeserver = os.environ["MATRIX_HOMESERVER"]
    user = os.environ["MATRIX_USER"]
    password = os.environ["MATRIX_PASSWORD"]
    room_id = os.environ["MATRIX_ROOM_ID"]
    kafka_bootstrap = os.environ["KAFKA_BOOTSTRAP"]
    kafka_topic = os.environ.get("KAFKA_TOPIC", "knarr.watch.alerts")

    # Connect to Matrix
    client = AsyncClient(homeserver, user)
    response = await client.login(password)
    if not isinstance(response, LoginResponse):
        logger.error("Matrix login failed: %s", response)
        return
    logger.info("Logged into Matrix as %s", user)

    # Join the target room before consuming anything.
    #
    # The reconciler INVITES the router; it does not accept on its behalf, and
    # an invited-but-not-joined bot cannot send. Joining here makes the router
    # self-sufficient after a rebuild instead of needing someone to accept the
    # invite by hand. Idempotent — joining a room you are already in succeeds.
    join = await client.join(room_id)
    if not isinstance(join, JoinResponse):
        logger.error(
            "Could not join %s: %s. Every send would be rejected, so refusing "
            "to start rather than logging phantom successes.", room_id, join,
        )
        await client.close()
        return
    logger.info("Joined %s", room_id)

    # Set up Kafka consumer.
    #
    # enable.auto.commit is OFF deliberately. With the default on, the offset
    # advances on a timer regardless of whether the alert was delivered — so a
    # send Matrix rejected was logged and then permanently skipped. Committing
    # only after a confirmed send is what makes the log line and reality agree.
    consumer = Consumer({
        "bootstrap.servers": kafka_bootstrap,
        "group.id": "knarr-router",
        "auto.offset.reset": "latest",
        "enable.auto.commit": False,
    })
    consumer.subscribe([kafka_topic])

    running = True

    def handle_signal(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info("Router started — consuming from %s, posting to %s", kafka_topic, room_id)

    try:
        while running:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("Kafka error: %s", msg.error())
                continue

            alert = deserialize_alert(msg.value())
            if alert is None:
                # Undecodable payload. It will never decode, so retrying it
                # forever would wedge the partition — commit past it.
                consumer.commit(message=msg, asynchronous=False)
                continue

            send = await _send_with_retry(client, room_id, alert)

            if send is None:
                logger.critical(
                    "Giving up on alert %s from %s/%s after %d attempts. NOT "
                    "committing the offset — this alert is retried on restart "
                    "rather than dropped. Fix the Matrix side.",
                    alert.event_id, alert.platform, alert.instance_id,
                    _SEND_ATTEMPTS,
                )
                break

            consumer.commit(message=msg, asynchronous=False)
            logger.info(
                "Posted alert from %s/%s (event %s)",
                alert.platform, alert.instance_id, send.event_id,
            )
    finally:
        consumer.close()
        await client.close()
        logger.info("Router shut down")


if __name__ == "__main__":
    asyncio.run(main())
