"""Tests for config loading and validation."""

from pathlib import Path

import pytest

from src.admin.config_schema import (
    CommunityConfig,
    ConfigError,
    KnarrConfig,
    load_config,
    validate_config,
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
    config = KnarrConfig.from_dict(VALID_INDEX, community_loader=lambda _: VALID_COMMUNITY)
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
        KnarrConfig.from_dict(bad, community_loader=lambda _: VALID_COMMUNITY)


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
        community_loader=lambda _: bad_community,
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
        community_loader=lambda _: dup_community,
    )
    with pytest.raises(ConfigError, match="same-alias"):
        validate_config(config)


def test_validate_accepts_valid_config():
    config = KnarrConfig.from_dict(VALID_INDEX, community_loader=lambda _: VALID_COMMUNITY)
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


def test_validate_rejects_duplicate_room_keys_across_spaces():
    """Room keys must be globally unique — collisions break _find_room()."""
    dup_keys = {
        **VALID_COMMUNITY,
        "spaces": {
            "space-a": {
                "name": "A", "visibility": "private", "members": [], "rooms": {
                    "general": {"alias": "general-a", "name": "General A"},
                },
            },
            "space-b": {
                "name": "B", "visibility": "private", "members": [], "rooms": {
                    "general": {"alias": "general-b", "name": "General B"},
                },
            },
        },
    }
    config = KnarrConfig.from_dict(VALID_INDEX, community_loader=lambda _: dup_keys)
    with pytest.raises(ConfigError, match="Duplicate room key: general"):
        validate_config(config)


def test_validate_rejects_duplicate_space_keys():
    """Space keys must be globally unique too — same reason."""
    dup_space_keys = {
        **VALID_COMMUNITY,
        "spaces": {
            "outer-a": {
                "name": "A", "visibility": "private", "members": [], "rooms": {},
                "spaces": {"inner": {"name": "Inner A", "rooms": {}}},
            },
            "outer-b": {
                "name": "B", "visibility": "private", "members": [], "rooms": {},
                "spaces": {"inner": {"name": "Inner B", "rooms": {}}},
            },
        },
    }
    config = KnarrConfig.from_dict(VALID_INDEX, community_loader=lambda _: dup_space_keys)
    with pytest.raises(ConfigError, match="Duplicate space key: inner"):
        validate_config(config)


def test_load_config_wraps_yaml_parse_error(tmp_path: Path):
    """Malformed YAML should surface as ConfigError with file context."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("server_name: knarr.local\n  bad indent: [unterminated")

    with pytest.raises(ConfigError, match=r"Failed to parse YAML"):
        load_config(str(bad))


def test_load_config_rejects_non_dict_yaml(tmp_path: Path):
    """A YAML list at the top level is not a valid config — raise ConfigError."""
    bad = tmp_path / "list.yaml"
    bad.write_text("- one\n- two\n")

    with pytest.raises(ConfigError, match="must be a YAML mapping"):
        load_config(str(bad))


def test_load_config_rejects_empty_yaml(tmp_path: Path):
    """An empty file gives yaml.safe_load -> None; reject explicitly."""
    bad = tmp_path / "empty.yaml"
    bad.write_text("")

    with pytest.raises(ConfigError, match="empty"):
        load_config(str(bad))


def test_from_dict_rejects_rooms_as_list():
    """rooms must be a mapping — a YAML list is a common malformed shape."""
    bad = {
        **VALID_COMMUNITY,
        "spaces": {
            "s": {
                "name": "S", "visibility": "private", "members": [],
                "rooms": [],  # malformed — should be a dict
            },
        },
    }
    with pytest.raises(ConfigError, match="rooms"):
        CommunityConfig.from_dict(bad)


def test_from_dict_rejects_spaces_as_list():
    """child spaces field must be a mapping."""
    bad = {
        **VALID_COMMUNITY,
        "spaces": {
            "s": {
                "name": "S", "visibility": "private", "members": [],
                "rooms": {},
                "spaces": [],  # malformed
            },
        },
    }
    with pytest.raises(ConfigError, match="spaces"):
        CommunityConfig.from_dict(bad)


def test_from_dict_rejects_members_as_string():
    """members must be a list of strings, not a single string."""
    bad = {
        **VALID_COMMUNITY,
        "spaces": {
            "s": {
                "name": "S", "visibility": "private",
                "members": "admin",  # malformed — should be a list
                "rooms": {},
            },
        },
    }
    with pytest.raises(ConfigError, match="members"):
        CommunityConfig.from_dict(bad)


def test_from_dict_accepts_members_null():
    """members: null is permitted and treated as empty (YAML default behavior)."""
    cfg = {
        **VALID_COMMUNITY,
        "spaces": {
            "s": {
                "name": "S", "visibility": "private",
                "members": None,
                "rooms": {},
            },
        },
    }
    community = CommunityConfig.from_dict(cfg)
    assert community.spaces["s"].members == []


def test_from_dict_rejects_room_members_with_non_strings():
    """A list with a non-string entry is rejected (e.g. members: [123])."""
    bad = {
        **VALID_COMMUNITY,
        "spaces": {
            "s": {
                "name": "S", "visibility": "private", "members": [],
                "rooms": {
                    "r": {"alias": "r", "name": "R", "members": [123]},
                },
            },
        },
    }
    with pytest.raises(ConfigError, match="members"):
        CommunityConfig.from_dict(bad)


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
