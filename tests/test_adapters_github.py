import re
from unittest.mock import AsyncMock, patch

import pytest

from src.watchers.adapters.github_api import GitHubApiAdapter

_SAMPLE_NOTIFICATIONS = [
    {
        "id": "1001",
        "updated_at": "2026-05-31T11:55:00Z",
        "repository": {"full_name": "MovingBlocks/Terasology"},
        "subject": {
            "type": "Issue",
            "title": "Some bug",
            "url": "https://api.github.com/repos/MovingBlocks/Terasology/issues/42",
        },
    },
    {
        "id": "1002",
        "updated_at": "2026-05-31T11:50:00Z",
        "repository": {"full_name": "MovingBlocks/Terasology"},
        "subject": {
            "type": "PullRequest",
            "title": "Fix the thing",
            "url": "https://api.github.com/repos/MovingBlocks/Terasology/pulls/43",
        },
    },
    {
        "id": "1003",
        "updated_at": "2026-05-31T11:45:00Z",
        "repository": {"full_name": "SomeOther/Repo"},
        "subject": {
            "type": "Issue",
            "title": "Off-topic",
            "url": "https://api.github.com/repos/SomeOther/Repo/issues/1",
        },
    },
]


@pytest.mark.asyncio
async def test_first_fetch_returns_filtered_alerts():
    adapter = GitHubApiAdapter(
        instance_id="github-terasology",
        scope="community/terasology",
        repos=["MovingBlocks/Terasology"],
        token=None,
    )
    with patch.object(adapter, "_http_get",
                      new=AsyncMock(return_value=_SAMPLE_NOTIFICATIONS)):
        alerts, cursor = await adapter.fetch(None)

    assert len(alerts) == 2
    assert {a.event_id for a in alerts} == {"1001", "1002"}
    # The cursor is the newest updated_at across ALL rows — including the
    # off-topic one, because it describes how far the feed was read rather
    # than which rows we kept.
    assert cursor == "2026-05-31T11:55:00Z"

    issue = next(a for a in alerts if a.event_id == "1001")
    assert issue.platform == "github"
    assert issue.access_path == "api"
    assert issue.instance_id == "github-terasology"
    assert issue.scope == "community/terasology"
    assert issue.content.type == "issue"
    assert issue.content.title == "Some bug"
    assert "github.com/MovingBlocks/Terasology/issues/42" in issue.raw_post_ref


@pytest.mark.asyncio
async def test_pullrequest_type_normalized():
    adapter = GitHubApiAdapter(
        instance_id="g", scope="community/x",
        repos=["MovingBlocks/Terasology"], token=None,
    )
    with patch.object(adapter, "_http_get",
                      new=AsyncMock(return_value=_SAMPLE_NOTIFICATIONS)):
        alerts, _ = await adapter.fetch(None)

    pr = next(a for a in alerts if a.event_id == "1002")
    assert pr.content.type == "pull_request"
    assert "pull/43" in pr.raw_post_ref


@pytest.mark.asyncio
async def test_cursor_is_sent_as_since_with_overlap_and_seen_rows_suppressed():
    """`since` is sent one second BEHIND the cursor, and repeats are dropped by id.

    The overlap exists because GitHub documents `since` as "updated after this
    time" — exclusive — which would drop every row sharing the cursor's
    second, including one never seen. Re-reading that second and suppressing
    by id is correct under either interpretation; suppressing by timestamp
    instead would reintroduce exactly the loss the overlap prevents.
    """
    adapter = GitHubApiAdapter(
        instance_id="g", scope="community/x",
        repos=["MovingBlocks/Terasology"], token=None,
    )
    captured_urls = []

    async def fake_get(url):
        """Honour `since` the way the real endpoint does.

        This matters: `_seen_ids` is deliberately bounded to the overlap
        window rather than growing without limit, which is only safe because
        the SERVER filters out everything older. A stub that ignored `since`
        would make the adapter look broken for a reason production never has.
        """
        captured_urls.append(url)
        match = re.search(r"since=([^&]+)", url)
        if not match:
            return _SAMPLE_NOTIFICATIONS
        since = match.group(1)
        return [n for n in _SAMPLE_NOTIFICATIONS if n["updated_at"] > since]

    with patch.object(adapter, "_http_get", new=AsyncMock(side_effect=fake_get)):
        _, cursor = await adapter.fetch(None)
        assert cursor == "2026-05-31T11:55:00Z"

        # Second poll: same feed replayed. Every row was already emitted, so
        # nothing should come back out even though the overlap re-reads them.
        alerts, cursor2 = await adapter.fetch(cursor)

    # One second behind the cursor, not the cursor itself.
    assert "since=2026-05-31T11:54:59Z" in captured_urls[1]
    assert alerts == []
    assert cursor2 == "2026-05-31T11:55:00Z"


