"""Reddit watcher — polls a subreddit for new posts and publishes alerts to Kafka."""

import logging

import httpx

from .schemas import Content, Source, WatchAlert

logger = logging.getLogger(__name__)

REDDIT_BASE = "https://www.reddit.com"


class RedditWatcher:
    def __init__(
        self,
        subreddit: str,
        kafka_bootstrap: str,
        kafka_topic: str,
        poll_interval_seconds: int = 300,
    ):
        self.subreddit = subreddit
        self.kafka_bootstrap = kafka_bootstrap
        self.kafka_topic = kafka_topic
        self.poll_interval_seconds = poll_interval_seconds
        self._seen_ids: set[str] = set()

    def parse_post(self, post_data: dict) -> WatchAlert | None:
        """Parse a Reddit post JSON object into a WatchAlert. Returns None if already seen."""
        data = post_data["data"]
        post_id = data["name"]

        if post_id in self._seen_ids:
            return None
        self._seen_ids.add(post_id)

        title = data["title"]
        author = data.get("author", "[deleted]")
        permalink = data["permalink"]
        selftext = data.get("selftext", "")

        body = f"**{title}** by u/{author}"
        if selftext:
            preview = selftext[:200] + ("..." if len(selftext) > 200 else "")
            body += f"\n{preview}"

        return WatchAlert(
            source=Source(
                platform="reddit",
                channel=f"r/{self.subreddit}",
                community="terasology",
            ),
            content=Content(
                type="new_post",
                body=body,
                url=f"https://www.reddit.com{permalink}",
            ),
        )

    async def fetch_new_posts(self) -> list[WatchAlert]:
        """Fetch recent posts from the subreddit and return unseen alerts."""
        url = f"{REDDIT_BASE}/r/{self.subreddit}/new.json?limit=10"
        headers = {"User-Agent": "knarr-watcher/0.1 (by u/Cervator)"}

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, follow_redirects=True)
            response.raise_for_status()

        posts = response.json()["data"]["children"]
        alerts = []
        for post in posts:
            alert = self.parse_post(post)
            if alert is not None:
                alerts.append(alert)
        return alerts
