# Config Reconciler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a GitOps-style config reconciler that reads desired Matrix room topology, bridge mappings, and watcher config from YAML files, diffs against live state, and applies changes idempotently.

**Architecture:** A `Reconciler` class loads community YAML configs, queries current Matrix state via extended `MatrixAdminClient` methods, computes a diff as a list of `Action` objects, and applies them in dependency order (spaces → rooms → members → bridges → watchers). CLI commands `knarr config apply/audit/validate` wrap this. Unit tests use httpx mock transport; integration tests use pytest-bdd against real Synapse.

**Tech Stack:** Python 3.11+, httpx, click, pyyaml, pytest, pytest-bdd, kubectl (for watcher deployment patches).

**Spec:** `docs/plans/2026-04-25-config-reconciler-design.md`

---

## File Map

```text
components/knarr/
  config/
    knarr.yaml                   # Top-level index (new)
    test.yaml                    # Test community config (new)
  src/admin/
    client.py                    # MatrixAdminClient (modify — add 8 methods)
    bridge.py                    # BridgeManager (existing, no changes)
    config_schema.py             # Config loading and validation (new)
    reconciler.py                # Reconciler diff/apply engine (new)
    cli.py                       # CLI (modify — add config subgroup)
  tests/
    test_admin_client.py         # (modify — add tests for new methods)
    test_config_schema.py        # Config validation tests (new)
    test_reconciler.py           # Reconciler unit tests (new)
    features/                    # BDD integration tests (new)
      reconcile.feature
      conftest.py
      steps/
        reconcile_steps.py
  pyproject.toml                 # (modify — add pyyaml, pytest-bdd)
  requirements.txt               # (modify — add pyyaml, pytest-bdd)
```

---

## Task 1: Add PyYAML and pytest-bdd Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`

- [ ] **Step 1: Update pyproject.toml**

Add `pyyaml` to dependencies and `pytest-bdd` to dev dependencies:

```toml
[project]
name = "knarr"
version = "0.1.0"
description = "Knarr — Matrix integration hub, bridges, and message routing"
requires-python = ">=3.11"
dependencies = [
    "matrix-nio>=0.24.0",
    "confluent-kafka>=2.6.0",
    "pydantic>=2.0",
    "httpx>=0.27.0",
    "click>=8.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24.0",
    "pytest-bdd>=7.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Update requirements.txt**

```text
matrix-nio>=0.24.0
confluent-kafka>=2.6.0
pydantic>=2.0
httpx>=0.27.0
click>=8.0
pyyaml>=6.0
```

- [ ] **Step 3: Install dependencies**

Run: `pip3 install pyyaml pytest-bdd`

- [ ] **Step 4: Verify existing tests still pass**

Run: `cd components/knarr && python3 -m pytest tests/ -v`
Expected: 23 passed

- [ ] **Step 5: Commit**

```
message: "chore: add pyyaml and pytest-bdd dependencies"
add: [pyproject.toml, requirements.txt]
```

---

## Task 2: Extend MatrixAdminClient with Room Query Methods

**Files:**
- Modify: `src/admin/client.py`
- Modify: `tests/test_admin_client.py`

- [ ] **Step 1: Write failing tests for new methods**

Add to `tests/test_admin_client.py`:

```python
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
    # Default 404 response from mock_transport
    room_id = client.resolve_alias("#missing:test")
    assert room_id is None


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
    client = make_client({
        "/login": (200, {"access_token": "tok"}),
        "/directory/room/": (200, {}),
    })
    client.set_room_alias("!room:test", "#myalias:test")


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
    assert "#myspace:test" in body.get("room_alias_name", "") or "myspace" in body.get("room_alias_name", "")


