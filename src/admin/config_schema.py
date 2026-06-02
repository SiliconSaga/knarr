"""Config loading, parsing, and validation for the Knarr reconciler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    """Raised when config is invalid."""


def _require_mapping(value: object, field_name: str) -> dict:
    """Coerce ``value`` to a dict or raise ConfigError.

    YAML's flexibility means ``rooms: []`` (a list) or ``rooms: null`` are
    syntactically valid but trip ``AttributeError``/``TypeError`` later when we
    try to ``.items()`` on them. Catch the type mismatch up front with a clear
    message tied to the field name.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{field_name} must be a mapping, got {type(value).__name__}")
    return value


def _require_str_list(value: object, field_name: str) -> list[str]:
    """Coerce ``value`` to a list[str] or raise ConfigError."""
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ConfigError(f"{field_name} must be a list of strings")
    return value


def _require_str(value: object, field_name: str) -> str:
    """Coerce value to str or raise ConfigError with field context."""
    if not isinstance(value, str):
        raise ConfigError(
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    return value


@dataclass
class RoomConfig:
    alias: str
    name: str
    topic: str = ""
    members: list[str] = field(default_factory=list)
    bridge: dict | None = None
    watchers: dict | None = None

    @classmethod
    def from_dict(cls, key: str, data: dict) -> RoomConfig:
        return cls(
            alias=data.get("alias", key),
            name=data.get("name", key),
            topic=data.get("topic", ""),
            members=_require_str_list(data.get("members"), f"room.{key}.members"),
            bridge=_require_mapping(data["bridge"], f"room.{key}.bridge")
            if "bridge" in data and data["bridge"] is not None
            else None,
            watchers=_require_mapping(data["watchers"], f"room.{key}.watchers")
            if "watchers" in data and data["watchers"] is not None
            else None,
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
        for room_key, room_data in _require_mapping(
            data.get("rooms"), f"space.{key}.rooms"
        ).items():
            rooms[room_key] = RoomConfig.from_dict(
                room_key, _require_mapping(room_data, f"room.{room_key}")
            )

        children = {}
        for space_key, space_data in _require_mapping(
            data.get("spaces"), f"space.{key}.spaces"
        ).items():
            children[space_key] = SpaceConfig.from_dict(
                space_key, _require_mapping(space_data, f"space.{space_key}")
            )

        return cls(
            name=data.get("name", key),
            visibility=data.get("visibility", "private"),
            members=_require_str_list(data.get("members"), f"space.{key}.members"),
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
        if "community" not in data:
            raise ConfigError("'community' key is required in community config")
        spaces = {}
        for space_key, space_data in _require_mapping(
            data.get("spaces"), "community.spaces"
        ).items():
            spaces[space_key] = SpaceConfig.from_dict(
                space_key, _require_mapping(space_data, f"space.{space_key}")
            )

        return cls(
            community=data["community"],
            display_name=data.get("display_name", data["community"]),
            spaces=spaces,
        )


_VALID_SCOPE_PREFIXES = ("community/", "user/", "group/")


@dataclass
class InstanceConfig:
    """A single WatcherInstance: one (platform, access_path, scope) row.

    Each instance is independently configured and runs against one source
    on behalf of one identity scope. See
    docs/plans/2026-05-30-knarr-source-identity-design.md for the model.
    """
    id: str
    platform: str
    access_path: str
    scope: str
    polling: dict
    platform_config: dict
    target_room: str            # config-key of the Matrix room to route alerts to.
                                # Stored in Phase 1 but NOT consumed by the router
                                # yet (Phase 1 router still posts to MATRIX_ROOM_ID,
                                # preserving today's behaviour). Phase 2 reads it.
    credentials_ref: dict | None = None  # {"secret_name": str, "secret_key": str}

    @classmethod
    def from_dict(cls, data: dict) -> InstanceConfig:
        required = ("id", "platform", "access_path", "scope",
                    "polling", "platform_config", "target_room")
        for key in required:
            if key not in data:
                raise ConfigError(f"instance is missing required field: {key}")
        iid = data["id"]
        return cls(
            id=_require_str(iid, "instance.id"),
            platform=_require_str(data["platform"], f"instance.{iid}.platform"),
            access_path=_require_str(data["access_path"],
                                     f"instance.{iid}.access_path"),
            scope=_require_str(data["scope"], f"instance.{iid}.scope"),
            polling=_require_mapping(data["polling"], f"instance.{iid}.polling"),
            platform_config=_require_mapping(
                data["platform_config"], f"instance.{iid}.platform_config"),
            target_room=_require_str(data["target_room"],
                                     f"instance.{iid}.target_room"),
            credentials_ref=(
                _require_mapping(
                    data["credentials_ref"],
                    f"instance.{iid}.credentials_ref")
                if data.get("credentials_ref") is not None else None
            ),
        )


@dataclass
class KnarrConfig:
    server_name: str
    secrets: dict[str, str] = field(default_factory=dict)
    users: dict[str, str] = field(default_factory=dict)
    communities: list[CommunityConfig] = field(default_factory=list)
    instances: list[InstanceConfig] = field(default_factory=list)

    @classmethod
    def from_dict(
        cls,
        data: dict,
        community_loader: Callable[[str], dict],
    ) -> KnarrConfig:
        if "server_name" not in data:
            raise ConfigError("server_name is required in the index config")

        community_paths = _require_str_list(data.get("communities"), "communities")
        communities = []
        for path in community_paths:
            community_data = community_loader(path)
            communities.append(CommunityConfig.from_dict(community_data))

        instances_raw = data.get("instances") or []
        if not isinstance(instances_raw, list):
            raise ConfigError(
                f"instances must be a list, got {type(instances_raw).__name__}"
            )
        instances = [InstanceConfig.from_dict(_require_mapping(i, "instance"))
                     for i in instances_raw]

        return cls(
            server_name=data["server_name"],
            secrets=_require_mapping(data.get("secrets"), "secrets"),
            users=_require_mapping(data.get("users"), "users"),
            communities=communities,
            instances=instances,
        )


def _load_yaml_dict(path: Path) -> dict:
    """Read a YAML file, surface parse errors with file context, and require a dict.

    Raw ``yaml.safe_load`` raises ``yaml.YAMLError`` on malformed input and can
    return ``None`` or non-dict values for empty/invalid files; both bubble up
    later as confusing ``TypeError``s. Wrap them so the CLI can present a clean
    ConfigError.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse YAML at {path}: {e}") from e
    if data is None:
        raise ConfigError(f"Config at {path} is empty")
    if not isinstance(data, dict):
        raise ConfigError(
            f"Config at {path} must be a YAML mapping, got {type(data).__name__}"
        )
    return data


def load_config(config_path: str) -> KnarrConfig:
    """Load the index config and all referenced community configs."""
    config_dir = Path(config_path).parent
    index_data = _load_yaml_dict(Path(config_path))

    def community_loader(rel_path: str) -> dict:
        full_path = config_dir / rel_path
        if not full_path.exists():
            full_path = config_dir.parent / rel_path
        if not full_path.exists():
            raise ConfigError(f"Community config not found: {rel_path}")
        return _load_yaml_dict(full_path)

    return KnarrConfig.from_dict(index_data, community_loader)


def _collect_aliases(space: SpaceConfig) -> list[str]:
    """Recursively collect all room aliases from a space."""
    aliases = [r.alias for r in space.rooms.values()]
    for child in space.children.values():
        aliases.extend(_collect_aliases(child))
    return aliases


def _collect_room_keys(space: SpaceConfig) -> list[str]:
    """Recursively collect all room keys from a space."""
    keys = list(space.rooms.keys())
    for child in space.children.values():
        keys.extend(_collect_room_keys(child))
    return keys


def _collect_space_keys(space: SpaceConfig) -> list[str]:
    """Recursively collect all child-space keys nested under this space."""
    keys: list[str] = []
    for child_key, child in space.children.items():
        keys.append(child_key)
        keys.extend(_collect_space_keys(child))
    return keys


def _collect_user_refs(space: SpaceConfig) -> set[str]:
    """Recursively collect all user references from a space."""
    refs = set(space.members)
    for room in space.rooms.values():
        refs.update(room.members)
    for child in space.children.values():
        refs.update(_collect_user_refs(child))
    return refs


def validate_config(config: KnarrConfig) -> None:
    """Validate cross-references and uniqueness constraints.

    Accumulates all errors and raises a single ConfigError with the full set,
    so users can fix everything in one pass.

    Room and space keys must be globally unique (within their type) because the
    reconciler keys ``Action.resource`` and ``_room_ids`` by local key alone —
    a collision would silently apply changes to the wrong room.
    """
    all_aliases: list[str] = []
    all_room_keys: list[str] = []
    all_space_keys: list[str] = []
    all_user_refs: set[str] = set()

    for community in config.communities:
        for space_key, space in community.spaces.items():
            all_aliases.extend(_collect_aliases(space))
            all_room_keys.extend(_collect_room_keys(space))
            all_space_keys.append(space_key)
            all_space_keys.extend(_collect_space_keys(space))
            all_user_refs.update(_collect_user_refs(space))

    errors: list[str] = []

    seen_aliases: set[str] = set()
    for alias in all_aliases:
        if alias in seen_aliases:
            errors.append(f"Duplicate room alias: {alias}")
        seen_aliases.add(alias)

    seen_room_keys: set[str] = set()
    for key in all_room_keys:
        if key in seen_room_keys:
            errors.append(f"Duplicate room key: {key} (room keys must be globally unique)")
        seen_room_keys.add(key)

    seen_space_keys: set[str] = set()
    for key in all_space_keys:
        if key in seen_space_keys:
            errors.append(f"Duplicate space key: {key} (space keys must be globally unique)")
        seen_space_keys.add(key)

    known = set(config.users.keys())
    for ref in sorted(all_user_refs):
        if ref not in known:
            errors.append(f"Unknown user reference: {ref} (known: {sorted(known)})")

    for inst in config.instances:
        if not any(inst.scope.startswith(p) for p in _VALID_SCOPE_PREFIXES):
            errors.append(
                f"Instance '{inst.id}': scope '{inst.scope}' must start with "
                f"one of {_VALID_SCOPE_PREFIXES}"
            )
        if inst.credentials_ref is not None:
            secret_name = inst.credentials_ref.get("secret_name")
            if not secret_name:
                errors.append(
                    f"Instance '{inst.id}': credentials_ref.secret_name "
                    f"is required when credentials_ref is set"
                )
            elif not isinstance(secret_name, str):
                errors.append(
                    f"Instance '{inst.id}': credentials_ref.secret_name "
                    f"must be a string, got {type(secret_name).__name__}"
                )
            secret_key = inst.credentials_ref.get("secret_key")
            if not secret_key:
                errors.append(
                    f"Instance '{inst.id}': credentials_ref.secret_key "
                    f"is required when credentials_ref is set"
                )
            elif not isinstance(secret_key, str):
                errors.append(
                    f"Instance '{inst.id}': credentials_ref.secret_key "
                    f"must be a string, got {type(secret_key).__name__}"
                )

    if errors:
        raise ConfigError("\n  - " + "\n  - ".join(errors))
