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
        full_path = config_dir / rel_path
        if not full_path.exists():
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

    seen = set()
    for alias in all_aliases:
        if alias in seen:
            raise ConfigError(f"Duplicate room alias: {alias}")
        seen.add(alias)

    for ref in all_user_refs:
        if ref not in config.users:
            raise ConfigError(
                f"Unknown user reference: {ref} "
                f"(not in knarr.yaml users: {list(config.users.keys())})"
            )