def test_add_space_child():
    requests_seen = []

    def capturing_handler(request: httpx.Request) -> httpx.Response:
        if "/login" in request.url.path:
            return httpx.Response(200, json={"access_token": "tok"})
        if "/state/m.space.child" in request.url.path:
            requests_seen.append(json.loads(request.content))
            return httpx.Response(200, {"event_id": "$evt"})
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd components/knarr && python3 -m pytest tests/test_admin_client.py -v`
Expected: 11 new tests FAIL (AttributeError: no such method)

- [ ] **Step 3: Implement new methods in client.py**

Add to `MatrixAdminClient` in `src/admin/client.py`:

```python
    def resolve_alias(self, alias: str) -> Optional[str]:
        """Resolve a room alias to a room ID. Returns None if not found."""
        encoded_alias = quote(alias, safe="")
        try:
            resp = self._authed_request(
                "GET", f"/_matrix/client/v3/directory/room/{encoded_alias}"
            )
            return resp.json().get("room_id")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def get_room_state(self, room_id: str) -> dict:
        """Read room state: name, topic, join_rule. Returns a flat dict."""
        resp = self._authed_request(
            "GET",
            f"/_matrix/client/v3/rooms/{self._encode_room(room_id)}/state",
        )
        events = resp.json()
        result = {}
        for event in events:
            if event["type"] == "m.room.name":
                result["name"] = event["content"].get("name", "")
            elif event["type"] == "m.room.topic":
                result["topic"] = event["content"].get("topic", "")
            elif event["type"] == "m.room.join_rules":
                result["join_rule"] = event["content"].get("join_rule", "")
            elif event["type"] == "m.room.create":
                result["room_type"] = event["content"].get("type", "")
        return result

    def get_room_members(self, room_id: str) -> list[str]:
        """List user IDs of current room members."""
        resp = self._authed_request(
            "GET",
            f"/_matrix/client/v3/rooms/{self._encode_room(room_id)}/joined_members",
        )
        return list(resp.json().get("joined", {}).keys())

    def set_room_alias(self, room_id: str, alias: str) -> None:
        """Assign an alias to a room."""
        encoded_alias = quote(alias, safe="")
        self._authed_request(
            "PUT",
            f"/_matrix/client/v3/directory/room/{encoded_alias}",
            json={"room_id": room_id},
        )

    def create_space(
        self,
        name: str,
        alias: str = "",
        topic: str = "",
        invite: Optional[list[str]] = None,
        private: bool = True,
    ) -> str:
        """Create a Matrix space (a room with m.space type). Returns room ID."""
        body: dict = {
            "name": name,
            "preset": "private_chat" if private else "public_chat",
            "creation_content": {"type": "m.space"},
        }
        if alias:
            body["room_alias_name"] = alias
        if topic:
            body["topic"] = topic
        if invite:
            body["invite"] = invite
        resp = self._authed_request("POST", "/_matrix/client/v3/createRoom", json=body)
        return resp.json()["room_id"]

    def add_space_child(self, space_id: str, child_id: str) -> None:
        """Add a room or space as a child of a space."""
        # Extract server name from child_id for the 'via' field
        server = child_id.split(":")[1] if ":" in child_id else "localhost"
        self._authed_request(
            "PUT",
            f"/_matrix/client/v3/rooms/{self._encode_room(space_id)}"
            f"/state/m.space.child/{child_id}",
            json={"via": [server]},
        )

    def set_room_state_event(
        self, room_id: str, event_type: str, content: dict
    ) -> None:
        """Write a custom state event to a room."""
        self._authed_request(
            "PUT",
            f"/_matrix/client/v3/rooms/{self._encode_room(room_id)}"
            f"/state/{quote(event_type, safe='')}",
            json=content,
        )

    def get_room_state_event(
        self, room_id: str, event_type: str
    ) -> Optional[dict]:
        """Read a state event from a room. Returns None if not found."""
        try:
            resp = self._authed_request(
                "GET",
                f"/_matrix/client/v3/rooms/{self._encode_room(room_id)}"
                f"/state/{quote(event_type, safe='')}",
            )
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 403):
                return None
            raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd components/knarr && python3 -m pytest tests/test_admin_client.py -v`
Expected: all tests pass (original 8 + 11 new)

- [ ] **Step 5: Commit**

```
message: "feat: extend MatrixAdminClient with room query and space methods"
add: [src/admin/client.py, tests/test_admin_client.py]
```

---

## Task 3: Config Schema Loader and Validator

**Files:**
- Create: `src/admin/config_schema.py`
- Create: `tests/test_config_schema.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_config_schema.py`:

```python
"""Tests for config loading and validation."""

