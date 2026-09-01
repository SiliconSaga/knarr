from src.router.kafka_consumer import (
    deserialize_alert,
    format_alert_html,
    format_alert_message,
)
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


def test_deserialize_alert_survives_invalid_utf8():
    """Invalid UTF-8 raises UnicodeDecodeError, which is not a JSONDecodeError.

    The original narrower catch let this through and it would have killed the
    consumer loop on a single corrupt record.
    """
    assert deserialize_alert(b"\xff\xfe\xfd") is None


def test_deserialize_alert_survives_valid_json_of_the_wrong_shape():
    """b"[]" decodes cleanly, then subscripting a list raises TypeError."""
    assert deserialize_alert(b"[]") is None
    assert deserialize_alert(b'"a string"') is None
    assert deserialize_alert(b"42") is None


def test_deserialize_alert_rejects_wrong_typed_nested_scalars():
    """A present-but-wrong-typed nested field must fail HERE, not in formatting.

    `content.title` as a list used to pass deserialization untouched and then
    raise TypeError inside `"\\n".join(...)` — outside the consumer's guard,
    so one bad record killed the loop rather than being skipped.
    """
    import json
    payload = _reddit_alert().to_kafka_dict()
    payload["content"]["title"] = ["unexpected"]
    assert deserialize_alert(json.dumps(payload).encode()) is None


def test_deserialize_alert_rejects_wrong_typed_top_level_scalars():
    import json
    payload = _reddit_alert().to_kafka_dict()
    payload["platform"] = {"not": "a string"}
    assert deserialize_alert(json.dumps(payload).encode()) is None


def test_deserialize_alert_rejects_non_object_content():
    import json
    payload = _reddit_alert().to_kafka_dict()
    payload["content"] = "just a string"
    assert deserialize_alert(json.dumps(payload).encode()) is None


def test_html_body_escapes_untrusted_content():
    """Alert bodies come from Reddit and GitHub — they are not trusted markup.

    formatted_body used to be derived from the plain text by replacing
    newlines, which put attacker-controlled HTML straight into the field
    Matrix clients render as markup.
    """
    alert = _reddit_alert()
    alert.content.body = '<img src=x onerror="alert(1)">'
    html = format_alert_html(alert)

    assert "<img" not in html
    assert "&lt;img" in html
    assert "onerror=" not in html or "&quot;" in html
    # The tags this function adds itself are still real markup.
    assert "<strong>" in html


def test_html_body_escapes_a_crafted_link_ref():
    alert = _reddit_alert()
    alert.raw_post_ref = 'https://x/"><script>bad()</script>'
    html = format_alert_html(alert)

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_plain_body_is_not_html_escaped():
    """`body` is the plain-text field — escaping it would show entities."""
    alert = _reddit_alert()
    alert.content.body = "5 > 3 & rising"
    assert "5 > 3 & rising" in format_alert_message(alert)


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


def test_github_alert_renders_its_title():
    """GitHub's /notifications payload has no body — the title IS the content.

    Rendering only the body produced a header, a blank line and a link, with
    no indication of what the notification was about.
    """
    alert = _github_alert()
    assert alert.content.body == "", "fixture must keep the empty-body shape"

    assert "Some bug" in format_alert_message(alert)
    assert "Some bug" in format_alert_html(alert)


def test_title_is_escaped_in_html():
    alert = _github_alert()
    alert.content.title = "<b>not bold</b>"
    html = format_alert_html(alert)
    assert "<b>not bold</b>" not in html
    assert "&lt;b&gt;" in html
