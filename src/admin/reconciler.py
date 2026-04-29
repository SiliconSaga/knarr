"""Knarr config reconciler — diffs desired state against live Matrix and applies changes."""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from .client import MatrixAdminClient
from .config_schema import KnarrConfig, RoomConfig, SpaceConfig

logger = logging.getLogger(__name__)


@dataclass
class Action:
    """A single reconciler action.

    ``resource`` is the canonical identifier (``space:<key>``, ``room:<key>``,
    ``bridge:<key>``, ``watcher:<type>``) that matches the apply lookup key.
    ``target`` and ``subject`` are structured fields used by apply handlers
    (e.g., the user_id to invite, or the parent space key). ``details`` is a
    human-readable display string and is never parsed.
    """

    resource: str
    operation: str  # create, update, invite, bridge, noop, skip, adopt
    details: str
    target: str | None = None
    subject: str | None = None


@dataclass
class ReconcileReport:
    actions: list[Action] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        # "skip" = we own this and it's correct.
        # "noop" = informational only (e.g., watcher config we don't reconcile yet);
        # neither should drive the audit drift exit code.
        return any(a.operation not in ("skip", "noop") for a in self.actions)

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
        # Keyed by Action.resource (e.g. "space:knarr-test", "room:social-watch")
        self._room_ids: dict[str, str] = {}

    def diff(self) -> ReconcileReport:
        """Compute the diff between desired config and live state."""
        report = ReconcileReport()
        for community in self.config.communities:
            for space_key, space in community.spaces.items():
                self._diff_space(space_key, space, report)
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

    def _creator_mxid(self) -> str:
        return f"@{self.client.admin_user}:{self.config.server_name}"

    def _managed_event_payload(self, key: str) -> dict:
        return {
            "config_key": key,
            "managed_by": "knarr-reconciler",
            "last_reconciled": datetime.now(UTC).isoformat(),
        }

    def _managed_by_us(self, event: dict | None, expected_key: str) -> bool:
        """True only if the org.knarr.managed marker matches our payload contract.

        A bare truthy check would treat any state event as "managed", including
        markers from a different reconciler or for a different config key — which
        could cause us to skip reconciliation on the wrong room.
        """
        if not isinstance(event, dict):
            return False
        return (
            event.get("managed_by") == "knarr-reconciler"
            and event.get("config_key") == expected_key
        )

    def _diff_space(
        self,
        key: str,
        space: SpaceConfig,
        report: ReconcileReport,
    ) -> None:
        alias = self._full_alias(key)
        resource = f"space:{key}"
        room_id = self.client.resolve_alias(alias)

        if room_id is None:
            report.actions.append(Action(resource, "create", f'"{space.name}" ({space.visibility})'))
        else:
            self._room_ids[resource] = room_id
            managed = self.client.get_room_state_event(room_id, "org.knarr.managed")
            if self._managed_by_us(managed, key):
                report.actions.append(Action(resource, "skip", f"already exists ({alias})"))
            else:
                report.actions.append(Action(resource, "adopt", f"exists but unmanaged ({alias})"))

            # Check membership — count joined AND already-invited users so we
            # don't re-invite anyone who simply hasn't accepted yet.
            current_members = self.client.get_room_members_with_state(room_id)
            for member_ref in space.members:
                user_id = self._resolve_user(member_ref)
                if user_id not in current_members:
                    report.actions.append(
                        Action(
                            resource,
                            "invite",
                            f"{user_id} to {key}",
                            target=resource,
                            subject=user_id,
                        )
                    )

        # Diff rooms within this space
        for room_key, room in space.rooms.items():
            self._diff_room(room_key, room, key, report)

        # Diff child spaces
        for child_key, child_space in space.children.items():
            self._diff_space(child_key, child_space, report)

    def _diff_room(
        self,
        key: str,
        room: RoomConfig,
        parent_space_key: str,
        report: ReconcileReport,
    ) -> None:
        alias = self._full_alias(room.alias)
        resource = f"room:{key}"
        room_id = self.client.resolve_alias(alias)

        if room_id is None:
            # _apply_create_room passes invite=members to createRoom, so members
            # are seeded as part of room creation. Don't emit duplicate per-member
            # invite actions here — Synapse would 4xx on "already invited" and
            # blow up the apply run.
            report.actions.append(
                Action(resource, "create", f'"{room.name}" in {parent_space_key}')
            )
        else:
            self._room_ids[resource] = room_id
            managed = self.client.get_room_state_event(room_id, "org.knarr.managed")
            if self._managed_by_us(managed, key):
                report.actions.append(Action(resource, "skip", f"already exists ({alias})"))
            else:
                report.actions.append(Action(resource, "adopt", f"exists but unmanaged ({alias})"))

            # Check membership — count joined AND already-invited users so we
            # don't re-invite anyone who simply hasn't accepted yet.
            current_members = self.client.get_room_members_with_state(room_id)
            for member_ref in room.members:
                user_id = self._resolve_user(member_ref)
                if user_id not in current_members:
                    report.actions.append(
                        Action(
                            resource,
                            "invite",
                            f"{user_id} to {key}",
                            target=resource,
                            subject=user_id,
                        )
                    )

        # Bridge config — encode bridge_type into resource so each (room,
        # bridge_type) pair is its own apply unit. Without this, two
        # bridge entries on the same room collide on resource and
        # _apply_bridge would loop over every bridge type per emitted
        # action (N×N apply).
        if room.bridge:
            for bridge_type, bridge_config in room.bridge.items():
                report.actions.append(
                    Action(
                        f"bridge:{key}:{bridge_type}",
                        "bridge",
                        f"{bridge_type} channel {bridge_config.get('channel_id', '?')}",
                        target=resource,
                        subject=bridge_type,
                    )
                )

        # Watcher config — informational only. Apply path isn't implemented
        # yet (watchers run as a separate Deployment), so emit as "noop" so
        # the action shows up in audit output without driving the drift exit
        # code or being counted as "applied" by config apply. Resource
        # includes the room key so two rooms watching the same source don't
        # collide on identifier.
        if room.watchers:
            for watcher_type, watcher_config in room.watchers.items():
                report.actions.append(
                    Action(
                        f"watcher:{watcher_type}:{key}",
                        "noop",
                        f"{watcher_type} in {key}: {watcher_config} (apply not yet implemented)",
                    )
                )

    def _diff_watchers(self, report: ReconcileReport) -> None:
        """Watchers are aggregated across all rooms — nothing extra to diff here.
        Individual watcher actions are already added per-room in _diff_room."""

    def _apply_actions(self, report: ReconcileReport) -> None:
        """Execute actions in dependency order."""
        for action in report.actions:
            try:
                if action.operation in ("skip", "noop"):
                    continue
                if action.operation == "create" and action.resource.startswith("space:"):
                    self._apply_create_space(action)
                elif action.operation == "create" and action.resource.startswith("room:"):
                    self._apply_create_room(action)
                elif action.operation == "adopt":
                    self._apply_adopt(action)
                elif action.operation == "invite":
                    self._apply_invite(action)
                elif action.operation == "bridge":
                    self._apply_bridge(action)
            except httpx.HTTPStatusError as e:
                body = e.response.text[:500] if e.response.text else ""
                report.errors.append(
                    f"{action.resource}: {action.operation} failed "
                    f"({e.response.status_code} on {e.request.method} {e.request.url}): {body}"
                )
            except Exception as e:  # noqa: BLE001 — last-resort safety net
                report.errors.append(
                    f"{action.resource}: {action.operation} failed: {e!r}\n"
                    f"{traceback.format_exc()}"
                )

    def _apply_create_space(self, action: Action) -> None:
        """Create a space, link it to its parent space if nested, and mark it managed."""
        key = action.resource.split(":", 1)[1]
        space = self._find_space(key)
        if not space:
            return

        # Filter out the creator (admin user) — Synapse rejects inviting them
        creator_mxid = self._creator_mxid()
        members = [
            self._resolve_user(m)
            for m in space.members
            if self._resolve_user(m) != creator_mxid
        ]
        room_id = self.client.create_space(
            name=space.name,
            alias=key,
            private=(space.visibility == "private"),
            invite=members,
        )
        self._room_ids[action.resource] = room_id

        # Link nested spaces under their parent so the topology shows up in clients.
        # _apply_actions iterates in tree-traversal order, so the parent's room_id
        # is already cached by the time we reach a child.
        parent_key = self._find_parent_space_key_for_space(key)
        if parent_key:
            parent_resource = f"space:{parent_key}"
            if parent_resource in self._room_ids:
                self.client.add_space_child(self._room_ids[parent_resource], room_id)

        self.client.set_room_state_event(
            room_id,
            "org.knarr.managed",
            self._managed_event_payload(key),
        )

    def _apply_create_room(self, action: Action) -> None:
        """Atomically create a room with its alias, add to parent space, set managed marker."""
        key = action.resource.split(":", 1)[1]
        room = self._find_room(key)
        if not room:
            return

        # Filter out the creator (admin user) — Synapse rejects inviting them
        creator_mxid = self._creator_mxid()
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

        # Atomic create + alias assignment — Synapse rejects the call if the
        # alias is taken, preventing orphan rooms when retries collide.
        room_id = self.client.create_room(
            name=room.name,
            topic=room.topic,
            alias=room.alias,
            invite=members,
            private=True,
        )
        self._room_ids[action.resource] = room_id

        # Add to parent space if known
        parent_key = self._find_parent_space_key(key)
        if parent_key:
            parent_resource = f"space:{parent_key}"
            if parent_resource in self._room_ids:
                self.client.add_space_child(self._room_ids[parent_resource], room_id)

        self.client.set_room_state_event(
            room_id,
            "org.knarr.managed",
            self._managed_event_payload(key),
        )

    def _apply_adopt(self, action: Action) -> None:
        """Mark an existing unmanaged room as managed."""
        # Resolve the alias for this resource (room.alias may differ from key)
        key = action.resource.split(":", 1)[1]
        if action.resource.startswith("room:"):
            room = self._find_room(key)
            alias = self._full_alias(room.alias) if room else self._full_alias(key)
        else:
            alias = self._full_alias(key)

        room_id = self.client.resolve_alias(alias)
        if room_id:
            self._room_ids[action.resource] = room_id
            self.client.set_room_state_event(
                room_id,
                "org.knarr.managed",
                self._managed_event_payload(key),
            )

    def _apply_invite(self, action: Action) -> None:
        """Invite a user to a room or space."""
        if not action.target or not action.subject:
            return

        room_id = self._room_ids.get(action.target)
        if not room_id:
            # Fall back to alias resolution using the resource's room.alias
            target_type, target_key = action.target.split(":", 1)
            if target_type == "room":
                room = self._find_room(target_key)
                alias = self._full_alias(room.alias) if room else self._full_alias(target_key)
            else:
                alias = self._full_alias(target_key)
            room_id = self.client.resolve_alias(alias)

        if room_id:
            self.client.invite(room_id, action.subject)

    def _apply_bridge(self, action: Action) -> None:
        """Bridge a room to a single bridge type (Discord today).

        ``action.resource`` is ``bridge:<room_key>:<bridge_type>``; ``subject``
        carries the bridge_type so we apply only that one bridge per action,
        not every bridge configured on the room.
        """
        if not self.bridge_manager:
            return
        # bridge:<room_key>:<bridge_type> — split into 3 segments. Older
        # consumers wrote bridge:<room_key>; the early-return below handles
        # that benignly.
        parts = action.resource.split(":", 2)
        if len(parts) != 3:
            return
        _, key, bridge_type = parts

        room = self._find_room(key)
        if not room or not room.bridge or bridge_type not in room.bridge:
            return
        bridge_config = room.bridge[bridge_type]

        room_resource = f"room:{key}"
        room_id = self._room_ids.get(room_resource)
        if not room_id:
            room_id = self.client.resolve_alias(self._full_alias(room.alias))
        if not room_id:
            return

        # The bridge operator user (used by BridgeManager) needs to be a
        # member of the room to send the !discord bridge command. Surface real
        # errors but ignore "already in the room" (403).
        try:
            self.bridge_manager.client.join_room(room_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 403:
                raise
            logger.debug("Bridge user already in room %s", room_id)

        if bridge_type == "discord":
            self.bridge_manager.bridge_channel(
                room_id,
                bridge_config["channel_id"],
                replace=True,
            )

    # --- Helper methods to find config entries ---

    def _find_space(self, key: str) -> SpaceConfig | None:
        for community in self.config.communities:
            for sk, space in community.spaces.items():
                if sk == key:
                    return space
                found = self._find_space_recursive(key, space)
                if found:
                    return found
        return None

    def _find_space_recursive(self, key: str, space: SpaceConfig) -> SpaceConfig | None:
        for ck, child in space.children.items():
            if ck == key:
                return child
            found = self._find_space_recursive(key, child)
            if found:
                return found
        return None

    def _find_room(self, key: str) -> RoomConfig | None:
        for community in self.config.communities:
            for space in community.spaces.values():
                found = self._find_room_in_space(key, space)
                if found:
                    return found
        return None

    def _find_room_in_space(self, key: str, space: SpaceConfig) -> RoomConfig | None:
        if key in space.rooms:
            return space.rooms[key]
        for child in space.children.values():
            found = self._find_room_in_space(key, child)
            if found:
                return found
        return None

    def _find_parent_space_key(self, room_key: str) -> str | None:
        for community in self.config.communities:
            for sk, space in community.spaces.items():
                if room_key in space.rooms:
                    return sk
                found = self._find_parent_in_children(room_key, space)
                if found:
                    return found
        return None

    def _find_parent_in_children(self, room_key: str, space: SpaceConfig) -> str | None:
        for ck, child in space.children.items():
            if room_key in child.rooms:
                return ck
            found = self._find_parent_in_children(room_key, child)
            if found:
                return found
        return None

    def _find_parent_space_key_for_space(self, child_space_key: str) -> str | None:
        """Return the parent space key for a nested space, or None if top-level."""
        for community in self.config.communities:
            for sk, space in community.spaces.items():
                if child_space_key in space.children:
                    return sk
                found = self._find_parent_space_in_children(child_space_key, space)
                if found:
                    return found
        return None

    def _find_parent_space_in_children(
        self, child_space_key: str, space: SpaceConfig
    ) -> str | None:
        for ck, child in space.children.items():
            if child_space_key in child.children:
                return ck
            found = self._find_parent_space_in_children(child_space_key, child)
            if found:
                return found
        return None
