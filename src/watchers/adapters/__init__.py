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

        Any internal dedup state the adapter builds while fetching must be
        held PENDING until `commit()` — see below.
        """
        ...

    def commit(self) -> None:
        """Promote the last fetch's pending dedup state to committed.

        Called by WatcherInstance only after every alert from that fetch has
        been confirmed delivered to Kafka.

        This exists because an adapter that dedups internally can silently
        defeat the instance's cursor safety. GitHubApiAdapter suppresses rows
        it has already emitted; if it recorded them during `fetch`, then a
        delivery failure — which correctly holds the instance's cursor — would
        be followed by a re-fetch in which those same rows are suppressed as
        "already seen". The cursor would be right and the alerts would be gone
        anyway. Splitting fetch from commit keeps the two in step.

        Adapters with no internal dedup state implement this as a no-op.
        """
        ...
