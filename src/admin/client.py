"""Matrix admin client for Knarr operational commands."""

import time
from typing import Optional
from urllib.parse import quote

import httpx


class MatrixAdminClient:
    """Wraps the Matrix client-server API for admin/operational use.

    Handles token caching, room ID URL-encoding, and common operations
    that would otherwise require multi-line curl incantations.
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
        self._token_time: float = 0
        self._http = httpx.Client(timeout=timeout)

    def _api(self, path: str) -> str:
        return f"{self.homeserver}{path}"

    def _encode_room(self, room_id: str) -> str:
        return quote(room_id, safe="")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.get_token()}"}

    def get_token(self) -> str:
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
        self._token_time = time.monotonic()
        return self._token

    def invalidate_token(self):
        self._token = None

    def create_room(
        self,
        name: str,
        topic: str = "",
        invite: Optional[list[str]] = None,
        private: bool = True,
        direct: bool = False,
    ) -> str:
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
        resp = self._http.post(
            self._api("/_matrix/client/v3/createRoom"),
            headers=self._headers(),
            json=body,
        )
        resp.raise_for_status()
        return resp.json()["room_id"]

    def invite(self, room_id: str, user_id: str) -> None:
        resp = self._http.post(
            self._api(f"/_matrix/client/v3/rooms/{self._encode_room(room_id)}/invite"),
            headers=self._headers(),
            json={"user_id": user_id},
        )
        resp.raise_for_status()

    def send_message(self, room_id: str, body: str) -> str:
        txn_id = str(int(time.time() * 1_000_000))
        resp = self._http.put(
            self._api(
                f"/_matrix/client/v3/rooms/{self._encode_room(room_id)}"
                f"/send/m.room.message/{txn_id}"
            ),
            headers=self._headers(),
            json={"msgtype": "m.text", "body": body},
        )
        resp.raise_for_status()
        return resp.json()["event_id"]

    def get_messages(
        self, room_id: str, limit: int = 10, direction: str = "b"
    ) -> list[dict]:
        resp = self._http.get(
            self._api(
                f"/_matrix/client/v3/rooms/{self._encode_room(room_id)}/messages"
            ),
            headers=self._headers(),
            params={"dir": direction, "limit": str(limit)},
        )
        resp.raise_for_status()
        return [
            e for e in resp.json().get("chunk", [])
            if e.get("type") == "m.room.message"
        ]

    def register_user(
        self, username: str, password: str, admin: bool = False
    ) -> str:
        resp = self._http.put(
            self._api("/_synapse/admin/v1/register"),
            headers=self._headers(),
            json={
                "nonce": self._get_register_nonce(),
                "username": username,
                "password": password,
                "admin": admin,
            },
        )
        if resp.status_code == 400 and "HMAC" in resp.text:
            return self._register_via_shared_secret(username, password, admin)
        resp.raise_for_status()
        return resp.json()["user_id"]

    def _get_register_nonce(self) -> str:
        resp = self._http.get(
            self._api("/_synapse/admin/v1/register"),
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()["nonce"]

    def _register_via_shared_secret(
        self, username: str, password: str, admin: bool
    ) -> str:
        import hashlib
        import hmac

        nonce = self._get_register_nonce()
        # The shared secret is in Synapse's config — we can't easily get it
        # from here. Fall back to the register_new_matrix_user CLI.
        raise NotImplementedError(
            "Shared-secret registration requires Synapse's registration_shared_secret. "
            "Use 'kubectl exec' with register_new_matrix_user instead."
        )

    def set_display_name(self, user_id: str, display_name: str) -> None:
        resp = self._http.put(
            self._api(f"/_matrix/client/v3/profile/{quote(user_id, safe='')}/displayname"),
            headers=self._headers(),
            json={"displayname": display_name},
        )
        resp.raise_for_status()

    def set_avatar(self, user_id: str, avatar_path: str) -> None:
        with open(avatar_path, "rb") as f:
            image_data = f.read()
        content_type = "image/png" if avatar_path.endswith(".png") else "image/jpeg"
        resp = self._http.post(
            self._api("/_matrix/media/v3/upload"),
            headers={**self._headers(), "Content-Type": content_type},
            params={"filename": avatar_path.split("/")[-1]},
            content=image_data,
        )
        resp.raise_for_status()
        mxc_uri = resp.json()["content_uri"]

        self._http.put(
            self._api(f"/_matrix/client/v3/profile/{quote(user_id, safe='')}/avatar_url"),
            headers=self._headers(),
            json={"avatar_url": mxc_uri},
        )

    def get_joined_rooms(self) -> list[dict]:
        resp = self._http.get(
            self._api("/_matrix/client/v3/joined_rooms"),
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json().get("joined_rooms", [])
