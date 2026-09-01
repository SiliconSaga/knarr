from unittest.mock import AsyncMock, patch

import pytest

from src.watchers.adapters.reddit_api import _MAX_PAGES, RedditApiAdapter

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
                    # NEWER than post2 — /new sorts descending, and the
                    # adapter takes the first child as the cursor tip. A
                    # fixture in the opposite order quietly disagrees with the
                    # ordering the code relies on.
                    "created_utc": 1748700060,
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
                    "created_utc": 1748700000,
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

    deleted_author_alert = next(a for a in alerts if a.event_id == "t3_post2")
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


def _page(names, after=None):
    """Build a /new listing page from post names."""
    return {
        "data": {
            "after": after,
            "children": [
                {
                    "data": {
                        "name": n,
                        "id": n.removeprefix("t3_"),
                        "title": f"Post {n}",
                        "author": "someone",
                        "permalink": f"/r/X/comments/{n}/x/",
                        "selftext": "",
                        "created_utc": 1748700000,
                    }
                }
                for n in names
            ],
        }
    }


@pytest.mark.asyncio
async def test_pagination_walks_back_to_the_cursor():
    """Posts beyond the first page must not be silently dropped.

    Without paging, a subreddit that produced more posts than one page
    between polls loses everything past that page — and the adapter cannot
    tell a full page from a complete one, so nothing surfaces the loss.
    """
    adapter = RedditApiAdapter(
        instance_id="r", scope="community/x", subreddit="X",
    )
    pages = [
        _page(["t3_n1", "t3_n2"], after="t3_n2"),
        _page(["t3_n3", "t3_old"], after="t3_older"),   # cursor is on page 2
    ]
    seen_urls = []

    async def fake_get(url):
        seen_urls.append(url)
        return pages[len(seen_urls) - 1]

    with patch.object(adapter, "_http_get", new=AsyncMock(side_effect=fake_get)):
        alerts, cursor = await adapter.fetch("t3_old")

    assert [a.event_id for a in alerts] == ["t3_n1", "t3_n2", "t3_n3"]
    assert cursor == "t3_n1"
    assert "after=t3_n2" in seen_urls[1]


@pytest.mark.asyncio
async def test_pagination_stops_when_listing_is_exhausted():
    """A listing with no `after` ends the walk rather than looping."""
    adapter = RedditApiAdapter(
        instance_id="r", scope="community/x", subreddit="X",
    )
    calls = []

    async def fake_get(url):
        calls.append(url)
        return _page(["t3_a"], after=None)

    with patch.object(adapter, "_http_get", new=AsyncMock(side_effect=fake_get)):
        alerts, _ = await adapter.fetch("t3_never_appears")

    assert len(calls) == 1
    assert [a.event_id for a in alerts] == ["t3_a"]


@pytest.mark.asyncio
async def test_pagination_is_bounded_when_cursor_is_never_found():
    """A vanished cursor degrades to a bounded backlog, not an endless crawl."""
    adapter = RedditApiAdapter(
        instance_id="r", scope="community/x", subreddit="X",
    )
    calls = []

    async def fake_get(url):
        calls.append(url)
        return _page([f"t3_p{len(calls)}"], after=f"t3_p{len(calls)}")

    with patch.object(adapter, "_http_get", new=AsyncMock(side_effect=fake_get)):
        await adapter.fetch("t3_deleted_post")

    assert len(calls) == _MAX_PAGES
