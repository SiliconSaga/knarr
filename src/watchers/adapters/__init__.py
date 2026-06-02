"""Adapter Protocol — how WatcherInstance talks to platforms.

Each concrete adapter (RedditApiAdapter, GitHubApiAdapter, etc.) speaks
to one platform through one access path. The Protocol keeps WatcherInstance
adapter-agnostic.

See docs/plans/2026-05-30-knarr-source-identity-design.md § Pipeline
architecture for the WatcherInstance + adapter split rationale.
"""

from typing import Protocol

from src.watchers.schemas import WatchAlert


class Adapter(Protocol):
    """A platform-specific source adapter.

    `fetch` is called by the host WatcherInstance on each poll cycle. The
    cursor lets the adapter remember "where it left off"; what the cursor
    looks like is adapter-defined (a timestamp, a fullname id, an opaque
    token — whatever the platform supports). The adapter returns whatever
    new events it saw plus the new cursor value, which the instance
    persists.
    """

    async def fetch(
        self, since_cursor: str | None
    ) -> tuple[list[WatchAlert], str | None]:
        """Fetch events since the given cursor.

        Args:
            since_cursor: opaque value previously returned by this adapter;
                          None on first call.

        Returns:
            (alerts, new_cursor) — alerts may be empty; new_cursor may equal
            since_cursor if nothing new arrived.
        """
        ...
