"""Matrix admin client for Knarr operational commands."""

import os
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx


class MatrixAdminClient:
    """Wraps the Matrix client-server API for admin/operational use.

    Handles token caching, room ID URL-encoding, and common operations
    that would otherwise require multi-line curl incantations. Usable
    from the CLI (src.admin.cli) or imported directly by services.
    """

    def __init__(
        self,
        homeserver: str,
        admin_user: str,
        admin_password: str,
        timeout: float = 30.0,
    ):
        self.homeserver = homeserver.rstrip("/")
        self.admin_user = admin_user
        self.admin_password = admin_password
        self._token: Optional[str] = None
        self._http = httpx.Client(timeout=timeout)

    def _api(self, path: str) -> str:
        return f"{self.homeserver}{path}"

    def _encode_room(self, room_id: str) -> str:
        return quote(room_id, safe="")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.get_token()}"}

    def _authed_request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make an authenticated request, retrying once on 401 (stale token).

        Merges caller-supplied headers (e.g. Content-Type) with the auth header.
        """
        merged_headers = {**self._headers(), **(kwargs.pop("headers", None) or {})}
        resp = self._http.request(method, self._api(path), headers=merged_headers, **kwargs)
        if resp.status_code == 401:
            self.invalidate_token()
            merged_headers = {**self._headers(), **(kwargs.pop("headers", None) or {})}
            resp = self._http.request(method, self._api(path), headers=merged_headers, **kwargs)
        resp.raise_for_status()
        return resp

    def get_token(self) -> str:
        """Login and return an access token, caching it for subsequent calls."""
        if self._token is not None:
            return self._token
        resp = self._http.post(
            self._api("/_matrix/client/v3/login"),
            json={
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": self.admin_user},
                "password": self.admin_password,
            },
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    def invalidate_token(self):
        """Clear the cached token, forcing a fresh login on next request."""
        self._token = None

    def create_room(
        self,
        name: str,
        topic: str = "",
        invite: Optional[list[str]] = None,
        private: bool = True,
        direct: bool = False,
    ) -> str:
        """Create a Matrix room and return its room ID."""
        body: dict = {
            "name": name,
            "preset": "private_chat" if private else "public_chat",
        }
        if topic:
            body["topic"] = topic
        if invite:
            body["invite"] = invite
        if direct:
            body["is_direct"] = True
        resp = self._authed_request("POST", "/_matrix/client/v3/createRoom", json=body)
        return resp.json()["room_id"]

    def invite(self, room_id: str, user_id: str) -> None:
        """Invite a user to a room."""
        self._authed_request(
            "POST",
            f"/_matrix/client/v3/rooms/{self._encode_room(room_id)}/invite",
            json={"user_id": user_id},
        )

    def send_message(self, room_id: str, body: str) -> str:
        """Send a text message to a room. Returns the event ID."""
        txn_id = uuid.uuid4().hex
        resp = self._authed_request(
            "PUT",
            f"/_matrix/client/v3/rooms/{self._encode_room(room_id)}"
            f"/send/m.room.message/{txn_id}",
            json={"msgtype": "m.text", "body": body},
        )
        return resp.json()["event_id"]

    def get_messages(
        self, room_id: str, limit: int = 10, direction: str = "b"
    ) -> list[dict]:
        """Fetch recent messages from a room. Returns newest-first by default."""
        resp = self._authed_request(
            "GET",
            f"/_matrix/client/v3/rooms/{self._encode_room(room_id)}/messages",
            params={"dir": direction, "limit": str(limit)},
        )
        return [
            e for e in resp.json().get("chunk", [])
            if e.get("type") == "m.room.message"
        ]

    def register_user(
        self,
        username: str,
        password: str,
        admin: bool = False,
        server_name: Optional[str] = None,
    ) -> str:
        """Register a new user via Synapse v2 admin API. Returns the user ID.

        Uses the bearer-token-authenticated endpoint (requires the calling
        user to be a Synapse admin). Derives server_name from KNARR_SERVER_NAME
        env var or the homeserver URL hostname if not provided.
        """
        if server_name is None:
            server_name = os.environ.get("KNARR_SERVER_NAME")
        if server_name is None:
            from urllib.parse import urlparse
            server_name = urlparse(self.homeserver).hostname or "localhost"
        user_id = f"@{username}:{server_name}"
        encoded = quote(user_id, safe="")
        resp = self._authed_request(
            "PUT",
            f"/_synapse/admin/v2/users/{encoded}",
            json={"password": password, "admin": admin},
        )
        return resp.json().get("name", user_id)

    def set_display_name(self, user_id: str, display_name: str) -> None:
        """Set a user's display name."""
        self._authed_request(
            "PUT",
            f"/_matrix/client/v3/profile/{quote(user_id, safe='')}/displayname",
            json={"displayname": display_name},
        )

    def set_avatar(self, user_id: str, avatar_path: str) -> None:
        """Upload an image and set it as a user's avatar."""
        path = Path(avatar_path)
        suffix = path.suffix.lower()
        content_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        content_type = content_types.get(suffix, "application/octet-stream")

        resp = self._authed_request(
            "POST",
            "/_matrix/media/v3/upload",
            params={"filename": path.name},
            content=path.read_bytes(),
            headers={"Content-Type": content_type},
        )
        mxc_uri = resp.json()["content_uri"]

        self._authed_request(
            "PUT",
            f"/_matrix/client/v3/profile/{quote(user_id, safe='')}/avatar_url",
            json={"avatar_url": mxc_uri},
        )

    def get_joined_rooms(self) -> list[str]:
        """List room IDs the authenticated user has joined."""
        resp = self._authed_request("GET", "/_matrix/client/v3/joined_rooms")
        return resp.json().get("joined_rooms", [])
