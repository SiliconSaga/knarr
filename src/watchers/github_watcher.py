"""GitHub watcher — polls notifications for watched repos and publishes alerts to Kafka."""

import logging

import httpx

from .schemas import Content, Source, WatchAlert

logger = logging.getLogger(__name__)


class GitHubWatcher:
    def __init__(
        self,
        repos: list[str],
        kafka_bootstrap: str,
        kafka_topic: str,
        github_token: str | None = None,
        poll_interval_seconds: int = 300,
    ):
        self.repos = repos
        self.kafka_bootstrap = kafka_bootstrap
        self.kafka_topic = kafka_topic
        self.github_token = github_token
        self.poll_interval_seconds = poll_interval_seconds
        self._seen_ids: set[str] = set()

    def _api_url_to_html_url(self, api_url: str) -> str:
        """Convert GitHub API URL to human-readable HTML URL."""
        return (
            api_url.replace("api.github.com/repos/", "github.com/")
            .replace("/pulls/", "/pull/")
        )

    def parse_notification(self, notification: dict) -> WatchAlert | None:
        """Parse a GitHub notification into a WatchAlert. Returns None if already seen."""
        notif_id = notification["id"]

        if notif_id in self._seen_ids:
            return None
        self._seen_ids.add(notif_id)

        subject = notification["subject"]
        repo = notification["repository"]["full_name"]
        title = subject["title"]
        subject_type = subject["type"]
        api_url = subject.get("url", "")
        html_url = self._api_url_to_html_url(api_url) if api_url else notification["repository"]["html_url"]

        return WatchAlert(
            source=Source(
                platform="github",
                channel=repo,
                community="terasology",
            ),
            content=Content(
                type=subject_type,
                body=f"[{subject_type}] {title}",
                url=html_url,
            ),
        )

    async def fetch_notifications(self) -> list[WatchAlert]:
        """Fetch recent GitHub notifications and return unseen alerts."""
        headers = {"Accept": "application/vnd.github+json"}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/notifications",
                headers=headers,
                params={"participating": "false", "all": "false"},
            )
            response.raise_for_status()

        alerts = []
        for notification in response.json():
            repo = notification["repository"]["full_name"]
            if repo in self.repos:
                alert = self.parse_notification(notification)
                if alert is not None:
                    alerts.append(alert)
        return alerts
