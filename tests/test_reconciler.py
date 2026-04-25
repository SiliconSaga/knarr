"""Tests for the Reconciler — diff engine and action ordering."""

from unittest.mock import MagicMock

import pytest

from src.admin.reconciler import Reconciler, Action
from src.admin.config_schema import (
    KnarrConfig,
    CommunityConfig,
    SpaceConfig,
    RoomConfig,
)


def make_config(rooms=None, spaces=None):
    """Helper to create a minimal KnarrConfig for testing."""
    room_configs = {}
    if rooms:
        for key, data in rooms.items():
            room_configs[key] = RoomConfig(
                alias=data.get("alias", key),
                name=data.get("name", key),
                topic=data.get("topic", ""),
                members=data.get("members", []),
                bridge=data.get("bridge"),
                watchers=data.get("watchers"),
            )

    child_spaces = {}
    if spaces:
        for key, data in spaces.items():
            child_rooms = {}
            for rk, rd in data.get("rooms", {}).items():
                child_rooms[rk] = RoomConfig(
                    alias=rd.get("alias", rk), name=rd.get("name", rk),
                    topic=rd.get("topic", ""), members=rd.get("members", []),
                    bridge=rd.get("bridge"), watchers=rd.get("watchers"),
                )
            child_spaces[key] = SpaceConfig(
                name=data.get("name", key),
                visibility=data.get("visibility", "private"),
                members=data.get("members", []),
                rooms=child_rooms,
            )

    space = SpaceConfig(
        name="Test Space",
        visibility="private",
        members=["admin"],
        rooms=room_configs,
        children=child_spaces,
    )
    community = CommunityConfig(
        community="test",
        display_name="Test",
        spaces={"test-space": space},
    )
    return KnarrConfig(
        server_name="test.local",
        users={
            "admin": "@admin:test.local",
            "router": "@router:test.local",
            "bridge_bot": "@discordbot:test.local",
        },
        communities=[community],
    )


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.homeserver = "http://test:8008"
    # Default: nothing exists
    client.resolve_alias.return_value = None
    client.get_room_state.return_value = {}
    client.get_room_members.return_value = []
    client.get_room_state_event.return_value = None
    client.create_space.return_value = "!space:test.local"
    client.create_room.return_value = "!room:test.local"
    return client


def test_diff_creates_missing_space(mock_client):
    config = make_config()
    reconciler = Reconciler(mock_client, config)
    report = reconciler.diff()

    space_actions = [a for a in report.actions if "space:test-space" in a.resource]
    assert any(a.operation == "create" for a in space_actions)


def test_diff_creates_missing_room(mock_client):
    config = make_config(rooms={
        "my-room": {"alias": "my-room", "name": "My Room", "topic": "Test"},
    })
    reconciler = Reconciler(mock_client, config)
    report = reconciler.diff()

    room_actions = [a for a in report.actions if "room:my-room" in a.resource]
    assert any(a.operation == "create" for a in room_actions)


def test_diff_skips_existing_room(mock_client):
    mock_client.resolve_alias.return_value = "!existing:test.local"
    mock_client.get_room_state.return_value = {"name": "My Room", "topic": "Test"}
    mock_client.get_room_members.return_value = ["@admin:test.local"]
    mock_client.get_room_state_event.return_value = {"config_key": "test/my-room"}

    config = make_config(rooms={
        "my-room": {"alias": "my-room", "name": "My Room", "topic": "Test", "members": ["admin"]},
    })
    reconciler = Reconciler(mock_client, config)
    report = reconciler.diff()

    room_actions = [a for a in report.actions if "room:my-room" in a.resource]
    assert all(a.operation == "skip" for a in room_actions)


def test_diff_invites_missing_members(mock_client):
    mock_client.resolve_alias.return_value = "!existing:test.local"
    mock_client.get_room_state.return_value = {"name": "My Room"}
    mock_client.get_room_members.return_value = ["@admin:test.local"]
    mock_client.get_room_state_event.return_value = {"config_key": "test/my-room"}

    config = make_config(rooms={
        "my-room": {
            "alias": "my-room", "name": "My Room",
            "members": ["admin", "router"],
        },
    })
    reconciler = Reconciler(mock_client, config)
    report = reconciler.diff()

    invite_actions = [a for a in report.actions if a.operation == "invite"]
    assert any("@router:test.local" in a.details for a in invite_actions)


def test_diff_detects_bridge_needed(mock_client):
    config = make_config(rooms={
        "bridged": {
            "alias": "bridged", "name": "Bridged",
            "bridge": {"discord": {"channel_id": "123", "relay": True}},
        },
    })
    reconciler = Reconciler(mock_client, config)
    report = reconciler.diff()

    bridge_actions = [a for a in report.actions if a.operation == "bridge"]
    assert len(bridge_actions) == 1


def test_diff_detects_watcher_config(mock_client):
    config = make_config(rooms={
        "watched": {
            "alias": "watched", "name": "Watched",
            "watchers": {"reddit": {"subreddit": "Test", "interval_seconds": 300}},
        },
    })
    reconciler = Reconciler(mock_client, config)
    report = reconciler.diff()

    watcher_actions = [a for a in report.actions if a.operation == "config"]
    assert len(watcher_actions) >= 1


def test_report_has_drift():
    from src.admin.reconciler import ReconcileReport
    report = ReconcileReport(
        actions=[Action("room:x", "create", "new room")],
        errors=[],
    )
    assert report.has_drift is True


def test_report_no_drift():
    from src.admin.reconciler import ReconcileReport
    report = ReconcileReport(
        actions=[Action("room:x", "skip", "up to date")],
        errors=[],
    )
    assert report.has_drift is False


def test_actions_ordered_spaces_before_rooms(mock_client):
    config = make_config(rooms={
        "a-room": {"alias": "a-room", "name": "A"},
    })
    reconciler = Reconciler(mock_client, config)
    report = reconciler.diff()

    ops = [(a.resource, a.operation) for a in report.actions if a.operation == "create"]
    space_idx = next(i for i, (r, _) in enumerate(ops) if "space:" in r)
    room_idx = next(i for i, (r, _) in enumerate(ops) if "room:" in r)
    assert space_idx < room_idx
