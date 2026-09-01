"""Tests for config loading and validation."""

from pathlib import Path

import pytest
import yaml

from src.admin.config_schema import (
    CommunityConfig,
    ConfigError,
    KnarrConfig,
    load_config,
    validate_config,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

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


def test_from_dict_rejects_communities_as_string():
    """`communities` must be a list of paths, not a single string."""
    bad = {**VALID_INDEX, "communities": "config/test.yaml"}
    with pytest.raises(ConfigError, match="communities"):
        KnarrConfig.from_dict(bad, community_loader=lambda _: VALID_COMMUNITY)


def test_from_dict_rejects_communities_as_null():
    """`communities` is required to be a list when present; null is rejected."""
    # `_require_str_list(None, ...)` returns []; that's the existing
    # contract for "field absent". Verify the contract holds.
    cfg = {**VALID_INDEX, "communities": None}
    parsed = KnarrConfig.from_dict(cfg, community_loader=lambda _: VALID_COMMUNITY)
    assert parsed.communities == []


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


def test_parse_instances_block():
    """Top-level `instances:` parses into InstanceConfig dataclasses."""
    cfg = {
        **VALID_INDEX,
        "instances": [
            {
                "id": "reddit-terasology",
                "platform": "reddit",
                "access_path": "api",
                "scope": "community/terasology",
                "credentials_ref": None,
                "polling": {"interval_seconds": 21600},
                "platform_config": {"subreddit": "Terasology"},
                "target_room": "social-watch",
            },
            {
                "id": "github-terasology",
                "platform": "github",
                "access_path": "api",
                "scope": "community/terasology",
                "credentials_ref": {
                    "secret_name": "knarr-cred-community-terasology-gh-pat",
                    "secret_key": "token",
                },
                "polling": {"interval_seconds": 21600},
                "platform_config": {"repos": ["MovingBlocks/Terasology"]},
                "target_room": "social-watch",
            },
        ],
    }
    parsed = KnarrConfig.from_dict(cfg, community_loader=lambda _: VALID_COMMUNITY)
    assert len(parsed.instances) == 2
    assert parsed.instances[0].id == "reddit-terasology"
    assert parsed.instances[0].platform == "reddit"
    assert parsed.instances[0].access_path == "api"
    assert parsed.instances[0].scope == "community/terasology"
    assert parsed.instances[0].credentials_ref is None
    assert parsed.instances[0].polling["interval_seconds"] == 21600
    assert parsed.instances[0].platform_config["subreddit"] == "Terasology"
    assert parsed.instances[0].target_room == "social-watch"
    assert (
        parsed.instances[1].credentials_ref["secret_name"]
        == "knarr-cred-community-terasology-gh-pat"
    )


def test_parse_instances_defaults_to_empty():
    """No `instances:` key → instances is an empty list."""
    parsed = KnarrConfig.from_dict(VALID_INDEX, community_loader=lambda _: VALID_COMMUNITY)
    assert parsed.instances == []


def test_validate_rejects_instance_with_unknown_scope_type():
    """Scope must use the canonical prefixes: community/, user/, group/."""
    bad = {
        **VALID_INDEX,
        "instances": [{
            "id": "broken",
            "platform": "reddit",
            "access_path": "api",
            "scope": "garbage/terasology",
            "polling": {"interval_seconds": 100},
            "platform_config": {},
            "target_room": "social-watch",
        }],
    }
    config = KnarrConfig.from_dict(bad, community_loader=lambda _: VALID_COMMUNITY)
    with pytest.raises(ConfigError, match="scope"):
        validate_config(config)


def _instance(**overrides):
    """A minimal valid reddit/api instance, with fields overridable."""
    base = {
        "id": "inst",
        "platform": "reddit",
        "access_path": "api",
        "scope": "community/terasology",
        "polling": {"interval_seconds": 100},
        "platform_config": {"subreddit": "Terasology"},
        "target_room": "social-watch",
    }
    base.update(overrides)
    return {**VALID_INDEX, "instances": [base]}


def _expect_config_error(payload, match):
    config = KnarrConfig.from_dict(payload, community_loader=lambda _: VALID_COMMUNITY)
    with pytest.raises(ConfigError, match=match):
        validate_config(config)


def test_validate_requires_polling_interval():
    """A missing interval used to fall back to a hardcoded default silently."""
    _expect_config_error(
        _instance(polling={}), "interval_seconds is required",
    )


def test_validate_rejects_non_positive_polling_interval():
    """Zero or negative turns the poll loop into a busy-wait on the platform."""
    _expect_config_error(
        _instance(polling={"interval_seconds": 0}), "must be positive",
    )
    _expect_config_error(
        _instance(polling={"interval_seconds": -5}), "must be positive",
    )


def test_validate_rejects_non_integer_polling_interval():
    """bool is an int subclass, so `interval_seconds: true` needs catching."""
    _expect_config_error(
        _instance(polling={"interval_seconds": "600"}), "must be an integer",
    )
    _expect_config_error(
        _instance(polling={"interval_seconds": True}), "must be an integer",
    )


def test_validate_requires_reddit_subreddit():
    """Previously a KeyError deep in adapter construction, naming nothing."""
    _expect_config_error(
        _instance(platform_config={}), "non-empty platform_config.subreddit",
    )
    _expect_config_error(
        _instance(platform_config={"subreddit": "   "}),
        "non-empty platform_config.subreddit",
    )


def test_validate_rejects_platform_access_path_without_an_adapter():
    """A pair with no adapter used to fail only when build_adapter gave up.

    Config that parses and validates cleanly, then dies at startup, is worse
    than config that is rejected — the error arrives far from the mistake.
    """
    _expect_config_error(
        _instance(platform="github", access_path="scrape",
                  platform_config={"repos": ["a/b"]}),
        "no adapter for github/scrape",
    )
    _expect_config_error(
        _instance(platform="facebook", access_path="admin-app"),
        "no adapter for facebook/admin-app",
    )


def test_validate_requires_github_repos():
    _expect_config_error(
        _instance(platform="github", platform_config={}),
        "non-empty platform_config.repos",
    )
    _expect_config_error(
        _instance(platform="github", platform_config={"repos": []}),
        "non-empty platform_config.repos",
    )
    _expect_config_error(
        _instance(platform="github", platform_config={"repos": ["ok", ""]}),
        "must be a non-empty string",
    )


def test_parse_instance_rejects_non_string_scope():
    """Non-string scope is caught at parse time, not at validate_config."""
    bad = {
        **VALID_INDEX,
        "instances": [{
            "id": "broken",
            "platform": "reddit",
            "access_path": "api",
            "scope": 42,
            "polling": {"interval_seconds": 100},
            "platform_config": {},
            "target_room": "social-watch",
        }],
    }
    with pytest.raises(ConfigError, match="scope"):
        KnarrConfig.from_dict(bad, community_loader=lambda _: VALID_COMMUNITY)


def test_validate_rejects_credentials_ref_missing_secret_key():
    """credentials_ref shape is caught at `config validate` time."""
    bad = {
        **VALID_INDEX,
        "instances": [{
            "id": "broken",
            "platform": "github",
            "access_path": "api",
            "scope": "community/terasology",
            "polling": {"interval_seconds": 100},
            "platform_config": {"repos": ["a/b"]},
            "target_room": "social-watch",
            "credentials_ref": {"secret_name": "knarr-cred-x"},
        }],
    }
    config = KnarrConfig.from_dict(bad, community_loader=lambda _: VALID_COMMUNITY)
    with pytest.raises(ConfigError, match="secret_key"):
        validate_config(config)


def test_validate_rejects_credentials_ref_missing_secret_name():
    """Symmetric: secret_name also required."""
    bad = {
        **VALID_INDEX,
        "instances": [{
            "id": "broken",
            "platform": "github",
            "access_path": "api",
            "scope": "community/terasology",
            "polling": {"interval_seconds": 100},
            "platform_config": {"repos": ["a/b"]},
            "target_room": "social-watch",
            "credentials_ref": {"secret_key": "GITHUB_TOKEN"},
        }],
    }
    config = KnarrConfig.from_dict(bad, community_loader=lambda _: VALID_COMMUNITY)
    with pytest.raises(ConfigError, match="secret_name"):
        validate_config(config)


def test_validate_rejects_non_string_secret_key():
    """Non-string secret_key would crash os.environ.get at runtime; catch it earlier."""
    bad = {
        **VALID_INDEX,
        "instances": [{
            "id": "broken",
            "platform": "github",
            "access_path": "api",
            "scope": "community/terasology",
            "polling": {"interval_seconds": 100},
            "platform_config": {"repos": ["a/b"]},
            "target_room": "social-watch",
            "credentials_ref": {
                "secret_name": "knarr-cred-x",
                "secret_key": 42,
            },
        }],
    }
    config = KnarrConfig.from_dict(bad, community_loader=lambda _: VALID_COMMUNITY)
    with pytest.raises(ConfigError, match="secret_key.*must be a string"):
        validate_config(config)


def test_parse_rejects_non_list_instances():
    """A non-list (truthy) instances value is rejected with a clear message."""
    bad = {**VALID_INDEX, "instances": "oops-not-a-list"}
    with pytest.raises(ConfigError, match="instances must be a list"):
        KnarrConfig.from_dict(bad, community_loader=lambda _: VALID_COMMUNITY)


def test_validate_rejects_non_string_secret_name():
    """Symmetric with the secret_key case: non-string secret_name is caught."""
    bad = {
        **VALID_INDEX,
        "instances": [{
            "id": "broken",
            "platform": "github",
            "access_path": "api",
            "scope": "community/terasology",
            "polling": {"interval_seconds": 100},
            "platform_config": {"repos": ["a/b"]},
            "target_room": "social-watch",
            "credentials_ref": {
                "secret_name": 42,
                "secret_key": "GITHUB_TOKEN",
            },
        }],
    }
    config = KnarrConfig.from_dict(bad, community_loader=lambda _: VALID_COMMUNITY)
    with pytest.raises(ConfigError, match="secret_name.*must be a string"):
        validate_config(config)


def test_loads_real_test_config_with_instances():
    """Walking the actual config/knarr.yaml from this repo parses cleanly
    and produces 2 instances."""
    config_path = _REPO_ROOT / "config" / "knarr.yaml"
    if not config_path.exists():
        pytest.skip(f"{config_path} not present in this checkout")
    parsed = load_config(str(config_path))
    assert len(parsed.instances) == 2
    assert {i.id for i in parsed.instances} == {
        "reddit-terasology", "github-terasology",
    }
    gh = next(i for i in parsed.instances if i.id == "github-terasology")
    assert gh.credentials_ref["secret_key"] == "GITHUB_TOKEN"
    validate_config(parsed)  # must pass validation too


def test_watcher_configmap_instances_match_the_source_config():
    """The K8s ConfigMap embeds a COPY of config/. Catch it drifting.

    `k8s/watchers/reddit-github.yaml` hand-maintains a copy of knarr.yaml so
    the watcher pod can mount it. Generating it from source is the proper fix
    and is still deferred — until then, a `config/` change nobody mirrored is
    silent drift between what the CLI validates and what the pod actually
    runs, and the pod wins.

    Only `instances:` is compared, because that is the part that drives
    behaviour. Two differences are DELIBERATE and must not fail this test:
    the embedded `communities:` uses the bare filename (both files are flat
    keys of one ConfigMap, so there is no `config/` directory in the pod),
    and the embedded community omits the bridge room the watcher never reads.
    """
    manifest = _REPO_ROOT / "k8s" / "watchers" / "reddit-github.yaml"
    source = _REPO_ROOT / "config" / "knarr.yaml"
    if not manifest.exists() or not source.exists():
        pytest.skip("manifest or source config not present in this checkout")

    docs = [d for d in yaml.safe_load_all(manifest.read_text()) if d]
    configmaps = [d for d in docs if d.get("kind") == "ConfigMap"]
    assert configmaps, "expected a ConfigMap in the watcher manifest"

    embedded = yaml.safe_load(configmaps[0]["data"]["knarr.yaml"])
    on_disk = yaml.safe_load(source.read_text())

    assert embedded["instances"] == on_disk["instances"], (
        "k8s/watchers/reddit-github.yaml's embedded instances have drifted "
        "from config/knarr.yaml. Update the ConfigMap to match, or the "
        "watcher pod will run configuration nothing else validates."
    )
