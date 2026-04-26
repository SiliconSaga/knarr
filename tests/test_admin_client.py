"""Tests for MatrixAdminClient — uses httpx mock transport, no live server needed."""

import json
import time
from unittest.mock import patch

import httpx
import pytest

from src.admin.client import MatrixAdminClient


def mock_transport(responses: dict):
    """Create an httpx mock transport that returns canned responses by path."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for pattern, (status, body) in responses.items():
            if pattern in path:
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={"errcode": "M_NOT_FOUND"})

    return httpx.MockTransport(handler)


def make_client(responses: dict) -> MatrixAdminClient:
    transport = mock_transport(responses)
    client = MatrixAdminClient(
        homeserver="http://test:8008",
        admin_user="admin",
        admin_password="secret",
    )
    client._http = httpx.Client(transport=transport)
    return client


def test_login_returns_and_caches_token():
    client = make_client({
        "/login": (200, {"access_token": "tok_abc123"}),
    })

    token = client.get_token()
    assert token == "tok_abc123"
    assert client._token == "tok_abc123"


def test_login_caches_across_calls():
    call_count = 0

    def counting_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        if "/login" in request.url.path:
            call_count += 1
            return httpx.Response(200, json={"access_token": "tok_abc123"})
        return httpx.Response(404, json={})

    client = MatrixAdminClient(
        homeserver="http://test:8008",
        admin_user="admin",
        admin_password="secret",
    )
    client._http = httpx.Client(transport=httpx.MockTransport(counting_handler))

    client.get_token()
    client.get_token()
    assert call_count == 1


def test_create_room_returns_room_id():
    client = make_client({
        "/login": (200, {"access_token": "tok_abc123"}),
        "/createRoom": (200, {"room_id": "!newroom:test"}),
    })

    room_id = client.create_room("test-room")
    assert room_id == "!newroom:test"


def test_create_room_with_invites():
    requests_seen = []

    def capturing_handler(request: httpx.Request) -> httpx.Response:
        if "/login" in request.url.path:
            return httpx.Response(200, json={"access_token": "tok"})
        if "/createRoom" in request.url.path:
            requests_seen.append(json.loads(request.content))
            return httpx.Response(200, json={"room_id": "!r:test"})
        return httpx.Response(404, json={})

    client = MatrixAdminClient("http://test:8008", "admin", "secret")
    client._http = httpx.Client(transport=httpx.MockTransport(capturing_handler))

    client.create_room("my-room", invite=["@bob:test", "@alice:test"])
    assert "@bob:test" in requests_seen[0]["invite"]
    assert "@alice:test" in requests_seen[0]["invite"]


def test_invite_user():
    client = make_client({
        "/login": (200, {"access_token": "tok"}),
        "/invite": (200, {}),
    })

    client.invite("!room:test", "@user:test")


def test_send_message_returns_event_id():
    client = make_client({
        "/login": (200, {"access_token": "tok"}),
        "/send/": (200, {"event_id": "$evt123"}),
    })

    event_id = client.send_message("!room:test", "hello world")
    assert event_id == "$evt123"


def test_register_user():
    requests_seen = []

    def capturing_handler(request: httpx.Request) -> httpx.Response:
        if "/login" in request.url.path:
            return httpx.Response(200, json={"access_token": "tok"})
        if "/_synapse/admin/v2/users/" in request.url.path:
            requests_seen.append(json.loads(request.content))
            return httpx.Response(200, json={"name": "@newuser:test"})
        return httpx.Response(404, json={})

    client = MatrixAdminClient("http://test:8008", "admin", "secret")
    client._http = httpx.Client(transport=httpx.MockTransport(capturing_handler))

    user_id = client.register_user("newuser", "password123", admin=False, server_name="test")
    assert user_id == "@newuser:test"
    assert requests_seen[0]["password"] == "password123"
    assert requests_seen[0]["admin"] is False


def test_get_room_messages():
    client = make_client({
        "/login": (200, {"access_token": "tok"}),
        "/messages": (200, {
            "chunk": [
                {
                    "type": "m.room.message",
                    "sender": "@bob:test",
                    "content": {"msgtype": "m.text", "body": "hello"},
                    "origin_server_ts": 1776000000000,
                },
            ],
        }),
    })

    messages = client.get_messages("!room:test", limit=5)
    assert len(messages) == 1
    assert messages[0]["content"]["body"] == "hello"


def test_resolve_alias_returns_room_id():
    client = make_client({
        "/login": (200, {"access_token": "tok"}),
        "/directory/room/": (200, {"room_id": "!found:test"}),
    })
    room_id = client.resolve_alias("#test:test")
    assert room_id == "!found:test"


def test_resolve_alias_returns_none_for_missing():
    client = make_client({
        "/login": (200, {"access_token": "tok"}),
    })
    room_id = client.resolve_alias("#missing:test")
    assert room_id is None


def test_resolve_alias_returns_none_on_400():
    """Synapse returns 400 (not 404) for malformed/non-existent aliases."""
    client = make_client({
        "/login": (200, {"access_token": "tok"}),
        "/directory/room/": (400, {"errcode": "M_NOT_FOUND"}),
    })
    assert client.resolve_alias("#weird:test") is None


def test_get_room_state_returns_name_and_topic():
    client = make_client({
        "/login": (200, {"access_token": "tok"}),
        "/state": (200, [
            {"type": "m.room.name", "content": {"name": "My Room"}},
            {"type": "m.room.topic", "content": {"topic": "A topic"}},
            {"type": "m.room.join_rules", "content": {"join_rule": "invite"}},
        ]),
    })
    state = client.get_room_state("!room:test")
    assert state["name"] == "My Room"
    assert state["topic"] == "A topic"
    assert state["join_rule"] == "invite"


def test_get_room_members():
    client = make_client({
        "/login": (200, {"access_token": "tok"}),
        "/joined_members": (200, {
            "joined": {
                "@alice:test": {},
                "@bob:test": {},
            },
        }),
    })
    members = client.get_room_members("!room:test")
    assert sorted(members) == ["@alice:test", "@bob:test"]


def test_set_room_alias():
    requests_seen = []

    def capturing_handler(request: httpx.Request) -> httpx.Response:
        if "/login" in request.url.path:
            return httpx.Response(200, json={"access_token": "tok"})
        if "/directory/room/" in request.url.path:
            requests_seen.append({
                "method": request.method,
                "path": request.url.path,
                "body": json.loads(request.content) if request.content else None,
            })
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})

    client = MatrixAdminClient("http://test:8008", "admin", "secret")
    client._http = httpx.Client(transport=httpx.MockTransport(capturing_handler))

    client.set_room_alias("!room:test", "#myalias:test")
    assert requests_seen[-1]["method"] == "PUT"
    assert "myalias" in requests_seen[-1]["path"]
    assert requests_seen[-1]["body"] == {"room_id": "!room:test"}


def test_create_space_returns_room_id():
    requests_seen = []

    def capturing_handler(request: httpx.Request) -> httpx.Response:
        if "/login" in request.url.path:
            return httpx.Response(200, json={"access_token": "tok"})
        if "/createRoom" in request.url.path:
            requests_seen.append(json.loads(request.content))
            return httpx.Response(200, json={"room_id": "!space:test"})
        return httpx.Response(404, json={})

    client = MatrixAdminClient("http://test:8008", "admin", "secret")
    client._http = httpx.Client(transport=httpx.MockTransport(capturing_handler))

    space_id = client.create_space("My Space", alias="myspace")
    assert space_id == "!space:test"
    body = requests_seen[0]
    assert body["creation_content"]["type"] == "m.space"
    assert "myspace" in body.get("room_alias_name", "")


def test_add_space_child():
    requests_seen = []

    def capturing_handler(request: httpx.Request) -> httpx.Response:
        if "/login" in request.url.path:
            return httpx.Response(200, json={"access_token": "tok"})
        if "/state/m.space.child" in request.url.path:
            requests_seen.append(json.loads(request.content))
            return httpx.Response(200, json={"event_id": "$evt"})
        return httpx.Response(404, json={})

    client = MatrixAdminClient("http://test:8008", "admin", "secret")
    client._http = httpx.Client(transport=httpx.MockTransport(capturing_handler))

    client.add_space_child("!space:test", "!child:test")
    assert requests_seen[0]["via"] == ["test"]


def test_set_room_state_event():
    client = make_client({
        "/login": (200, {"access_token": "tok"}),
        "/state/org.knarr.managed": (200, {"event_id": "$evt"}),
    })
    client.set_room_state_event("!room:test", "org.knarr.managed", {"key": "val"})


def test_get_room_state_event_found():
    client = make_client({
        "/login": (200, {"access_token": "tok"}),
        "/state/org.knarr.managed": (200, {"key": "val"}),
    })
    result = client.get_room_state_event("!room:test", "org.knarr.managed")
    assert result == {"key": "val"}


def test_get_room_state_event_missing():
    client = make_client({
        "/login": (200, {"access_token": "tok"}),
    })
    result = client.get_room_state_event("!room:test", "org.knarr.managed")
    assert result is None
