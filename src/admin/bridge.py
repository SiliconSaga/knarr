"""Bridge manager for Discord bridge operations via Matrix management DM."""

import time
from typing import Optional

from .client import MatrixAdminClient

BRIDGE_BOT_USER = "@discordbot:knarr.local"
COMMAND_WAIT_SECONDS = 5


class BridgeManager:
    """Sends mautrix-discord commands via Matrix messages and reads responses."""

    def __init__(self, client: MatrixAdminClient, management_room: str):
        self.client = client
        self.management_room = management_room

    def _send_and_read(self, room_id: str, command: str, wait: float = COMMAND_WAIT_SECONDS) -> str:
        self.client.send_message(room_id, command)
        time.sleep(wait)
        messages = self.client.get_messages(room_id, limit=5)
        responses = []
        for msg in messages:
            body = msg.get("content", {}).get("body", "")
            if body and body != command and not body.startswith("!discord") and not body.startswith("login-token"):
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
        result = self._send_and_read(room_id, cmd)

        relay_result = self._send_and_read(room_id, "!discord set-relay --create")

        return result

    def create_and_bridge(
        self,
        channel_id: str,
        room_name: str,
        invite: Optional[list[str]] = None,
        topic: str = "",
        replace: bool = False,
    ) -> str:
        all_invites = [BRIDGE_BOT_USER]
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
