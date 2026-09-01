import pytest

from src.publisher.adapters.matrix import MatrixPublisher
from src.publisher.protocols import capabilities_of
from src.publisher.schemas import ObjectRef
from src.watchers.schemas import Content


class _FakeClient:
    """Models MatrixAdminClient — synchronous, send_event + resolve_alias."""

    def __init__(self, event_id="$evt1", raises=None):
        self.sent = []
        self.joined = []
        self._event_id = event_id
        self._raises = raises

    def send_event(self, room_id, content, txn_id=None):
        if self._raises:
            raise self._raises
        self.sent.append({"room_id": room_id, "content": content})
        return self._event_id

    def resolve_alias(self, alias):
        return "!resolved:knarr.local"

    def join_room(self, room_id):
        self.joined.append(room_id)


def _content(body="hello", title=""):
    return Content(type="post", title=title, body=body, author="knarr", attachments=[])


def _pub(client):
    return MatrixPublisher(
        id="mtl-matrix", scope="community/mtl",
        room_id="!room:knarr.local", client=client,
    )


def test_declares_post_and_comment_but_not_message():
    caps = capabilities_of(_pub(_FakeClient()))
    assert "post" in caps and "comment" in caps
    assert "message" not in caps


@pytest.mark.asyncio
async def test_post_returns_the_matrix_event_id():
    client = _FakeClient("$abc")
    result = await _pub(client).post(_content())
    assert result.ok is True
    assert result.ref.object_id == "$abc"
    assert result.ref.publisher_id == "mtl-matrix"
    assert result.ref.object_type == "post"


@pytest.mark.asyncio
async def test_post_escapes_html_in_the_formatted_body():
    client = _FakeClient()
    await _pub(client).post(_content(body="<img src=x onerror=y>"))
    sent = client.sent[0]["content"]
    assert "<img" not in sent["formatted_body"]
    assert "&lt;img" in sent["formatted_body"]
    assert sent["body"] == "<img src=x onerror=y>", "plain body stays literal"


@pytest.mark.asyncio
async def test_comment_threads_under_the_parent():
    client = _FakeClient()
    parent = ObjectRef(publisher_id="mtl-matrix", platform="matrix",
                       surface="room", object_type="post", object_id="$root")
    await _pub(client).comment(parent, _content(body="reply"))
    rel = client.sent[0]["content"]["m.relates_to"]
    assert rel["event_id"] == "$root"


@pytest.mark.asyncio
async def test_a_rejected_send_is_reported_not_swallowed():
    """A publisher must never claim a post it cannot produce an id for —
    the silent-success bug the router shipped, pointed outward."""
    result = await _pub(_FakeClient(event_id="")).post(_content())
    assert result.ok is False
    assert result.ref is None
    assert "event_id" in result.error


@pytest.mark.asyncio
async def test_transport_exception_becomes_a_failed_result():
    client = _FakeClient(raises=ConnectionResetError("reset"))
    result = await _pub(client).post(_content())
    assert result.ok is False
    assert "reset" in result.error


@pytest.mark.asyncio
async def test_an_alias_target_is_resolved_to_a_room_id():
    """Config names rooms by alias because aliases survive a cluster rebuild
    and room ids do not."""
    client = _FakeClient()
    pub = MatrixPublisher(id="p", scope="community/mtl",
                          room_id="#social-watch:knarr.local", client=client)
    await pub.post(_content())
    assert client.sent[0]["room_id"] == "!resolved:knarr.local"


@pytest.mark.asyncio
async def test_joins_the_room_before_sending():
    """The reconciler invites but does not accept. An invited-but-unjoined
    user gets 403 on every send — found live during Phase A validation."""
    client = _FakeClient()
    await _pub(client).post(_content())
    assert client.joined == ["!room:knarr.local"]


@pytest.mark.asyncio
async def test_joins_only_once_across_sends():
    client = _FakeClient()
    pub = _pub(client)
    await pub.post(_content())
    await pub.post(_content("again"))
    assert len(client.joined) == 1
    assert len(client.sent) == 2


@pytest.mark.asyncio
async def test_an_unresolvable_alias_fails_rather_than_guessing():
    class _NoAlias(_FakeClient):
        def resolve_alias(self, alias):
            return None

    pub = MatrixPublisher(id="p", scope="community/mtl",
                          room_id="#missing:knarr.local", client=_NoAlias())
    result = await pub.post(_content())
    assert result.ok is False
    assert "does not resolve" in result.error


def test_validate_rejects_an_empty_body():
    assert _pub(_FakeClient()).validate("post", _content(body="")) != []


def test_validate_accepts_a_normal_post():
    assert _pub(_FakeClient()).validate("post", _content()) == []
