import pytest

from src.publisher.adapters.discord import DiscordPublisher
from src.publisher.protocols import capabilities_of
from src.watchers.schemas import Content


class _FakeClient:
    """Models MatrixAdminClient — synchronous, send_event + resolve_alias."""

    def __init__(self):
        self.sent = []
        self.joined = []

    def send_event(self, room_id, content, txn_id=None):
        self.sent.append({"room_id": room_id, "content": content})
        return "$evt"

    def resolve_alias(self, alias):
        return "!resolved:knarr.local"

    def join_room(self, room_id):
        self.joined.append(room_id)


def _content(body="hello"):
    return Content(type="post", title="", body=body, author="knarr", attachments=[])


def _pub(client):
    return DiscordPublisher(
        id="mtl-discord", scope="community/mtl",
        room_id="!bridged:knarr.local", client=client,
    )


def test_reports_discord_as_the_platform_not_matrix():
    """The bridge is transport. A caller asking where this went must be told
    Discord, or the ObjectRef cannot route a reply."""
    pub = _pub(_FakeClient())
    assert pub.platform == "discord"
    assert pub.surface == "channel"


@pytest.mark.asyncio
async def test_post_records_discord_in_the_object_ref():
    client = _FakeClient()
    result = await _pub(client).post(_content())
    assert result.ok is True
    assert result.ref.platform == "discord"
    assert result.ref.publisher_id == "mtl-discord"


def test_declares_post_only():
    """Via the bridge there is no comment or message surface — Discord
    threads and DMs are not expressible this way."""
    assert capabilities_of(_pub(_FakeClient())) == frozenset({"post"})


@pytest.mark.asyncio
async def test_sends_through_the_bridged_room():
    client = _FakeClient()
    await _pub(client).post(_content("hi"))
    assert client.sent[0]["room_id"] == "!bridged:knarr.local"


def test_validate_rejects_over_discord_message_limit():
    """2000 characters is Discord's limit, and the bridge does not raise it."""
    long_body = "x" * 2100
    assert _pub(_FakeClient()).validate("post", _content(long_body)) != []


def test_validate_accepts_a_normal_message():
    assert _pub(_FakeClient()).validate("post", _content()) == []


@pytest.mark.parametrize("title,body", [
    ("", "!discord unbridge"), ("  !Discord set-relay", "body"),
])
def test_validate_rejects_bridge_command_text(title, body):
    content = Content(type="post", title=title, body=body,
                      author="knarr", attachments=[])
    violations = _pub(_FakeClient()).validate("post", content)
    assert any("command" in v.reason for v in violations)


def test_validate_counts_title_and_joining_newline():
    """The transport sends title + newline + body, so that is what must fit."""
    content = Content(type="post", title="t" * 1000, body="b" * 1000,
                      author="knarr", attachments=[])
    assert _pub(_FakeClient()).validate("post", content) != []
