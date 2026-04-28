"""Tests for the Reconciler — diff engine and action ordering."""

from unittest.mock import MagicMock

import pytest

from src.admin.config_schema import (
    CommunityConfig,
    KnarrConfig,
    RoomConfig,
    SpaceConfig,
)
from src.admin.reconciler import Action, Reconciler


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
    # admin_user is read by Reconciler._creator_mxid() to filter the creator
    # out of invite lists; matches the "admin" user in make_config().
    client.admin_user = "admin"
    # Default: nothing exists
    client.resolve_alias.return_value = None
    client.get_room_state.return_value = {}
    client.get_room_members.return_value = []
    # Reconciler reads with_state (invite+join) for member diffs; default empty.
    client.get_room_members_with_state.return_value = []
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


def _managed(key: str) -> dict:
    """Build a marker payload that satisfies _managed_by_us for a given key."""
    return {"config_key": key, "managed_by": "knarr-reconciler"}


def test_diff_skips_existing_room(mock_client):
    mock_client.resolve_alias.return_value = "!existing:test.local"
    mock_client.get_room_state.return_value = {"name": "My Room", "topic": "Test"}
    mock_client.get_room_members_with_state.return_value = ["@admin:test.local"]
    mock_client.get_room_state_event.return_value = _managed("my-room")

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
    mock_client.get_room_members_with_state.return_value = ["@admin:test.local"]
    mock_client.get_room_state_event.return_value = _managed("my-room")

    config = make_config(rooms={
        "my-room": {
            "alias": "my-room", "name": "My Room",
            "members": ["admin", "router"],
        },
    })
    reconciler = Reconciler(mock_client, config)
    report = reconciler.diff()

    invite_actions = [a for a in report.actions if a.operation == "invite"]
    assert any(a.subject == "@router:test.local" for a in invite_actions)
    assert any(a.target == "room:my-room" for a in invite_actions)


def test_apply_invite_uses_structured_target(mock_client):
    """Invite actions carry target/subject fields, not parsed from details."""
    mock_client.resolve_alias.return_value = "!existing:test.local"
    mock_client.get_room_state.return_value = {"name": "My Room"}
    mock_client.get_room_members_with_state.return_value = []
    mock_client.get_room_state_event.return_value = _managed("my-room")

    config = make_config(rooms={
        "my-room": {
            "alias": "different-alias", "name": "My Room",
            "members": ["router"],
        },
    })
    reconciler = Reconciler(mock_client, config)
    reconciler.apply()

    # The reconciler should have invited router via the room_id from resolve_alias
    mock_client.invite.assert_called()
    invited_user_ids = [c.args[1] for c in mock_client.invite.call_args_list]
    assert "@router:test.local" in invited_user_ids


def test_diff_room_with_custom_alias_keys_room_ids_consistently(mock_client):
    """When room.alias differs from key, _room_ids is keyed by resource, not alias."""
    mock_client.resolve_alias.return_value = "!found:test.local"
    mock_client.get_room_state.return_value = {"name": "X"}
    mock_client.get_room_members_with_state.return_value = []
    mock_client.get_room_state_event.return_value = _managed("my-room")

    config = make_config(rooms={
        "my-room": {"alias": "different-alias", "name": "My Room"},
    })
    reconciler = Reconciler(mock_client, config)
    reconciler.diff()

    # _room_ids should be keyed by the resource string ("room:my-room"),
    # not by room.alias ("different-alias")
    assert "room:my-room" in reconciler._room_ids
    assert "different-alias" not in reconciler._room_ids


def test_diff_treats_foreign_managed_marker_as_adopt(mock_client):
    """A state event from a different reconciler should not be trusted as ours."""
    mock_client.resolve_alias.return_value = "!existing:test.local"
    mock_client.get_room_state.return_value = {"name": "My Room"}
    mock_client.get_room_members_with_state.return_value = []
    # Marker exists but managed_by is something else — could be a foreign tool
    # or a stale payload. Treat it as drift, not as "we own this".
    mock_client.get_room_state_event.return_value = {
        "managed_by": "some-other-tool",
        "config_key": "my-room",
    }

    config = make_config(rooms={
        "my-room": {"alias": "my-room", "name": "My Room"},
    })
    reconciler = Reconciler(mock_client, config)
    report = reconciler.diff()

    room_actions = [a for a in report.actions if a.resource == "room:my-room"]
    assert any(a.operation == "adopt" for a in room_actions)
    assert not any(a.operation == "skip" for a in room_actions)


def test_diff_treats_mismatched_config_key_as_adopt(mock_client):
    """If the marker's config_key points at a different room, don't trust it."""
    mock_client.resolve_alias.return_value = "!existing:test.local"
    mock_client.get_room_state.return_value = {"name": "My Room"}
    mock_client.get_room_members_with_state.return_value = []
    mock_client.get_room_state_event.return_value = {
        "managed_by": "knarr-reconciler",
        "config_key": "some-other-room",
    }

    config = make_config(rooms={
        "my-room": {"alias": "my-room", "name": "My Room"},
    })
    reconciler = Reconciler(mock_client, config)
    report = reconciler.diff()

    room_actions = [a for a in report.actions if a.resource == "room:my-room"]
    assert any(a.operation == "adopt" for a in room_actions)
    assert not any(a.operation == "skip" for a in room_actions)


def test_diff_missing_room_does_not_emit_per_member_invites(mock_client):
    """create_room handles invites; _diff_room must not duplicate them as actions."""
    mock_client.resolve_alias.return_value = None  # room missing

    config = make_config(rooms={
        "my-room": {
            "alias": "my-room", "name": "My Room",
            "members": ["admin", "router"],
        },
    })
    reconciler = Reconciler(mock_client, config)
    report = reconciler.diff()

    # Exactly one create action for the room, no separate invites.
    room_actions = [a for a in report.actions if a.resource == "room:my-room"]
    assert [a.operation for a in room_actions] == ["create"]


def test_apply_create_room_seeds_invites_atomically(mock_client):
    """Verify the missing-room create path passes invite= to create_room."""
    mock_client.resolve_alias.return_value = None  # all missing

    config = make_config(rooms={
        "my-room": {
            "alias": "my-room", "name": "My Room",
            "members": ["admin", "router"],
        },
    })
    reconciler = Reconciler(mock_client, config)
    reconciler.apply()

    # create_room should have been called with invite including router (admin
    # is filtered out as the creator).
    create_calls = [
        c for c in mock_client.create_room.call_args_list
        if c.kwargs.get("alias") == "my-room"
    ]
    assert create_calls, "create_room was not called for my-room"
    invites = create_calls[0].kwargs.get("invite") or []
    assert "@router:test.local" in invites
    assert "@admin:test.local" not in invites  # creator filtered


def test_apply_create_space_links_nested_space_to_parent(mock_client):
    """When a child space is created, it should be linked under its parent."""
    mock_client.resolve_alias.return_value = None  # all missing
    mock_client.create_space.side_effect = ["!parent:test.local", "!child:test.local"]

    config = make_config(spaces={
        "child-space": {
            "name": "Child Space",
            "visibility": "private",
            "rooms": {},
        },
    })
    reconciler = Reconciler(mock_client, config)
    reconciler.apply()

    # add_space_child should be called with (parent_id, child_id)
    add_child_calls = mock_client.add_space_child.call_args_list
    assert any(
        c.args == ("!parent:test.local", "!child:test.local") for c in add_child_calls
    ), f"Expected child space linked to parent, got: {add_child_calls}"


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
