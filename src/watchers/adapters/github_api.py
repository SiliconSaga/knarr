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
        # Ids seen at exactly the cursor timestamp on the previous fetch.
        # GitHub's `since` is inclusive, so these come back every poll; this
        # is the tie-breaker that suppresses them without discarding a
        # genuinely new notification that shares their timestamp.
        self._boundary_ids: set[str] = set()

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
    def _api_url_to_web_url(notif: dict) -> str:
        """Best web URL for a notification, per subject type.

        A blanket string rewrite of `subject.url` is wrong for two types and
        produces links that 404:

        - **Commit** — the API path is `/commits/<sha>` but the web path is
          the singular `/commit/<sha>`.
        - **Release** — `subject.url` ends in the release's numeric API id,
          which does not appear in any web URL at all. There is no rewrite
          that recovers the tag, so prefer `latest_comment_url` when GitHub
          supplies it and otherwise fall back to the repo's releases page,
          which is at least a real destination.
        """
        subject = notif.get("subject", {})
        subject_type = subject.get("type", "")
        api_url = subject.get("url", "") or ""

        def to_web(u: str) -> str:
            return (u
                    .replace("api.github.com/repos/", "github.com/")
                    .replace("/pulls/", "/pull/")
                    .replace("/commits/", "/commit/"))

        if subject_type == "Release":
            latest = subject.get("latest_comment_url") or ""
            if latest:
                return to_web(latest)
            repo_url = notif.get("repository", {}).get("html_url", "")
            return f"{repo_url}/releases" if repo_url else ""

        return to_web(api_url) if api_url else ""

    async def fetch(
        self, since_cursor: str | None
    ) -> tuple[list[WatchAlert], str | None]:
        """Poll /notifications, filtered by repo, since the last timestamp.

        The cursor is an `updated_at` TIMESTAMP, not a notification id. Ids
        are not ordered — GitHub sorts this feed by `updated_at` — so an id
        cursor could only ever be matched by scanning for it, and a
        notification that aged out of the feed made the cursor unmatchable
        and the whole feed look new. A timestamp also feeds `since`, so the
        server does the filtering instead of the client.

        `since` is EXCLUSIVE in effect for our purposes but GitHub treats it
        as inclusive, so rows exactly at the boundary come back again; they
        are dropped by id against the previous batch rather than by
        timestamp, which is what keeps two notifications sharing a
        millisecond from cancelling each other out.
        """
        url = f"{_GITHUB_API}/notifications?participating=false&all=false"
        if since_cursor:
            url = f"{url}&since={since_cursor}"
        notifications = await self._http_get(url)

        alerts: list[WatchAlert] = []
        newest_seen: str | None = None

        for notif in notifications:
            notif_id = notif["id"]
            updated_at = notif.get("updated_at")

            # Track the newest timestamp across ALL rows, not just matched
            # ones — the cursor describes how far the feed was read, which is
            # independent of which repos we care about.
            if updated_at and (newest_seen is None or updated_at > newest_seen):
                newest_seen = updated_at

            # Boundary rows: `since` is inclusive, so the row that set the
            # previous cursor comes back. Skip it by identity.
            if since_cursor is not None and updated_at == since_cursor \
                    and notif_id in self._boundary_ids:
                continue

            repo_full = notif["repository"]["full_name"]
            if repo_full not in self.repos:
                continue

            subject = notif["subject"]
            subject_type = subject["type"]
            title = subject["title"]
            web_url = self._api_url_to_web_url(notif)

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

        # Remember which ids sat exactly on the new cursor, so the next poll
        # can drop them when GitHub returns them again for an inclusive
        # `since` — without dropping a new notification that happens to share
        # the timestamp.
        if cursor is not None:
            self._boundary_ids = {
                n["id"] for n in notifications if n.get("updated_at") == cursor
            }

        return alerts, cursor
