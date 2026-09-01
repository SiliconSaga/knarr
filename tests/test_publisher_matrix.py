import pytest

from src.publisher.adapters.matrix import MatrixPublisher
from src.publisher.protocols import capabilities_of
from src.publisher.schemas import ObjectRef
from src.watchers.schemas import Content


class _FakeSendResponse:
    def __init__(self, event_id):
        self.event_id = event_id


class _FakeClient:
    def __init__(self, response=None):
        self.sent = []
        self._response = response or _FakeSendResponse("$evt1")

    async def room_send(self, room_id, message_type, content, tx_id=None):
        self.sent.append({"room_id": room_id, "content": content, "tx_id": tx_id})
        return self._response


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
    client = _FakeClient(_FakeSendResponse("$abc"))
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
    """nio RETURNS errors rather than raising. An unchecked send reports a
    success that never happened — the exact bug the router shipped."""
    class _Err:
        message = "M_FORBIDDEN"

    result = await _pub(_FakeClient(_Err())).post(_content())
    assert result.ok is False
    assert result.ref is None
    assert "M_FORBIDDEN" in result.error


@pytest.mark.asyncio
async def test_transport_exception_becomes_a_failed_result():
    class _Boom:
        async def room_send(self, *a, **k):
            raise ConnectionResetError("reset")

    result = await _pub(_Boom()).post(_content())
    assert result.ok is False
    assert "reset" in result.error


def test_validate_rejects_an_empty_body():
    assert _pub(_FakeClient()).validate("post", _content(body="")) != []


def test_validate_accepts_a_normal_post():
    assert _pub(_FakeClient()).validate("post", _content()) == []
