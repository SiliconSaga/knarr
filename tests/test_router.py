from src.router.kafka_consumer import format_alert_message
from src.watchers.schemas import WatchAlert, Source, Content


def test_format_reddit_alert():
    alert = WatchAlert(
        source=Source(platform="reddit", channel="r/Terasology", community="terasology"),
        content=Content(
            type="new_post",
            body="**Cool build** by u/gamer42",
            url="https://www.reddit.com/r/Terasology/comments/abc123/cool_build/",
        ),
    )

    message = format_alert_message(alert)

    assert "Reddit" in message
    assert "r/Terasology" in message
    assert "Cool build" in message
    assert "reddit.com" in message


def test_format_github_alert():
    alert = WatchAlert(
        source=Source(platform="github", channel="MovingBlocks/Terasology", community="terasology"),
        content=Content(
            type="Issue",
            body="[Issue] Fix rendering bug on ARM Macs",
            url="https://github.com/MovingBlocks/Terasology/issues/5678",
        ),
    )

    message = format_alert_message(alert)

    assert "GitHub" in message or "github" in message.lower()
    assert "MovingBlocks/Terasology" in message
    assert "Fix rendering bug" in message
