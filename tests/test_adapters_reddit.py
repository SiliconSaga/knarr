from unittest.mock import AsyncMock, patch

import pytest

from src.watchers.adapters.reddit_api import RedditApiAdapter

_SAMPLE_REDDIT_JSON = {
    "data": {
        "children": [
            {
                "data": {
                    "name": "t3_post1",
                    "id": "post1",
                    "title": "First post",
                    "author": "cervator",
                    "permalink": "/r/Terasology/comments/post1/first_post/",
                    "selftext": "Hello world",
                    "created_utc": 1748700000,
                }
            },
            {
                "data": {
                    "name": "t3_post2",
                    "id": "post2",
                    "title": "Second post",
                    "author": "[deleted]",
                    "permalink": "/r/Terasology/comments/post2/second/",
                    "selftext": "",
                    "created_utc": 1748700060,
                }
            },
        ]
    }
}


@pytest.mark.asyncio
async def test_first_fetch_returns_all_posts_and_cursor():
    adapter = RedditApiAdapter(
        instance_id="reddit-terasology",
        scope="community/terasology",
        subreddit="Terasology",
    )
    with patch.object(adapter, "_http_get",
                      new=AsyncMock(return_value=_SAMPLE_REDDIT_JSON)):
        alerts, cursor = await adapter.fetch(None)

    assert len(alerts) == 2
    assert cursor == "t3_post1"

    a = alerts[0]
    assert a.instance_id == "reddit-terasology"
    assert a.scope == "community/terasology"
    assert a.access_path == "api"
    assert a.platform == "reddit"
    assert a.event_id == "t3_post1"
    assert a.raw_post_ref == "https://www.reddit.com/r/Terasology/comments/post1/first_post/"
    assert a.content.type == "post"
    assert a.content.title == "First post"
    assert a.content.author == "cervator"
    assert "Hello world" in a.content.body


@pytest.mark.asyncio
async def test_second_fetch_filters_already_seen():
    """With a cursor, only posts newer than the cursor are returned."""
    adapter = RedditApiAdapter(
        instance_id="reddit-terasology",
        scope="community/terasology",
        subreddit="Terasology",
    )
    with patch.object(adapter, "_http_get",
                      new=AsyncMock(return_value=_SAMPLE_REDDIT_JSON)):
        alerts, cursor = await adapter.fetch("t3_post1")

    assert alerts == []
    assert cursor == "t3_post1"


@pytest.mark.asyncio
async def test_deleted_author_handled():
    adapter = RedditApiAdapter(
        instance_id="r", scope="community/x", subreddit="X",
    )
    with patch.object(adapter, "_http_get",
                      new=AsyncMock(return_value=_SAMPLE_REDDIT_JSON)):
        alerts, _ = await adapter.fetch(None)

    deleted_author_alert = [a for a in alerts if a.event_id == "t3_post2"][0]
    assert deleted_author_alert.content.author == "[deleted]"
    assert deleted_author_alert.content.body == ""


@pytest.mark.asyncio
async def test_empty_response_returns_empty_alerts():
    adapter = RedditApiAdapter(
        instance_id="r", scope="community/x", subreddit="X",
    )
    empty = {"data": {"children": []}}
    with patch.object(adapter, "_http_get", new=AsyncMock(return_value=empty)):
        alerts, cursor = await adapter.fetch(None)
    assert alerts == []
    assert cursor is None
