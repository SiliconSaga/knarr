"""GitHub API adapter — polls the authenticated user's /notifications.

Replaces `src/watchers/github_watcher.py`. Behaviour matches the legacy
implementation: read the unified notifications inbox, filter to the
configured repo list, emit one WatchAlert per notification.

The repo-side polling alternative (issues/PRs/discussions endpoints per
repo) is a separate decision from Phase 3+ of the source-identity work;
this adapter preserves the existing notifications-based behaviour.
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx

from src.watchers.schemas import Content, WatchAlert

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_PER_PAGE = 50
_MAX_PAGES = 5
# Re-request one second before the cursor.
#
# GitHub documents `since` as "only show results updated after this time",
# which reads as exclusive — and an exclusive filter drops EVERY row sharing
# the cursor's second, including one we have never seen. Asking for a second
# of history we have already read costs one comparison per row and removes
# the guesswork about which semantics apply; _seen_ids suppresses the
# duplicates it brings back.
_OVERLAP_SECONDS = 1


def _overlap(cursor: str | None) -> str | None:
    """Shift an ISO-8601 cursor back by the overlap window."""
    if not cursor:
        return None
    try:
        parsed = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
    except ValueError:
        # Not a timestamp we can shift — send it unchanged rather than
        # dropping the filter entirely and re-reading the whole feed.
        return cursor
    shifted = parsed - timedelta(seconds=_OVERLAP_SECONDS)
    return shifted.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        # Ids already emitted from within the overlap window. The window is
        # deliberately re-requested each poll (see _OVERLAP_SECONDS), so these
        # come back every time; suppressing by ID is what lets the overlap be
        # safe without re-emitting, AND what lets a genuinely new
        # notification sharing that second still get through.
        self._seen_ids: set[str] = set()
        # Staged by fetch(), promoted by commit(). None means nothing pending.
        self._pending_seen_ids: set[str] | None = None

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

    @staticmethod
    def _newest_timestamp(notifications: list[dict]) -> str | None:
        """Newest updated_at across ALL rows, matched or not.

        The cursor describes how far the feed was read, which is independent
        of which repos we happen to care about — filtering it by repo would
        make the cursor lag behind the feed and re-read the gap forever.
        """
        newest: str | None = None
        for notif in notifications:
            updated_at = notif.get("updated_at")
            if updated_at and (newest is None or updated_at > newest):
                newest = updated_at
        return newest

    def _build_alerts(self, notifications: list[dict]) -> list[WatchAlert]:
        """Filter to configured repos and previously-unseen ids, emit alerts."""
        alerts: list[WatchAlert] = []
        for notif in notifications:
            notif_id = notif["id"]

            # Rows from the re-requested overlap window that we already
            # emitted. Suppressed by ID, never by timestamp — a timestamp
            # filter here would also discard an unseen notification that
            # happens to share the second.
            if notif_id in self._seen_ids:
                continue

            repo_full = notif["repository"]["full_name"]
            if repo_full not in self.repos:
                continue

            subject = notif["subject"]
            alerts.append(WatchAlert(
                event_id=notif_id,
                instance_id=self.instance_id,
                scope=self.scope,
                access_path="api",
                platform="github",
                raw_post_ref=self._api_url_to_web_url(notif),
                content=Content(
                    type=self._normalize_type(subject["type"]),
                    title=subject["title"],
                    body="",  # /notifications doesn't include body
                    author=repo_full,  # best we have without an extra request
                    attachments=[],
                ),
                timestamp=notif["updated_at"],
            ))
        return alerts

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
        since_param = _overlap(since_cursor)

        notifications: list[dict] = []
        exhausted = False
        for page in range(1, _MAX_PAGES + 1):
            url = (
                f"{_GITHUB_API}/notifications"
                f"?participating=false&all=false"
                f"&per_page={_PER_PAGE}&page={page}"
            )
            if since_param:
                url = f"{url}&since={since_param}"
            batch = await self._http_get(url)
            notifications.extend(batch)
            # A short page is the last page. Paginating at all matters for the
            # same reason it did on Reddit: without it a busy interval loses
            # everything past the first page, and a full page is
            # indistinguishable from a complete one.
            if len(batch) < _PER_PAGE:
                exhausted = True
                break

        if not exhausted:
            # Hit the page cap with more still to read. The feed is sorted
            # newest-first, so the unread remainder is OLDER than everything
            # fetched — and advancing the cursor to the newest row would put
            # it permanently beyond them. Hold the cursor instead: this poll's
            # alerts still ship, and the next poll re-reads the same window
            # rather than skipping what it never saw.
            #
            # A visible stall beats silent loss. The warning fires every poll
            # until the backlog drains or the cap is raised.
            logger.error(
                "instance=%s hit the %d-page cap (%d notifications) with more "
                "remaining; holding the cursor at %s so older unread rows are "
                "not skipped",
                self.instance_id, _MAX_PAGES, len(notifications), since_cursor,
            )
            self._pending_seen_ids = None
            return self._build_alerts(notifications), since_cursor

        newest_seen = self._newest_timestamp(notifications)
        alerts = self._build_alerts(notifications)
        cursor = newest_seen if newest_seen is not None else since_cursor

        # Ids inside the window the next poll will re-request, so the overlap
        # costs a set lookup rather than duplicate alerts. Bounded by the
        # window, not by feed size.
        #
        # Held PENDING, not committed. Recording these here would defeat the
        # instance's cursor safety: a delivery failure correctly holds the
        # cursor, but the re-fetch would then suppress these very rows as
        # "already seen" and the alerts would be lost with the cursor still
        # looking correct. WatcherInstance calls commit() once delivery is
        # confirmed.
        if cursor is not None:
            boundary = _overlap(cursor)
            self._pending_seen_ids = {
                n["id"] for n in notifications
                if boundary is None or (n.get("updated_at") or "") >= boundary
            }

        return alerts, cursor

    def commit(self) -> None:
        """Promote pending dedup state — called only after confirmed delivery."""
        if self._pending_seen_ids is not None:
            self._seen_ids = self._pending_seen_ids
            self._pending_seen_ids = None