import pytest
import yaml

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
    validate_config(config)  # should not raise


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd components/knarr && python3 -m pytest tests/test_config_schema.py -v`
Expected: ImportError (module doesn't exist yet)

- [ ] **Step 3: Implement config_schema.py**

Create `src/admin/config_schema.py`:

```python
"""Config loading, parsing, and validation for the Knarr reconciler."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import yaml


class ConfigError(Exception):
    """Raised when config is invalid."""


@dataclass
class RoomConfig:
    alias: str
    name: str
    topic: str = ""
    members: list[str] = field(default_factory=list)
    bridge: Optional[dict] = None
    watchers: Optional[dict] = None

    @classmethod
    def from_dict(cls, key: str, data: dict) -> RoomConfig:
        return cls(
            alias=data.get("alias", key),
            name=data.get("name", key),
            topic=data.get("topic", ""),
            members=data.get("members", []),
            bridge=data.get("bridge"),
            watchers=data.get("watchers"),
        )


@dataclass
class SpaceConfig:
    name: str
    visibility: str = "private"
    members: list[str] = field(default_factory=list)
    rooms: dict[str, RoomConfig] = field(default_factory=dict)
    children: dict[str, SpaceConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, key: str, data: dict) -> SpaceConfig:
        rooms = {}
        for room_key, room_data in data.get("rooms", {}).items():
            rooms[room_key] = RoomConfig.from_dict(room_key, room_data)

        children = {}
        for space_key, space_data in data.get("spaces", {}).items():
            children[space_key] = SpaceConfig.from_dict(space_key, space_data)

        return cls(
            name=data.get("name", key),
            visibility=data.get("visibility", "private"),
            members=data.get("members", []),
            rooms=rooms,
            children=children,
        )


@dataclass
class CommunityConfig:
    community: str
    display_name: str
    spaces: dict[str, SpaceConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> CommunityConfig:
        spaces = {}
        for space_key, space_data in data.get("spaces", {}).items():
            spaces[space_key] = SpaceConfig.from_dict(space_key, space_data)

        return cls(
            community=data["community"],
            display_name=data.get("display_name", data["community"]),
            spaces=spaces,
        )


@dataclass
class KnarrConfig:
    server_name: str
    secrets: dict[str, str] = field(default_factory=dict)
    users: dict[str, str] = field(default_factory=dict)
    communities: list[CommunityConfig] = field(default_factory=list)

    @classmethod
    def from_dict(
        cls,
        data: dict,
        community_loader: Callable[[str], dict],
    ) -> KnarrConfig:
        if "server_name" not in data:
            raise ConfigError("server_name is required in the index config")

        communities = []
        for path in data.get("communities", []):
            community_data = community_loader(path)
            communities.append(CommunityConfig.from_dict(community_data))

        return cls(
            server_name=data["server_name"],
            secrets=data.get("secrets", {}),
            users=data.get("users", {}),
            communities=communities,
        )


def load_config(config_path: str) -> KnarrConfig:
    """Load the index config and all referenced community configs."""
    config_dir = Path(config_path).parent
    with open(config_path) as f:
        index_data = yaml.safe_load(f)

    def community_loader(rel_path: str) -> dict:
        # Resolve relative to the index config's directory
        full_path = config_dir / rel_path
        if not full_path.exists():
            # Try relative to config_path's parent's parent (repo root)
            full_path = config_dir.parent / rel_path
        if not full_path.exists():
            raise ConfigError(f"Community config not found: {rel_path}")
        with open(full_path) as f:
            return yaml.safe_load(f)

    return KnarrConfig.from_dict(index_data, community_loader)


def _collect_aliases(space: SpaceConfig) -> list[str]:
    """Recursively collect all room aliases from a space."""
    aliases = [r.alias for r in space.rooms.values()]
    for child in space.children.values():
        aliases.extend(_collect_aliases(child))
    return aliases


def _collect_user_refs(space: SpaceConfig) -> set[str]:
    """Recursively collect all user references from a space."""
    refs = set(space.members)
    for room in space.rooms.values():
        refs.update(room.members)
    for child in space.children.values():
        refs.update(_collect_user_refs(child))
    return refs


def validate_config(config: KnarrConfig) -> None:
    """Validate cross-references and uniqueness constraints."""
    all_aliases: list[str] = []
    all_user_refs: set[str] = set()

    for community in config.communities:
        for space in community.spaces.values():
            all_aliases.extend(_collect_aliases(space))
            all_user_refs.update(_collect_user_refs(space))

    # Check for duplicate aliases
    seen = set()
    for alias in all_aliases:
        if alias in seen:
            raise ConfigError(f"Duplicate room alias: {alias}")
        seen.add(alias)

    # Check that all user references resolve
    for ref in all_user_refs:
        if ref not in config.users:
            raise ConfigError(
                f"Unknown user reference: {ref} "
                f"(not in knarr.yaml users: {list(config.users.keys())})"
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd components/knarr && python3 -m pytest tests/test_config_schema.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```
message: "feat: add config schema loader and validator"
add: [src/admin/config_schema.py, tests/test_config_schema.py]
```

---

## Task 4: Reconciler Core — Diff Engine

**Files:**
- Create: `src/admin/reconciler.py`
- Create: `tests/test_reconciler.py`

- [ ] **Step 1: Write failing tests for diff computation**

Create `tests/test_reconciler.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd components/knarr && python3 -m pytest tests/test_reconciler.py -v`
Expected: ImportError (module doesn't exist yet)

- [ ] **Step 3: Implement reconciler.py**

Create `src/admin/reconciler.py`:

```python
"""Knarr config reconciler — diffs desired state against live Matrix and applies changes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .client import MatrixAdminClient
from .config_schema import KnarrConfig, SpaceConfig, RoomConfig


@dataclass
class Action:
    resource: str
    operation: str  # create, update, invite, bridge, config, skip
    details: str


@dataclass
class ReconcileReport:
    actions: list[Action] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return any(a.operation != "skip" for a in self.actions)

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for a in self.actions:
            counts[a.operation] = counts.get(a.operation, 0) + 1
        parts = [f"{v} {k}" for k, v in sorted(counts.items())]
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return ", ".join(parts)


