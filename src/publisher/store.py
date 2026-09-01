"""Where PublishResult identity lives.

Kafka is a log, not a lookup, and these ids are the hinge of the engagement
half: a comment arriving on a Page post can only be tied back to "the fall
soccer announcement" because we recorded what the platform called it.

Phase A is file-backed behind a protocol. The design presumes Postgres via a
Mimir DataService eventually; that is a new implementation of this interface,
not a rewrite of its callers.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from src.publisher.schemas import ObjectRef


class StoreError(Exception):
    """The store could not be read or written."""


class ResultStore(Protocol):
    def record(self, request_id: str, publisher_id: str, verb: str,
               ref: ObjectRef) -> None: ...

    def get(self, request_id: str, publisher_id: str,
            verb: str) -> ObjectRef | None: ...

    def succeeded(self, request_id: str, publisher_id: str, verb: str) -> bool: ...


def _key(request_id: str, publisher_id: str, verb: str) -> str:
    return f"{request_id}|{publisher_id}|{verb}"


class JsonResultStore:
    """A JSON file keyed by (request_id, publisher_id, verb)."""

    def __init__(self, path: str | Path):
        self._path = Path(path)

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            # Deliberately fatal. Reporting "nothing recorded" for an
            # unreadable store would make the fan-out repost everything it
            # had already sent — the exact failure the store exists to stop.
            raise StoreError(
                f"corrupt result store at {self._path}: {exc}. Refusing to "
                f"treat it as empty, which would republish."
            ) from exc

    def record(self, request_id: str, publisher_id: str, verb: str,
               ref: ObjectRef) -> None:
        data = self._load()
        data[_key(request_id, publisher_id, verb)] = asdict(ref)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Write via a temp file so an interrupted write cannot truncate the
        # store into the corrupt state above.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, request_id: str, publisher_id: str,
            verb: str) -> ObjectRef | None:
        raw = self._load().get(_key(request_id, publisher_id, verb))
        return ObjectRef(**raw) if raw else None

    def succeeded(self, request_id: str, publisher_id: str, verb: str) -> bool:
        return self.get(request_id, publisher_id, verb) is not None
