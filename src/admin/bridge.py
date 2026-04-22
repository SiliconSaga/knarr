"""Bridge manager for Discord bridge operations via Matrix management DM."""

import os
import time
from typing import Optional

from .client import MatrixAdminClient

DEFAULT_BRIDGE_BOT = "@discordbot:knarr.local"
COMMAND_WAIT_SECONDS = 5


class BridgeManager:
    """Sends mautrix-discord commands via Matrix messages and reads responses."""

    def __init__(
        self,
        client: MatrixAdminClient,
        management_room: str,
        bridge_bot_user: Optional[str] = None,
    ):
        self.client = client
        self.management_room = management_room
        self.bridge_bot_user = bridge_bot_user or os.environ.get(
            "KNARR_BRIDGE_BOT_USER", DEFAULT_BRIDGE_BOT
        )

    def _send_and_read(self, room_id: str, command: str, wait: float = COMMAND_WAIT_SECONDS) -> str:
        event_id = self.client.send_message(room_id, command)
        time.sleep(wait)
        messages = self.client.get_messages(room_id, limit=10)
        # Find responses that came after our command (newer = first in backwards list)
        responses = []
        for msg in messages:
            if msg.get("event_id") == event_id:
                break
            body = msg.get("content", {}).get("body", "")
            sender = msg.get("sender", "")
            if body and sender != self.client.admin_user:
                responses.append(body)
        return responses[0] if responses else "(no response from bridge)"

    def login_bot(self, discord_token: str) -> str:
        return self._send_and_read(
            self.management_room,
            f"login-token bot {discord_token}",
        )

    def ping(self) -> str:
        return self._send_and_read(self.management_room, "ping")

    def bridge_channel(
        self,
        room_id: str,
        channel_id: str,
        replace: bool = False,
    ) -> str:
        cmd = f"!discord bridge {'--replace ' if replace else ''}{channel_id}"
        bridge_result = self._send_and_read(room_id, cmd)

        relay_result = self._send_and_read(room_id, "!discord set-relay --create")
        if "webhook" not in relay_result.lower() and "relay" not in relay_result.lower():
            return f"{bridge_result}\nWARNING: relay webhook may have failed: {relay_result}"

        return bridge_result

    def create_and_bridge(
        self,
        channel_id: str,
        room_name: str,
        invite: Optional[list[str]] = None,
        topic: str = "",
        replace: bool = False,
    ) -> str:
        all_invites = [self.bridge_bot_user]
        if invite:
            all_invites.extend(invite)

        room_id = self.client.create_room(
            name=room_name,
            topic=topic,
            invite=all_invites,
            private=True,
        )

        time.sleep(2)
        self.bridge_channel(room_id, channel_id, replace=replace)
        return room_id

    def logout(self) -> str:
        return self._send_and_read(self.management_room, "logout")
