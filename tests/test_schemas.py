from datetime import UTC, datetime

from src.watchers.schemas import (
    Attachment,
    Content,
    WatchAlert,
)


def _sample_alert() -> WatchAlert:
    return WatchAlert(
        event_id="reddit_t3_abc123",
        instance_id="reddit-terasology",
        scope="community/terasology",
        access_path="api",
        platform="reddit",
        raw_post_ref="https://reddit.com/r/Terasology/comments/abc123/foo",
        content=Content(
            type="post",
            title="A new release",
            body="Body text here",
            author="cervator",
            attachments=[],
        ),
        timestamp="2026-05-31T12:00:00+00:00",
        extracted_at="2026-05-31T12:00:01+00:00",
    )


def test_watch_alert_serializes_to_dict():
    alert = _sample_alert()
    data = alert.to_kafka_dict()
    assert data["event_id"] == "reddit_t3_abc123"
    assert data["instance_id"] == "reddit-terasology"
    assert data["scope"] == "community/terasology"
    assert data["access_path"] == "api"
    assert data["platform"] == "reddit"
    assert data["raw_post_ref"] == "https://reddit.com/r/Terasology/comments/abc123/foo"
    assert data["content"]["type"] == "post"
    assert data["content"]["title"] == "A new release"
    assert data["content"]["body"] == "Body text here"
    assert data["content"]["author"] == "cervator"
    assert data["content"]["attachments"] == []
    assert data["timestamp"] == "2026-05-31T12:00:00+00:00"
    assert data["extracted_at"] == "2026-05-31T12:00:01+00:00"


def test_watch_alert_from_kafka_dict_roundtrip():
    original = _sample_alert()
    data = original.to_kafka_dict()
    rebuilt = WatchAlert.from_kafka_dict(data)
    assert rebuilt == original


def test_attachment_serializes():
    alert = WatchAlert(
        event_id="github_release_1.0",
        instance_id="github-terasology",
        scope="community/terasology",
        access_path="api",
        platform="github",
        raw_post_ref="https://github.com/MovingBlocks/Terasology/releases/tag/v1.0",
        content=Content(
            type="release",
            title="v1.0",
            body="Release notes",
            author="MovingBlocks",
            attachments=[
                Attachment(url="https://github.com/.../asset.zip", mime_hint="application/zip")
            ],
        ),
        timestamp="2026-05-31T12:00:00+00:00",
        extracted_at="2026-05-31T12:00:01+00:00",
    )
    data = alert.to_kafka_dict()
    assert data["content"]["attachments"][0]["url"].endswith("asset.zip")
    assert data["content"]["attachments"][0]["mime_hint"] == "application/zip"
    rebuilt = WatchAlert.from_kafka_dict(data)
    assert rebuilt == alert
    assert rebuilt.content.attachments[0].url.endswith("asset.zip")
    assert rebuilt.content.attachments[0].mime_hint == "application/zip"


def test_extracted_at_defaults_to_now():
    """Default extracted_at fires at construction time."""
    before = datetime.now(UTC).isoformat()
    alert = WatchAlert(
        event_id="x",
        instance_id="x",
        scope="community/x",
        access_path="api",
        platform="x",
        raw_post_ref="https://example.com",
        content=Content(type="post", title="", body="", author="", attachments=[]),
        timestamp="2026-05-31T00:00:00+00:00",
    )
    after = datetime.now(UTC).isoformat()
    assert before <= alert.extracted_at <= after
