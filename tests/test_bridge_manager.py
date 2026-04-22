"""Tests for BridgeManager — verifies command sequencing without a live bridge."""

import json
from unittest.mock import MagicMock, patch, call

import pytest

from src.admin.bridge import BridgeManager


@pytest.fixture(autouse=True)
def no_sleep():
    with patch("src.admin.bridge.time.sleep"):
        yield


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.homeserver = "http://test:8008"
    return client


def test_login_bot_sends_command_to_management_room(mock_client):
    mock_client.send_message.return_value = "$evt1"
    mock_client.get_messages.return_value = [
        {"content": {"body": "Successfully logged in as @Knarr"}},
        {"content": {"body": "Connecting to Discord as user ID 12345"}},
    ]

    mgr = BridgeManager(mock_client, management_room="!mgmt:test")
    result = mgr.login_bot("fake-discord-token")

    mock_client.send_message.assert_called_once_with(
        "!mgmt:test", "login-token bot fake-discord-token"
    )
    assert "Successfully logged in" in result


def test_bridge_channel_sends_command_and_sets_relay(mock_client):
    call_count = {"n": 0}

    def track_sends(room_id, body):
        call_count["n"] += 1
        return f"$evt{call_count['n']}"

    mock_client.send_message.side_effect = track_sends
    mock_client.get_messages.side_effect = [
        [{"content": {"body": "Room successfully bridged"}}],
        [{"content": {"body": "Saved webhook mautrix (123) as portal relay webhook"}}],
    ]

    mgr = BridgeManager(mock_client, management_room="!mgmt:test")
    result = mgr.bridge_channel("!room:test", "1342947610008485921")

    calls = mock_client.send_message.call_args_list
    assert calls[0] == call("!room:test", "!discord bridge 1342947610008485921")
    assert calls[1] == call("!room:test", "!discord set-relay --create")
    assert "bridged" in result.lower()


def test_bridge_channel_with_replace(mock_client):
    mock_client.send_message.return_value = "$evt1"
    mock_client.get_messages.side_effect = [
        [{"content": {"body": "Room successfully bridged"}}],
        [{"content": {"body": "Saved webhook"}}],
    ]

    mgr = BridgeManager(mock_client, management_room="!mgmt:test")
    mgr.bridge_channel("!room:test", "123456", replace=True)

    first_call_body = mock_client.send_message.call_args_list[0][0][1]
    assert "--replace" in first_call_body


def test_create_and_bridge_creates_room_first(mock_client):
    mock_client.create_room.return_value = "!new:test"
    mock_client.send_message.return_value = "$evt1"
    mock_client.get_messages.side_effect = [
        [{"content": {"body": "Room successfully bridged"}}],
        [{"content": {"body": "Saved webhook"}}],
    ]

    mgr = BridgeManager(mock_client, management_room="!mgmt:test")
    room_id = mgr.create_and_bridge(
        channel_id="1342947610008485921",
        room_name="my-channel (Discord)",
        invite=["@cervator:test"],
    )

    assert room_id == "!new:test"
    mock_client.create_room.assert_called_once()
    create_kwargs = mock_client.create_room.call_args
    assert "@discordbot:test" not in create_kwargs[1].get("invite", [])
    # discordbot is invited via the invite param
    assert "@cervator:test" in create_kwargs[1]["invite"]


def test_ping_sends_ping_command(mock_client):
    mock_client.send_message.return_value = "$evt1"
    mock_client.get_messages.return_value = [
        {"content": {"body": "logged in as @Knarr, connected to discord"}},
    ]

    mgr = BridgeManager(mock_client, management_room="!mgmt:test")
    result = mgr.ping()

    mock_client.send_message.assert_called_once_with("!mgmt:test", "ping")
    assert "logged in" in result.lower() or "connected" in result.lower()
