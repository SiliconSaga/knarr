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
    # Resource must encode bridge_type so multiple bridge configs on the same
    # room can't collide and cause _apply_bridge to apply each type N times.
    assert bridge_actions[0].resource == "bridge:bridged:discord"
    assert bridge_actions[0].subject == "discord"


def test_diff_emits_one_bridge_action_per_bridge_type(mock_client):
    """Two bridge types on a single room produce two distinct apply units."""
    config = make_config(rooms={
        "bridged": {
            "alias": "bridged", "name": "Bridged",
            "bridge": {
                "discord": {"channel_id": "123"},
                "telegram": {"channel_id": "456"},
            },
        },
    })
    reconciler = Reconciler(mock_client, config)
    report = reconciler.diff()

    bridge_resources = sorted(
        a.resource for a in report.actions if a.operation == "bridge"
    )
    assert bridge_resources == ["bridge:bridged:discord", "bridge:bridged:telegram"]


def test_apply_bridge_only_applies_specific_type(mock_client):
    """Each bridge action should call bridge_channel exactly once for its type."""
    mock_client.resolve_alias.return_value = "!room:test.local"
    mock_client.get_room_state_event.return_value = _managed("bridged")
    bridge_manager = MagicMock()
    bridge_manager.client = MagicMock()

    config = make_config(rooms={
        "bridged": {
            "alias": "bridged", "name": "Bridged",
            "bridge": {
                "discord": {"channel_id": "discord-chan"},
                "telegram": {"channel_id": "telegram-chan"},
            },
        },
    })
    reconciler = Reconciler(mock_client, config, bridge_manager=bridge_manager)
    reconciler.apply()

    # Only discord is wired to bridge_channel today; telegram is silently
    # ignored. Either way, bridge_channel must be called at most once and
    # only with the discord channel id — not 4 times like the old N×N path.
    discord_calls = [
        c for c in bridge_manager.bridge_channel.call_args_list
        if "discord-chan" in c.args
    ]
    telegram_calls = [
        c for c in bridge_manager.bridge_channel.call_args_list
        if "telegram-chan" in c.args
    ]
    assert len(discord_calls) == 1
    assert telegram_calls == []


def test_apply_bridge_grants_bridge_bot_power_level(mock_client):
    """Apply should pre-grant PL 50 to the bridge bot so mautrix-discord
    can set state events without M_FORBIDDEN warnings."""
    mock_client.resolve_alias.return_value = "!room:test.local"

    state_events = {
        "org.knarr.managed": _managed("bridged"),
        "m.room.power_levels": {"users": {"@admin:test.local": 100}},
    }
    mock_client.get_room_state_event.side_effect = lambda _rid, etype: state_events.get(etype)

    bridge_manager = MagicMock()
    bridge_manager.client = MagicMock()

    config = make_config(rooms={
        "bridged": {
            "alias": "bridged", "name": "Bridged",
            "bridge": {"discord": {"channel_id": "c1"}},
        },
    })
    reconciler = Reconciler(mock_client, config, bridge_manager=bridge_manager)
    reconciler.apply()

    pl_writes = [
        c for c in mock_client.set_room_state_event.call_args_list
        if c.args[1] == "m.room.power_levels"
    ]
    assert pl_writes, "Expected a power_levels state-event write"
    written = pl_writes[-1].args[2]
    assert written["users"]["@discordbot:test.local"] == 50
    # Existing entries must be preserved.
    assert written["users"]["@admin:test.local"] == 100


def test_apply_bridge_skips_pl_write_when_bot_already_powered(mock_client):
    """Idempotent: if the bot already has PL >= 50, don't rewrite the event."""
    mock_client.resolve_alias.return_value = "!room:test.local"

    state_events = {
        "org.knarr.managed": _managed("bridged"),
        "m.room.power_levels": {
            "users": {"@admin:test.local": 100, "@discordbot:test.local": 100},
        },
    }
    mock_client.get_room_state_event.side_effect = lambda _rid, etype: state_events.get(etype)

    bridge_manager = MagicMock()
    bridge_manager.client = MagicMock()

    config = make_config(rooms={
        "bridged": {
            "alias": "bridged", "name": "Bridged",
            "bridge": {"discord": {"channel_id": "c1"}},
        },
    })
    reconciler = Reconciler(mock_client, config, bridge_manager=bridge_manager)
    reconciler.apply()

    pl_writes = [
        c for c in mock_client.set_room_state_event.call_args_list
        if c.args[1] == "m.room.power_levels"
    ]
    assert pl_writes == [], "Should not rewrite power_levels when bot already powered"


def test_apply_bridge_skips_pl_grant_for_non_discord_bridges(mock_client):
    """The discord-bot PL grant is gated to bridge_type == 'discord' — other
    bridge types (telegram, matrix-matrix, etc.) have their own bot users
    and must not accidentally grant power to @discordbot."""
    mock_client.resolve_alias.return_value = "!room:test.local"

    state_events = {
        "org.knarr.managed": _managed("bridged"),
        "m.room.power_levels": {"users": {"@admin:test.local": 100}},
    }
    mock_client.get_room_state_event.side_effect = lambda _rid, etype: state_events.get(etype)

    bridge_manager = MagicMock()
    bridge_manager.client = MagicMock()

    config = make_config(rooms={
        "bridged": {
            "alias": "bridged", "name": "Bridged",
            # telegram-only — no discord
            "bridge": {"telegram": {"channel_id": "c1"}},
        },
    })
    reconciler = Reconciler(mock_client, config, bridge_manager=bridge_manager)
    reconciler.apply()

    pl_writes = [
        c for c in mock_client.set_room_state_event.call_args_list
        if c.args[1] == "m.room.power_levels"
    ]
    assert pl_writes == [], (
        "telegram-only bridge should not trigger a power_levels write "
        f"for @discordbot; got: {pl_writes}"
    )


def test_diff_detects_watcher_config(mock_client):
    config = make_config(rooms={
        "watched": {
            "alias": "watched", "name": "Watched",
            "watchers": {"reddit": {"subreddit": "Test", "interval_seconds": 300}},
        },
    })
    reconciler = Reconciler(mock_client, config)
    report = reconciler.diff()

    # Watcher config is informational — emitted as "noop" so the audit shows it
    # without driving the drift exit code or being counted as "applied".
    watcher_actions = [a for a in report.actions if a.operation == "noop"]
    assert len(watcher_actions) == 1
    # Resource includes the room key so two rooms watching the same source
    # don't produce duplicate-resource actions.
    assert watcher_actions[0].resource == "watcher:reddit:watched"


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


def test_report_no_drift_for_noop():
    """noop actions (e.g., watcher config) must not drive drift exit code."""
    from src.admin.reconciler import ReconcileReport
    report = ReconcileReport(
        actions=[Action("watcher:reddit:r", "noop", "informational only")],
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
