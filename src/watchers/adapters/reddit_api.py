"""Reddit API adapter — polls a subreddit's /new endpoint.

Replaces the legacy `src/watchers/reddit_watcher.py`. Same wire behaviour;
new internal shape (Adapter Protocol + per-instance config).
"""

import logging
from datetime import UTC, datetime

import httpx

from src.watchers.schemas import Content, WatchAlert

logger = logging.getLogger(__name__)

_REDDIT_BASE = "https://www.reddit.com"
_USER_AGENT = "knarr-watcher/0.2 (by u/Cervator)"


class RedditApiAdapter:
    """Polls /r/<subreddit>/new.json; emits one WatchAlert per new post.

    Cursor is the Reddit `name` (e.g. `t3_xxxxxx`) of the newest post seen
    so far. Posts at or older than the cursor are filtered out.
    """

    def __init__(self, instance_id: str, scope: str, subreddit: str):
        self.instance_id = instance_id
        self.scope = scope
        self.subreddit = subreddit

    async def _http_get(self, url: str) -> dict:
        """Indirection point so tests can mock the HTTP layer."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            )
            response.raise_for_status()
            return response.json()

    async def fetch(
        self, since_cursor: str | None
    ) -> tuple[list[WatchAlert], str | None]:
        url = f"{_REDDIT_BASE}/r/{self.subreddit}/new.json?limit=10"
        payload = await self._http_get(url)
        posts = payload["data"]["children"]

        alerts: list[WatchAlert] = []
        newest_seen: str | None = None
        for post in posts:
            data = post["data"]
            name = data["name"]
            if newest_seen is None:
                newest_seen = name  # first item is newest (Reddit /new sorts desc)
            if since_cursor is not None and name == since_cursor:
                # Reached previously-seen tip; anything after is old too.
                break

            permalink = data["permalink"]
            created_ts = datetime.fromtimestamp(
                data.get("created_utc", 0), tz=UTC,
            ).isoformat()

            alerts.append(WatchAlert(
                event_id=name,
                instance_id=self.instance_id,
                scope=self.scope,
                access_path="api",
                platform="reddit",
                raw_post_ref=f"{_REDDIT_BASE}{permalink}",
                content=Content(
                    type="post",
                    title=data.get("title", ""),
                    body=data.get("selftext", ""),
                    author=data.get("author", "[deleted]"),
                    attachments=[],
                ),
                timestamp=created_ts,
            ))

        cursor = newest_seen if newest_seen is not None else since_cursor
        return alerts, cursor
