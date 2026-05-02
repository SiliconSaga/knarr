from src.watchers.schemas import Content, Source, WatchAlert


def test_watch_alert_serializes_to_dict():
    alert = WatchAlert(
        source=Source(
            platform="reddit",
            channel="r/Terasology",
            community="terasology",
        ),
        content=Content(
            type="mention",
            body="Check out this Terasology build!",
            url="https://reddit.com/r/Terasology/comments/abc123",
        ),
    )
    data = alert.to_kafka_dict()

    assert data["source"]["platform"] == "reddit"
    assert data["source"]["community"] == "terasology"
    assert data["content"]["type"] == "mention"
    assert data["content"]["url"] == "https://reddit.com/r/Terasology/comments/abc123"
    assert "event_id" in data
    assert "timestamp" in data


def test_watch_alert_from_kafka_dict_roundtrip():
    alert = WatchAlert(
        source=Source(
            platform="github",
            channel="MovingBlocks/Terasology",
            community="terasology",
        ),
        content=Content(
            type="notification",
            body="New issue #1234: Fix rendering bug",
        ),
    )
    data = alert.to_kafka_dict()
    restored = WatchAlert.from_kafka_dict(data)

    assert restored.source.platform == "github"
    assert restored.content.body == "New issue #1234: Fix rendering bug"
    assert restored.event_id == alert.event_id
