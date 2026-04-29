"""Bridge manager for Discord bridge operations via Matrix management DM."""

import os
import time

from .client import MatrixAdminClient

DEFAULT_BRIDGE_BOT = "@discordbot:knarr.local"
COMMAND_WAIT_SECONDS = 5


class BridgeManager:
    """Sends mautrix-discord commands via Matrix messages and reads responses."""

    def __init__(
        self,
        client: MatrixAdminClient,
        management_room: str,
        bridge_bot_user: str | None = None,
    ):
        self.client = client
        self.management_room = management_room
        self.bridge_bot_user = bridge_bot_user or os.environ.get(
            "KNARR_BRIDGE_BOT_USER", DEFAULT_BRIDGE_BOT
        )

    def _send_and_read(self, room_id: str, command: str, wait: float = COMMAND_WAIT_SECONDS) -> str:
        """Send a command to a room and return the bridge bot's first reply."""
        send_ts = time.time() * 1000
        event_id = self.client.send_message(room_id, command)
        time.sleep(wait)
        messages = self.client.get_messages(room_id, limit=25)
        # Collect only bridge bot responses that arrived after our command
        responses = []
        found_sentinel = False
        for msg in messages:
            if msg.get("event_id") == event_id:
                found_sentinel = True
                break
            msg_ts = msg.get("origin_server_ts", 0)
            if msg_ts < send_ts:
                break
            sender = msg.get("sender", "")
            body = msg.get("content", {}).get("body", "")
            if body and sender == self.bridge_bot_user:
                responses.append(body)
        if not found_sentinel and not responses:
            return "(no response from bridge)"
        # responses is newest-first; return the oldest (chronologically first reply)
        return responses[-1] if responses else "(no response from bridge)"

    def login_bot(self, discord_token: str) -> str:
        """Log the bridge into Discord using a bot token."""
        return self._send_and_read(
            self.management_room,
            f"login-token bot {discord_token}",
        )

    def ping(self) -> str:
        """Check bridge connection to Discord."""
        return self._send_and_read(self.management_room, "ping")

    def bridge_channel(
        self,
        room_id: str,
        channel_id: str,
        replace: bool = False,
    ) -> str:
        """Bridge a Discord channel to a Matrix room and set up the relay webhook."""
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
        invite: list[str] | None = None,
        topic: str = "",
        replace: bool = False,
    ) -> tuple[str, str]:
        """Create a room and bridge it to a Discord channel.

        Returns (room_id, bridge_result) so callers can inspect warnings.
        """
        all_invites = [self.bridge_bot_user]
        if invite:
            all_invites.extend(invite)

        room_id = self.client.create_room(
            name=room_name,
            topic=topic,
            invite=all_invites,
            private=True,
        )

        # Wait for the bridge bot to accept the room invite before sending commands
        time.sleep(2)
        bridge_result = self.bridge_channel(room_id, channel_id, replace=replace)
        return room_id, bridge_result

    def logout(self) -> str:
        """Disconnect the bridge from Discord."""
        return self._send_and_read(self.management_room, "logout")
