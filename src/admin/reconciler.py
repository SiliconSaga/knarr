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
            # Members are invited as part of create; the creator (admin) is
            # auto-joined by Synapse so we skip them here.
            creator_mxid = f"@{self.client.admin_user}:{self.config.server_name}"
            for member_ref in room.members:
                user_id = self._resolve_user(member_ref)
                if user_id == creator_mxid:
                    continue
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
        # Filter out the creator (admin user) — Synapse rejects inviting them
        creator_mxid = f"@{self.client.admin_user}:{self.config.server_name}"
        members = [
            self._resolve_user(m)
            for m in space.members
            if self._resolve_user(m) != creator_mxid
        ]
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

        # Filter out the creator (admin user) — Synapse rejects inviting them
        creator_mxid = f"@{self.client.admin_user}:{self.config.server_name}"
        members = [
            self._resolve_user(m)
            for m in room.members
            if self._resolve_user(m) != creator_mxid
        ]
        # Auto-invite bridge bot if bridge config present
        if room.bridge and "bridge_bot" in self.config.users:
            bot = self.config.users["bridge_bot"]
            if bot not in members and bot != creator_mxid:
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
        if room_id:
            # The bridge operator user (used by BridgeManager) needs to be a
            # member of the room to send the !discord bridge command. They
            # were invited during room creation; auto-join here.
            try:
                self.bridge_manager.client.join_room(room_id)
            except Exception:
                pass  # Already joined or other transient issue

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
