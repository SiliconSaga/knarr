from src.router.kafka_consumer import deserialize_alert, format_alert_message
from src.watchers.schemas import Attachment, Content, WatchAlert


def _reddit_alert() -> WatchAlert:
    return WatchAlert(
        event_id="t3_post1",
        instance_id="reddit-terasology",
        scope="community/terasology",
        access_path="api",
        platform="reddit",
        raw_post_ref="https://www.reddit.com/r/Terasology/comments/post1/title/",
        content=Content(
            type="post",
            title="First post",
            body="Hello world",
            author="cervator",
            attachments=[],
        ),
        timestamp="2026-05-31T12:00:00+00:00",
    )


def _github_alert() -> WatchAlert:
    return WatchAlert(
        event_id="1001",
        instance_id="github-terasology",
        scope="community/terasology",
        access_path="api",
        platform="github",
        raw_post_ref="https://github.com/MovingBlocks/Terasology/issues/42",
        content=Content(
            type="issue",
            title="Some bug",
            body="",
            author="MovingBlocks/Terasology",
            attachments=[
                Attachment(url="https://github.com/.../screen.png",
                           mime_hint="image/png"),
            ],
        ),
        timestamp="2026-05-31T12:00:00+00:00",
    )


def test_deserialize_round_trips_new_envelope_from_bytes():
    """deserialize_alert accepts the raw bytes Kafka hands the consumer."""
    import json
    original = _reddit_alert()
    raw_bytes = json.dumps(original.to_kafka_dict()).encode("utf-8")
    rebuilt = deserialize_alert(raw_bytes)
    assert rebuilt == original


def test_deserialize_alert_returns_none_on_malformed_bytes():
    """Survives one bad event without crashing the consumer loop."""
    assert deserialize_alert(b"not json") is None


def test_format_reddit_alert_uses_emoji_header_and_full_body():
    msg = format_alert_message(_reddit_alert())
    assert "Reddit" in msg
    assert "reddit-terasology" in msg
    assert "Hello world" in msg
    assert "https://www.reddit.com/r/Terasology/comments/post1/title/" in msg


def test_format_github_alert_uses_emoji_header_and_instance_id():
    msg = format_alert_message(_github_alert())
    assert "Github" in msg
    assert "github-terasology" in msg
    assert "https://github.com/MovingBlocks/Terasology/issues/42" in msg