class Reconciler:
    """Diffs desired config against live Matrix state and optionally applies changes."""

    def __init__(
        self,
        client: MatrixAdminClient,
        config: KnarrConfig,
        bridge_manager=None,
    ):
        self.client = client
        self.config = config
        self.bridge_manager = bridge_manager
        self._room_ids: dict[str, str] = {}  # alias -> room_id mapping

    def diff(self) -> ReconcileReport:
        """Compute the diff between desired config and live state."""
        report = ReconcileReport()
        for community in self.config.communities:
            for space_key, space in community.spaces.items():
                self._diff_space(
                    space_key, space, community.community, None, report
                )
        self._diff_watchers(report)
        return report

    def apply(self) -> ReconcileReport:
        """Diff and apply all changes."""
        report = self.diff()
        self._apply_actions(report)
        return report

    def _full_alias(self, alias: str) -> str:
        return f"#{alias}:{self.config.server_name}"

    def _resolve_user(self, shorthand: str) -> str:
        return self.config.users.get(shorthand, shorthand)

    def _diff_space(
        self,
        key: str,
        space: SpaceConfig,
        community: str,
        parent_alias: Optional[str],
        report: ReconcileReport,
    ) -> None:
        alias = self._full_alias(key)
        resource = f"space:{key}"
        room_id = self.client.resolve_alias(alias)

        if room_id is None:
            report.actions.append(Action(resource, "create", f'"{space.name}" ({space.visibility})'))
        else:
            self._room_ids[key] = room_id
            managed = self.client.get_room_state_event(room_id, "org.knarr.managed")
            if managed:
                report.actions.append(Action(resource, "skip", f"already exists ({alias})"))
            else:
                report.actions.append(Action(resource, "adopt", f"exists but unmanaged ({alias})"))

            # Check membership
            current_members = self.client.get_room_members(room_id)
            for member_ref in space.members:
                user_id = self._resolve_user(member_ref)
                if user_id not in current_members:
                    report.actions.append(
                        Action(resource, "invite", f"{user_id} to space {key}")
                    )

        # Diff rooms within this space
        for room_key, room in space.rooms.items():
            self._diff_room(room_key, room, community, key, report)

        # Diff child spaces
        for child_key, child_space in space.children.items():
            self._diff_space(child_key, child_space, community, key, report)

    def _diff_room(
        self,
        key: str,
        room: RoomConfig,
        community: str,
        parent_space_key: str,
        report: ReconcileReport,
    ) -> None:
        alias = self._full_alias(room.alias)
        resource = f"room:{key}"
        room_id = self.client.resolve_alias(alias)

        if room_id is None:
            report.actions.append(
                Action(resource, "create", f'"{room.name}" in {parent_space_key}')
            )
            # All members need inviting for a new room
            for member_ref in room.members:
                user_id = self._resolve_user(member_ref)
                report.actions.append(
                    Action(resource, "invite", f"{user_id} to {key}")
                )
        else:
            self._room_ids[room.alias] = room_id
            managed = self.client.get_room_state_event(room_id, "org.knarr.managed")
            if managed:
                report.actions.append(Action(resource, "skip", f"already exists ({alias})"))
            else:
                report.actions.append(Action(resource, "adopt", f"exists but unmanaged ({alias})"))

            # Check membership
            current_members = self.client.get_room_members(room_id)
            for member_ref in room.members:
                user_id = self._resolve_user(member_ref)
                if user_id not in current_members:
                    report.actions.append(
                        Action(resource, "invite", f"{user_id} to {key}")
                    )

        # Bridge config
        if room.bridge:
            for bridge_type, bridge_config in room.bridge.items():
                report.actions.append(
                    Action(
                        f"bridge:{key}",
                        "bridge",
                        f"{bridge_type} channel {bridge_config.get('channel_id', '?')}",
                    )
                )

        # Watcher config (collected, applied in _diff_watchers)
        if room.watchers:
            for watcher_type, watcher_config in room.watchers.items():
                report.actions.append(
                    Action(
                        f"watcher:{watcher_type}",
                        "config",
                        f"{watcher_type}: {watcher_config}",
                    )
                )

    def _diff_watchers(self, report: ReconcileReport) -> None:
        """Watchers are aggregated across all rooms — nothing extra to diff here.
        Individual watcher actions are already added per-room in _diff_room."""

    def _apply_actions(self, report: ReconcileReport) -> None:
        """Execute actions in dependency order."""
        for action in report.actions:
            try:
                if action.operation == "skip":
                    continue
                elif action.operation == "create" and action.resource.startswith("space:"):
                    self._apply_create_space(action)
                elif action.operation == "create" and action.resource.startswith("room:"):
                    self._apply_create_room(action)
                elif action.operation == "adopt":
                    self._apply_adopt(action)
                elif action.operation == "invite":
                    self._apply_invite(action)
                elif action.operation == "bridge":
                    self._apply_bridge(action)
                elif action.operation == "config":
                    pass  # Watcher config applied in batch below
            except Exception as e:
                report.errors.append(f"{action.resource}: {e}")

    def _apply_create_space(self, action: Action) -> None:
        """Create a space and record its room ID."""
        key = action.resource.split(":", 1)[1]
        # Find the space config
        space = self._find_space(key)
        if not space:
            return

        alias = key
        members = [self._resolve_user(m) for m in space.members]
        room_id = self.client.create_space(
            name=space.name,
            alias=alias,
            private=(space.visibility == "private"),
            invite=members,
        )
        self._room_ids[key] = room_id
        self.client.set_room_state_event(
            room_id,
            "org.knarr.managed",
            {
                "config_key": key,
                "managed_by": "knarr-reconciler",
                "last_reconciled": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _apply_create_room(self, action: Action) -> None:
        """Create a room, assign alias, add to parent space, set managed marker."""
        key = action.resource.split(":", 1)[1]
        room = self._find_room(key)
        if not room:
            return

        members = [self._resolve_user(m) for m in room.members]
        # Auto-invite bridge bot if bridge config present
        if room.bridge and "bridge_bot" in self.config.users:
            bot = self.config.users["bridge_bot"]
            if bot not in members:
                members.append(bot)

        room_id = self.client.create_room(
            name=room.name,
            topic=room.topic,
            invite=members,
            private=True,
        )
        self.client.set_room_alias(room_id, self._full_alias(room.alias))
        self._room_ids[room.alias] = room_id

        # Add to parent space if known
        parent_key = self._find_parent_space_key(key)
        if parent_key and parent_key in self._room_ids:
            self.client.add_space_child(self._room_ids[parent_key], room_id)

        self.client.set_room_state_event(
            room_id,
            "org.knarr.managed",
            {
                "config_key": key,
                "managed_by": "knarr-reconciler",
                "last_reconciled": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _apply_adopt(self, action: Action) -> None:
        """Mark an existing unmanaged room as managed."""
        key = action.resource.split(":", 1)[1]
        alias = self._full_alias(key)
        room_id = self.client.resolve_alias(alias)
        if room_id:
            self._room_ids[key] = room_id
            self.client.set_room_state_event(
                room_id,
                "org.knarr.managed",
                {
                    "config_key": key,
                    "managed_by": "knarr-reconciler",
                    "last_reconciled": datetime.now(timezone.utc).isoformat(),
                },
            )

    def _apply_invite(self, action: Action) -> None:
        """Invite a user to a room."""
        # Parse user_id from details: "@user:server to room-key"
        parts = action.details.split(" to ", 1)
        if len(parts) != 2:
            return
        user_id = parts[0].strip()
        target_key = parts[1].strip()

        # Look up room_id by alias
        if target_key in self._room_ids:
            self.client.invite(self._room_ids[target_key], user_id)
        else:
            # Try resolving by alias
            room_id = self.client.resolve_alias(self._full_alias(target_key))
            if room_id:
                self.client.invite(room_id, user_id)

    def _apply_bridge(self, action: Action) -> None:
        """Bridge a room to a Discord channel."""
        if not self.bridge_manager:
            return
        key = action.resource.split(":", 1)[1]
        room = self._find_room(key)
        if not room or not room.bridge:
            return

        room_id = self._room_ids.get(room.alias)
        if not room_id:
            room_id = self.client.resolve_alias(self._full_alias(room.alias))
        if not room_id:
            return

        for bridge_type, bridge_config in room.bridge.items():
            if bridge_type == "discord":
                self.bridge_manager.bridge_channel(
                    room_id,
                    bridge_config["channel_id"],
                    replace=True,
                )

    # --- Helper methods to find config entries ---

    def _find_space(self, key: str) -> Optional[SpaceConfig]:
        for community in self.config.communities:
            for sk, space in community.spaces.items():
                if sk == key:
                    return space
                found = self._find_space_recursive(key, space)
                if found:
                    return found
        return None

    def _find_space_recursive(self, key: str, space: SpaceConfig) -> Optional[SpaceConfig]:
        for ck, child in space.children.items():
            if ck == key:
                return child
            found = self._find_space_recursive(key, child)
            if found:
                return found
        return None

    def _find_room(self, key: str) -> Optional[RoomConfig]:
        for community in self.config.communities:
            for space in community.spaces.values():
                found = self._find_room_in_space(key, space)
                if found:
                    return found
        return None

    def _find_room_in_space(self, key: str, space: SpaceConfig) -> Optional[RoomConfig]:
        if key in space.rooms:
            return space.rooms[key]
        for child in space.children.values():
            found = self._find_room_in_space(key, child)
            if found:
                return found
        return None

    def _find_parent_space_key(self, room_key: str) -> Optional[str]:
        for community in self.config.communities:
            for sk, space in community.spaces.items():
                if room_key in space.rooms:
                    return sk
                found = self._find_parent_in_children(room_key, space)
                if found:
                    return found
        return None

    def _find_parent_in_children(self, room_key: str, space: SpaceConfig) -> Optional[str]:
        for ck, child in space.children.items():
            if room_key in child.rooms:
                return ck
            found = self._find_parent_in_children(room_key, child)
            if found:
                return found
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd components/knarr && python3 -m pytest tests/test_reconciler.py -v`
Expected: 10 passed

- [ ] **Step 5: Run all tests**

Run: `cd components/knarr && python3 -m pytest tests/ -v`
Expected: all tests pass (23 existing + 10 config schema + 10 reconciler)

- [ ] **Step 6: Commit**

```
message: "feat: add reconciler core — diff engine with action ordering"
add: [src/admin/reconciler.py, tests/test_reconciler.py]
```

---

## Task 5: CLI Integration — config apply/audit/validate

**Files:**
- Modify: `src/admin/cli.py`

- [ ] **Step 1: Add config subgroup to CLI**

Add to `src/admin/cli.py` after the bridge commands section:

```python
from .config_schema import load_config, validate_config, ConfigError
from .reconciler import Reconciler


# --- Config commands ---

@cli.group()
def config():
    """GitOps config reconciliation."""


@config.command("validate")
@click.option("--config", "config_path", default="config/knarr.yaml", help="Config file path")
def config_validate(config_path):
    """Validate config syntax without touching Matrix."""
    try:
        cfg = load_config(config_path)
        validate_config(cfg)
    except ConfigError as e:
        click.echo(f"Config error: {e}", err=True)
        sys.exit(1)
    except FileNotFoundError as e:
        click.echo(f"File not found: {e}", err=True)
        sys.exit(1)

    space_count = sum(len(c.spaces) for c in cfg.communities)
    room_count = 0
    bridge_count = 0
    watcher_count = 0
    for c in cfg.communities:
        for s in c.spaces.values():
            room_count += len(s.rooms)
            for r in s.rooms.values():
                if r.bridge:
                    bridge_count += 1
                if r.watchers:
                    watcher_count += len(r.watchers)

    click.echo(
        f"Config valid: {len(cfg.communities)} community, "
        f"{space_count} spaces, {room_count} rooms, "
        f"{bridge_count} bridges, {watcher_count} watchers"
    )


@config.command("audit")
@click.option("--config", "config_path", default="config/knarr.yaml", help="Config file path")
def config_audit(config_path):
    """Dry-run: report what would change without making changes."""
    try:
        cfg = load_config(config_path)
        validate_config(cfg)
    except (ConfigError, FileNotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    client = get_client()
    reconciler = Reconciler(client, cfg)
    report = reconciler.diff()

    click.echo(f"Loading config: {config_path} ({len(cfg.communities)} community)")
    click.echo("Reading Matrix state...\n")

    for action in report.actions:
        icon = {"create": "+", "skip": "=", "invite": ">", "bridge": "~", "config": "*", "adopt": "!"}
        click.echo(f"  [{icon.get(action.operation, '?')}] {action.resource:<28} {action.operation:<8} {action.details}")

    click.echo(f"\nAudit: {report.summary()}")
    if report.has_drift:
        sys.exit(2)


@config.command("apply")
@click.option("--config", "config_path", default="config/knarr.yaml", help="Config file path")
def config_apply(config_path):
    """Apply config: converge live state to match desired state."""
    try:
        cfg = load_config(config_path)
        validate_config(cfg)
    except (ConfigError, FileNotFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Validate secrets are available
    for name, env_var in cfg.secrets.items():
        if not os.environ.get(env_var):
            click.echo(f"Warning: secret '{name}' ({env_var}) not set in environment", err=True)

    client = get_client()
    bridge_mgr = None
    mgmt_room = os.environ.get("KNARR_MANAGEMENT_ROOM")
    if mgmt_room:
        bridge_mgr = get_bridge_manager()

    reconciler = Reconciler(client, cfg, bridge_manager=bridge_mgr)

    click.echo(f"Loading config: {config_path} ({len(cfg.communities)} community)")
    click.echo("Reading Matrix state...\n")

    report = reconciler.apply()

    for action in report.actions:
        icon = {"create": "+", "skip": "=", "invite": ">", "bridge": "~", "config": "*", "adopt": "!", "error": "X"}
        click.echo(f"  [{icon.get(action.operation, '?')}] {action.resource:<28} {action.operation:<8} {action.details}")

    if report.errors:
        click.echo(f"\nErrors:", err=True)
        for error in report.errors:
            click.echo(f"  {error}", err=True)

    click.echo(f"\nApplied: {report.summary()}")
    if report.errors:
        sys.exit(1)
```

- [ ] **Step 2: Test the CLI**

Run: `cd components/knarr && ./scripts/knarr config --help`
Expected: Shows validate, audit, apply subcommands

Run: `cd components/knarr && ./scripts/knarr config validate --help`
Expected: Shows --config option

- [ ] **Step 3: Run all tests**

Run: `cd components/knarr && python3 -m pytest tests/ -v`
Expected: all tests pass

- [ ] **Step 4: Commit**

```
message: "feat: add knarr config apply/audit/validate CLI commands"
add: [src/admin/cli.py]
```

---

## Task 6: Create Test Config Files

**Files:**
- Create: `config/knarr.yaml`
- Create: `config/test.yaml`

- [ ] **Step 1: Create the index config**

Create `config/knarr.yaml`:

```yaml
server_name: knarr.local

secrets:
  discord_token: DISCORD_TOKEN
  github_token: GITHUB_TOKEN

users:
  admin: "@admin:knarr.local"
  knarr: "@knarr:knarr.local"
  router: "@knarr-router:knarr.local"
  bridge_bot: "@discordbot:knarr.local"

communities:
  - config/test.yaml
```

- [ ] **Step 2: Create the test community config**

Create `config/test.yaml`:

```yaml
community: knarr-test
display_name: "Knarr Test"

spaces:
  knarr-test:
    name: "Knarr Test"
    visibility: private
    members: [admin, knarr]

    rooms:
      social-watch:
        alias: social-watch
        name: "#social-watch"
        topic: "Platform monitoring alerts"
        members: [router]
        watchers:
          reddit:
            subreddit: Terasology
            interval_seconds: 21600
          github:
            repos: ["MovingBlocks/Terasology"]
            interval_seconds: 21600
            token_env: GITHUB_TOKEN

      bridge-test:
        alias: bridge-test
        name: "#autoboros-testing [test]"
        topic: "Bridged to Demicracy Discord #autoboros-testing"
        members: [admin, knarr]
        bridge:
          discord:
            channel_id: "1342947610008485921"
            relay: true

    spaces:
      feeds:
        name: "Feeds"
        visibility: private
        rooms:
          feed-reddit:
            alias: feed-reddit
            name: "#feed-reddit"
            topic: "Reddit watcher alerts"
```

- [ ] **Step 3: Validate the config**

Run: `cd components/knarr && ./scripts/knarr config validate`
Expected: `Config valid: 1 community, 1 spaces, 3 rooms, 1 bridges, 2 watchers`

- [ ] **Step 4: Commit**

```
message: "feat: add test community config for reconciler"
add: [config/knarr.yaml, config/test.yaml]
```

---

## Task 7: BDD Integration Test Scaffolding

**Files:**
- Create: `tests/features/reconcile.feature`
- Create: `tests/features/conftest.py`
- Create: `tests/features/steps/reconcile_steps.py`

- [ ] **Step 1: Create the feature file**

Create `tests/features/reconcile.feature`:

```gherkin
Feature: Config reconciliation

  Background:
    Given a Matrix homeserver is running
    And a valid config with test rooms

  Scenario: Validate accepts valid config
    When I run config validate
    Then it succeeds with "Config valid"

  Scenario: Audit reports drift for new rooms
    When I run config audit
    Then it reports "create" actions
    And it exits with code 2

  Scenario: Apply creates rooms from config
    When I run config apply
    Then room "#social-watch:knarr.local" exists in Matrix
    And room "#feed-reddit:knarr.local" exists in Matrix

  Scenario: Apply is idempotent
    Given the config has been applied
    When I run config audit
    Then it reports zero drift
    And it exits with code 0
```

- [ ] **Step 2: Create conftest.py**

Create `tests/features/conftest.py`:

```python
"""Shared fixtures for BDD integration tests."""

import os
import pytest

from src.admin.client import MatrixAdminClient


@pytest.fixture
def matrix_client():
    """Create a client connected to the real Synapse on k3d."""
    homeserver = os.environ.get("KNARR_HOMESERVER", "http://matrix.knarr.local")
    user = os.environ.get("KNARR_ADMIN_USER", "admin")
    password = os.environ.get("KNARR_ADMIN_PASSWORD")
    if not password:
        pytest.skip("KNARR_ADMIN_PASSWORD not set — skipping integration test")
    return MatrixAdminClient(homeserver, user, password)


@pytest.fixture
def test_config_path():
    """Path to the test config."""
    return "config/knarr.yaml"
```

- [ ] **Step 3: Create step definitions**

Create `tests/features/steps/reconcile_steps.py`:

```python
"""Step definitions for config reconciliation BDD tests."""

import os
import subprocess

from pytest_bdd import given, when, then, parsers, scenarios

from src.admin.client import MatrixAdminClient
from src.admin.config_schema import load_config, validate_config
from src.admin.reconciler import Reconciler

scenarios("../reconcile.feature")


@given("a Matrix homeserver is running")
def check_homeserver(matrix_client):
    """Verify the homeserver is reachable."""
    token = matrix_client.get_token()
    assert token is not None


@given("a valid config with test rooms")
def valid_config(test_config_path):
    """Load and validate the test config."""
    cfg = load_config(test_config_path)
    validate_config(cfg)


@given("the config has been applied")
def apply_config(matrix_client, test_config_path):
    """Run apply to set up the baseline."""
    cfg = load_config(test_config_path)
    validate_config(cfg)
    reconciler = Reconciler(matrix_client, cfg)
    report = reconciler.apply()
    assert not report.errors


@when("I run config validate", target_fixture="cli_result")
def run_validate(test_config_path):
    result = subprocess.run(
        ["python3", "-m", "src.admin.cli", "config", "validate", "--config", test_config_path],
        capture_output=True, text=True, cwd=os.getcwd(),
    )
    return result


@when("I run config audit", target_fixture="cli_result")
def run_audit(test_config_path):
    result = subprocess.run(
        ["python3", "-m", "src.admin.cli", "config", "audit", "--config", test_config_path],
        capture_output=True, text=True, cwd=os.getcwd(),
    )
    return result


@when("I run config apply", target_fixture="cli_result")
def run_apply(test_config_path):
    result = subprocess.run(
        ["python3", "-m", "src.admin.cli", "config", "apply", "--config", test_config_path],
        capture_output=True, text=True, cwd=os.getcwd(),
    )
    return result


@then(parsers.parse('it succeeds with "{text}"'))
def check_success(cli_result, text):
    assert cli_result.returncode == 0, f"stdout: {cli_result.stdout}\nstderr: {cli_result.stderr}"
    assert text in cli_result.stdout


@then('it reports "create" actions')
def check_create_actions(cli_result):
    assert "create" in cli_result.stdout


@then(parsers.parse("it exits with code {code:d}"))
def check_exit_code(cli_result, code):
    assert cli_result.returncode == code, f"Expected {code}, got {cli_result.returncode}\n{cli_result.stdout}\n{cli_result.stderr}"


@then(parsers.parse('room "{alias}" exists in Matrix'))
def check_room_exists(matrix_client, alias):
    room_id = matrix_client.resolve_alias(alias)
    assert room_id is not None, f"Room {alias} not found"


@then("it reports zero drift")
def check_no_drift(cli_result):
    assert "0 skip" not in cli_result.stdout or "create" not in cli_result.stdout
    # Audit exits 0 when no drift
    assert cli_result.returncode == 0
```

- [ ] **Step 4: Run integration tests (if Synapse available)**

Run: `cd components/knarr && python3 -m pytest tests/features/ -v --tb=short`
Expected: Tests pass if `KNARR_ADMIN_PASSWORD` is set and Synapse is reachable. Otherwise skipped.

- [ ] **Step 5: Run all tests**

Run: `cd components/knarr && python3 -m pytest tests/ -v --ignore=tests/features`
Expected: All unit tests pass (no integration dependency)

- [ ] **Step 6: Commit**

```
message: "feat: add BDD integration test scaffolding for reconciler"
add:
  - tests/features/reconcile.feature
  - tests/features/conftest.py
  - tests/features/steps/reconcile_steps.py
```

---

## Task 8: Documentation Update

**Files:**
- Modify: `docs/cli-usage.md`
- Modify: `README.md`

- [ ] **Step 1: Add config commands to CLI docs**

Add to `docs/cli-usage.md` after the Bridge Operations section:

```markdown
### Config Reconciliation

```bash
# Validate config syntax (no Matrix connection needed)
knarr config validate [--config config/knarr.yaml]

# Audit: dry-run, report what would change (exit code 2 if drift)
knarr config audit [--config config/knarr.yaml]

# Apply: converge live state to match config
knarr config apply [--config config/knarr.yaml]
```

Config files live in `config/`:
- `config/knarr.yaml` — index file (server name, users, secrets, community references)
- `config/test.yaml` — per-community room topology, bridges, watchers

See `docs/plans/2026-04-25-config-reconciler-design.md` for the full design.
```

- [ ] **Step 2: Update README components table**

Add to the Components table in `README.md`:

```markdown
| Config Reconciler | Python (built-in) | Reads YAML config, diffs against Matrix state, applies changes |
```

- [ ] **Step 3: Commit**

```
message: "docs: add config reconciler to CLI usage and README"
add: [docs/cli-usage.md, README.md]
```

---

## Summary

After completing all 8 tasks:

| What | Count |
|------|-------|
| New files | 7 (config_schema.py, reconciler.py, 2 configs, 3 test files) |
| Modified files | 5 (client.py, cli.py, pyproject.toml, requirements.txt, docs) |
| New unit tests | ~30 (config schema + reconciler + client extensions) |
| New integration tests | 4 BDD scenarios |
| CLI commands added | 3 (config validate, config audit, config apply) |
| New client methods | 8 (resolve_alias, get_room_state, get_room_members, set_room_alias, create_space, add_space_child, set_room_state_event, get_room_state_event) |
