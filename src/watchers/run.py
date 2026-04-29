"""Watcher runner — polls platforms and publishes alerts to Kafka.

Environment variables:
    KAFKA_BOOTSTRAP       - Kafka bootstrap servers
    KAFKA_TOPIC           - Target topic (default: knarr.watch.alerts)
    REDDIT_SUBREDDIT      - Subreddit to watch (default: Terasology)
    GITHUB_REPOS          - Comma-separated repo list (default: MovingBlocks/Terasology)
    GITHUB_TOKEN          - GitHub personal access token (optional, for higher rate limits)
    POLL_INTERVAL_SECONDS - Polling interval (default: 300)
"""

import asyncio
import json
import logging
import os
import signal

from confluent_kafka import Producer

from .github_watcher import GitHubWatcher
from .reddit_watcher import RedditWatcher
from .schemas import WatchAlert

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def publish_alerts(producer: Producer, topic: str, alerts: list[WatchAlert]):
    for alert in alerts:
        producer.produce(
            topic,
            key=f"{alert.source.platform}:{alert.source.channel}",
            value=json.dumps(alert.to_kafka_dict()),
        )
    producer.flush()


async def main():
    kafka_bootstrap = os.environ["KAFKA_BOOTSTRAP"]
    kafka_topic = os.environ.get("KAFKA_TOPIC", "knarr.watch.alerts")
    poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))

    reddit_sub = os.environ.get("REDDIT_SUBREDDIT", "Terasology")
    github_repos = os.environ.get("GITHUB_REPOS", "MovingBlocks/Terasology").split(",")
    github_token = os.environ.get("GITHUB_TOKEN")

    reddit = RedditWatcher(
        subreddit=reddit_sub,
        kafka_bootstrap=kafka_bootstrap,
        kafka_topic=kafka_topic,
        poll_interval_seconds=poll_interval,
    )
    github = GitHubWatcher(
        repos=github_repos,
        kafka_bootstrap=kafka_bootstrap,
        kafka_topic=kafka_topic,
        github_token=github_token,
        poll_interval_seconds=poll_interval,
    )

    producer = Producer({"bootstrap.servers": kafka_bootstrap})

    running = True

    def handle_signal(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info(
        "Watcher started — reddit: r/%s, github: %s, interval: %ds",
        reddit_sub, github_repos, poll_interval,
    )

    while running:
        try:
            reddit_alerts = await reddit.fetch_new_posts()
            if reddit_alerts:
                publish_alerts(producer, kafka_topic, reddit_alerts)
                logger.info("Published %d Reddit alerts", len(reddit_alerts))

            github_alerts = await github.fetch_notifications()
            if github_alerts:
                publish_alerts(producer, kafka_topic, github_alerts)
                logger.info("Published %d GitHub alerts", len(github_alerts))

        except Exception:
            logger.exception("Error during poll cycle")

        await asyncio.sleep(poll_interval)


if __name__ == "__main__":
    asyncio.run(main())
