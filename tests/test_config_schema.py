"""Tests for config loading and validation."""

import pytest

from src.admin.config_schema import (
    load_config,
    validate_config,
    KnarrConfig,
    CommunityConfig,
    ConfigError,
)


VALID_INDEX = {
    "server_name": "knarr.local",
    "secrets": {"discord_token": "DISCORD_TOKEN"},
    "users": {
        "admin": "@admin:knarr.local",
        "router": "@router:knarr.local",
        "bridge_bot": "@discordbot:knarr.local",
    },
    "communities": ["config/test.yaml"],
}

VALID_COMMUNITY = {
    "community": "test",
    "display_name": "Test",
    "spaces": {
        "test-space": {
            "name": "Test Space",
            "visibility": "private",
            "members": ["admin"],
            "rooms": {
                "test-room": {
                    "alias": "test-room",
                    "name": "#test-room",
                    "topic": "A test room",
                    "members": ["router"],
                },
            },
        },
    },
}


def test_parse_index_config():
    config = KnarrConfig.from_dict(VALID_INDEX, community_loader=lambda p: VALID_COMMUNITY)
    assert config.server_name == "knarr.local"
    assert len(config.communities) == 1
    assert config.users["admin"] == "@admin:knarr.local"


def test_parse_community_config():
    community = CommunityConfig.from_dict(VALID_COMMUNITY)
    assert community.community == "test"
    space = community.spaces["test-space"]
    assert space.name == "Test Space"
    assert "test-room" in space.rooms


def test_validate_rejects_missing_server_name():
    bad = {**VALID_INDEX}
    del bad["server_name"]
    with pytest.raises(ConfigError, match="server_name"):
        KnarrConfig.from_dict(bad, community_loader=lambda p: VALID_COMMUNITY)


def test_validate_rejects_unknown_user_reference():
    bad_community = {
        **VALID_COMMUNITY,
        "spaces": {
            "s": {
                "name": "S",
                "visibility": "private",
                "members": ["nonexistent_user"],
                "rooms": {},
            },
        },
    }
    config = KnarrConfig.from_dict(
        VALID_INDEX,
        community_loader=lambda p: bad_community,
    )
    with pytest.raises(ConfigError, match="nonexistent_user"):
        validate_config(config)


def test_validate_rejects_duplicate_aliases():
    dup_community = {
        **VALID_COMMUNITY,
        "spaces": {
            "s1": {
                "name": "S1",
                "visibility": "private",
                "members": [],
                "rooms": {
                    "r1": {"alias": "same-alias", "name": "R1", "topic": ""},
                    "r2": {"alias": "same-alias", "name": "R2", "topic": ""},
                },
            },
        },
    }
    config = KnarrConfig.from_dict(
        VALID_INDEX,
        community_loader=lambda p: dup_community,
    )
    with pytest.raises(ConfigError, match="same-alias"):
        validate_config(config)


def test_validate_accepts_valid_config():
    config = KnarrConfig.from_dict(VALID_INDEX, community_loader=lambda p: VALID_COMMUNITY)
    validate_config(config)


def test_room_with_bridge_config():
    community_with_bridge = {
        **VALID_COMMUNITY,
        "spaces": {
            "s": {
                "name": "S",
                "visibility": "private",
                "members": [],
                "rooms": {
                    "bridged": {
                        "alias": "bridged",
                        "name": "Bridged",
                        "topic": "",
                        "members": [],
                        "bridge": {
                            "discord": {
                                "channel_id": "123456",
                                "relay": True,
                            },
                        },
                    },
                },
            },
        },
    }
    community = CommunityConfig.from_dict(community_with_bridge)
    room = community.spaces["s"].rooms["bridged"]
    assert room.bridge is not None
    assert room.bridge["discord"]["channel_id"] == "123456"


def test_room_with_watcher_config():
    community_with_watcher = {
        **VALID_COMMUNITY,
        "spaces": {
            "s": {
                "name": "S",
                "visibility": "private",
                "members": [],
                "rooms": {
                    "watched": {
                        "alias": "watched",
                        "name": "Watched",
                        "topic": "",
                        "members": ["router"],
                        "watchers": {
                            "reddit": {
                                "subreddit": "Terasology",
                                "interval_seconds": 21600,
                            },
                        },
                    },
                },
            },
        },
    }
    community = CommunityConfig.from_dict(community_with_watcher)
    room = community.spaces["s"].rooms["watched"]
    assert room.watchers["reddit"]["subreddit"] == "Terasology"


def test_nested_spaces():
    nested = {
        **VALID_COMMUNITY,
        "spaces": {
            "parent": {
                "name": "Parent",
                "visibility": "private",
                "members": [],
                "rooms": {},
                "spaces": {
                    "child": {
                        "name": "Child",
                        "visibility": "private",
                        "members": [],
                        "rooms": {
                            "inner-room": {
                                "alias": "inner-room",
                                "name": "Inner",
                                "topic": "",
                            },
                        },
                    },
                },
            },
        },
    }
    community = CommunityConfig.from_dict(nested)
    parent = community.spaces["parent"]
    assert "child" in parent.children
    assert "inner-room" in parent.children["child"].rooms
