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
from nio import AsyncClient, LoginResponse

from .kafka_consumer import deserialize_alert, format_alert_message

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


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

    # Set up Kafka consumer
    consumer = Consumer({
        "bootstrap.servers": kafka_bootstrap,
        "group.id": "knarr-router",
        "auto.offset.reset": "latest",
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
                continue

            formatted = format_alert_message(alert)
            await client.room_send(
                room_id,
                message_type="m.room.message",
                content={
                    "msgtype": "m.text",
                    "body": formatted,
                    "format": "org.matrix.custom.html",
                    "formatted_body": formatted.replace("\n", "<br>"),
                },
            )
            logger.info("Posted alert from %s/%s", alert.platform, alert.instance_id)
    finally:
        consumer.close()
        await client.close()
        logger.info("Router shut down")


if __name__ == "__main__":
    asyncio.run(main())
