"""GitHub API adapter — polls the authenticated user's /notifications.

Replaces `src/watchers/github_watcher.py`. Behaviour matches the legacy
implementation: read the unified notifications inbox, filter to the
configured repo list, emit one WatchAlert per notification.

The repo-side polling alternative (issues/PRs/discussions endpoints per
repo) is a separate decision from Phase 3+ of the source-identity work;
this adapter preserves the existing notifications-based behaviour.
"""

import logging

import httpx

from src.watchers.schemas import Content, WatchAlert

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"


class GitHubApiAdapter:
    """Polls GET /notifications; filters by configured repos.

    Cursor is the notification id (string of digits) of the most-recent
    notification seen. GitHub returns notifications sorted by updated_at
    desc; we use the id of the first row as the cursor.
    """

    def __init__(
        self,
        instance_id: str,
        scope: str,
        repos: list[str],
        token: str | None,
    ):
        self.instance_id = instance_id
        self.scope = scope
        self.repos = set(repos)
        self.token = token

    async def _http_get_raw(self, url: str, headers: dict | None = None) -> list[dict]:
        """Indirection point so token-header tests can intercept."""
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers or {})
            response.raise_for_status()
            return response.json()

    async def _http_get(self, url: str) -> list[dict]:
        """Auth-aware GET. Adds Bearer token when configured."""
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return await self._http_get_raw(url, headers=headers)

    @staticmethod
    def _normalize_type(github_subject_type: str) -> str:
        """Map GitHub's subject.type to our content.type vocabulary."""
        mapping = {
            "Issue": "issue",
            "PullRequest": "pull_request",
            "Release": "release",
            "Commit": "commit",
            "Discussion": "discussion",
        }
        return mapping.get(github_subject_type, github_subject_type.lower())

    @staticmethod
    def _api_url_to_web_url(api_url: str) -> str:
        """Convert api.github.com/repos/o/r/pulls/N -> github.com/o/r/pull/N."""
        return (api_url
                .replace("api.github.com/repos/", "github.com/")
                .replace("/pulls/", "/pull/"))

    async def fetch(
        self, since_cursor: str | None
    ) -> tuple[list[WatchAlert], str | None]:
        url = f"{_GITHUB_API}/notifications?participating=false&all=false"
        notifications = await self._http_get(url)

        alerts: list[WatchAlert] = []
        newest_seen: str | None = None

        for notif in notifications:
            notif_id = notif["id"]
            if newest_seen is None:
                newest_seen = notif_id

            if since_cursor is not None and notif_id == since_cursor:
                break

            repo_full = notif["repository"]["full_name"]
            if repo_full not in self.repos:
                continue

            subject = notif["subject"]
            subject_type = subject["type"]
            title = subject["title"]
            api_url = subject.get("url", "")
            web_url = self._api_url_to_web_url(api_url) if api_url else ""

            alerts.append(WatchAlert(
                event_id=notif_id,
                instance_id=self.instance_id,
                scope=self.scope,
                access_path="api",
                platform="github",
                raw_post_ref=web_url,
                content=Content(
                    type=self._normalize_type(subject_type),
                    title=title,
                    body="",  # /notifications doesn't include body
                    author=repo_full,  # best we have without an extra request
                    attachments=[],
                ),
                timestamp=notif["updated_at"],
            ))

        cursor = newest_seen if newest_seen is not None else since_cursor
        return alerts, cursor
