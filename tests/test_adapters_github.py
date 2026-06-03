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
    assert cursor == "1001"

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
async def test_cursor_filter_excludes_seen():
    adapter = GitHubApiAdapter(
        instance_id="g", scope="community/x",
        repos=["MovingBlocks/Terasology"], token=None,
    )
    with patch.object(adapter, "_http_get",
                      new=AsyncMock(return_value=_SAMPLE_NOTIFICATIONS)):
        alerts, cursor = await adapter.fetch("1001")

    assert alerts == []
    assert cursor == "1001"


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
