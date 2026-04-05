from src.watchers.github_watcher import GitHubWatcher
from src.watchers.schemas import WatchAlert


def make_watcher():
    return GitHubWatcher(
        repos=["MovingBlocks/Terasology"],
        kafka_bootstrap="localhost:9092",
        kafka_topic="knarr.watch.alerts",
    )


def test_parse_notification_into_alert():
    watcher = make_watcher()
    notification = {
        "id": "12345",
        "subject": {
            "title": "Fix rendering bug on ARM Macs",
            "type": "Issue",
            "url": "https://api.github.com/repos/MovingBlocks/Terasology/issues/5678",
        },
        "repository": {
            "full_name": "MovingBlocks/Terasology",
            "html_url": "https://github.com/MovingBlocks/Terasology",
        },
        "reason": "subscribed",
        "updated_at": "2026-04-02T10:00:00Z",
    }

    alert = watcher.parse_notification(notification)

    assert isinstance(alert, WatchAlert)
    assert alert.source.platform == "github"
    assert alert.source.channel == "MovingBlocks/Terasology"
    assert alert.content.type == "Issue"
    assert "Fix rendering bug on ARM Macs" in alert.content.body
    assert "github.com/MovingBlocks/Terasology/issues/5678" in alert.content.url


def test_deduplication_skips_seen_notifications():
    watcher = make_watcher()
    notification = {
        "id": "99999",
        "subject": {
            "title": "Some issue",
            "type": "Issue",
            "url": "https://api.github.com/repos/MovingBlocks/Terasology/issues/1",
        },
        "repository": {
            "full_name": "MovingBlocks/Terasology",
            "html_url": "https://github.com/MovingBlocks/Terasology",
        },
        "reason": "mention",
        "updated_at": "2026-04-02T10:00:00Z",
    }

    alert1 = watcher.parse_notification(notification)
    assert alert1 is not None

    alert2 = watcher.parse_notification(notification)
    assert alert2 is None