@pytest.mark.asyncio
async def test_new_row_sharing_the_boundary_timestamp_is_not_dropped():
    """A tie on the cursor timestamp must not swallow an unseen notification."""
    adapter = GitHubApiAdapter(
        instance_id="g", scope="community/x",
        repos=["MovingBlocks/Terasology"], token=None,
    )
    with patch.object(adapter, "_http_get",
                      new=AsyncMock(return_value=_SAMPLE_NOTIFICATIONS)):
        _, cursor = await adapter.fetch(None)

    # A different notification with the SAME updated_at as the cursor.
    tie = {
        "id": "1004",
        "updated_at": "2026-05-31T11:55:00Z",
        "repository": {"full_name": "MovingBlocks/Terasology"},
        "subject": {
            "type": "Issue",
            "title": "Simultaneous",
            "url": "https://api.github.com/repos/MovingBlocks/Terasology/issues/44",
        },
    }
    with patch.object(adapter, "_http_get",
                      new=AsyncMock(return_value=[tie, *_SAMPLE_NOTIFICATIONS])):
        alerts, _ = await adapter.fetch(cursor)

    assert "1004" in {a.event_id for a in alerts}
    assert "1001" not in {a.event_id for a in alerts}


@pytest.mark.asyncio
async def test_release_and_commit_web_urls():
    """Release ids and commit paths need special handling, not a blanket rewrite."""
    adapter = GitHubApiAdapter(
        instance_id="g", scope="community/x",
        repos=["MovingBlocks/Terasology"], token=None,
    )
    rows = [
        {
            "id": "2001",
            "updated_at": "2026-06-01T10:00:00Z",
            "repository": {
                "full_name": "MovingBlocks/Terasology",
                "html_url": "https://github.com/MovingBlocks/Terasology",
            },
            "subject": {
                "type": "Commit",
                "title": "Fix a thing",
                "url": "https://api.github.com/repos/MovingBlocks/Terasology/commits/abc123",
            },
        },
        {
            "id": "2002",
            "updated_at": "2026-06-01T09:00:00Z",
            "repository": {
                "full_name": "MovingBlocks/Terasology",
                "html_url": "https://github.com/MovingBlocks/Terasology",
            },
            "subject": {
                "type": "Release",
                "title": "v1.2.3",
                # Ends in the numeric API id, which appears in no web URL.
                "url": "https://api.github.com/repos/MovingBlocks/Terasology/releases/98765",
            },
        },
    ]
    with patch.object(adapter, "_http_get", new=AsyncMock(return_value=rows)):
        alerts, _ = await adapter.fetch(None)

    by_id = {a.event_id: a for a in alerts}
    # Singular /commit/, not the API's /commits/.
    assert by_id["2001"].raw_post_ref == \
        "https://github.com/MovingBlocks/Terasology/commit/abc123"
    # No usable per-release web URL, so the releases page rather than a 404.
    assert by_id["2002"].raw_post_ref == \
        "https://github.com/MovingBlocks/Terasology/releases"


@pytest.mark.asyncio
async def test_token_passed_in_authorization_header():
    """When a token is configured, it's sent as Bearer auth."""
    adapter = GitHubApiAdapter(
        instance_id="g", scope="community/x",
        repos=["MovingBlocks/Terasology"], token="ghp_FAKETOKEN",
    )
    captured = {}

    async def fake_get(url, headers=None):
        captured["headers"] = headers or {}
        return []

    with patch.object(adapter, "_http_get_raw", new=AsyncMock(side_effect=fake_get)):
        await adapter.fetch(None)
    assert captured["headers"].get("Authorization") == "Bearer ghp_FAKETOKEN"
