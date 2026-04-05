from src.watchers.reddit_watcher import RedditWatcher
from src.watchers.schemas import WatchAlert


def make_watcher():
    return RedditWatcher(
        subreddit="Terasology",
        kafka_bootstrap="localhost:9092",
        kafka_topic="knarr.watch.alerts",
        poll_interval_seconds=60,
    )


def test_parse_reddit_post_into_alert():
    watcher = make_watcher()
    post_data = {
        "data": {
            "title": "Cool Terasology build showcase",
            "author": "gamer42",
            "permalink": "/r/Terasology/comments/abc123/cool_build/",
            "selftext": "Check out this amazing build I made!",
            "created_utc": 1743600000.0,
            "name": "t3_abc123",
        }
    }

    alert = watcher.parse_post(post_data)

    assert isinstance(alert, WatchAlert)
    assert alert.source.platform == "reddit"
    assert alert.source.channel == "r/Terasology"
    assert alert.source.community == "terasology"
    assert alert.content.type == "new_post"
    assert "Cool Terasology build showcase" in alert.content.body
    assert "reddit.com/r/Terasology/comments/abc123" in alert.content.url


def test_deduplication_skips_seen_posts():
    watcher = make_watcher()
    post_data = {
        "data": {
            "title": "Duplicate post",
            "author": "user1",
            "permalink": "/r/Terasology/comments/dup1/duplicate/",
            "selftext": "",
            "created_utc": 1743600000.0,
            "name": "t3_dup1",
        }
    }

    alert1 = watcher.parse_post(post_data)
    assert alert1 is not None

    alert2 = watcher.parse_post(post_data)
    assert alert2 is None
