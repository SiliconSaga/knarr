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
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from src.publisher.schemas import ObjectRef


class StoreError(Exception):
    """The store could not be read or written."""


class ResultStore(Protocol):
    def record(self, request_id: str, publisher_id: str, verb: str,
               ref: ObjectRef, source: str | None = None) -> None: ...

    def get(self, request_id: str, publisher_id: str,
            verb: str) -> ObjectRef | None: ...

    def succeeded(self, request_id: str, publisher_id: str, verb: str) -> bool: ...


# Stored beside the ObjectRef fields. Entries written before it existed have
# no such key and still read back.
_SOURCE_FIELD = "source"


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
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            # Deliberately fatal. Reporting "nothing recorded" for an
            # unreadable store would make the fan-out repost everything it
            # had already sent — the exact failure the store exists to stop.
            raise StoreError(
                f"corrupt result store at {self._path}: {exc}. Refusing to "
                f"treat it as empty, which would republish."
            ) from exc
        if not isinstance(data, dict):
            raise StoreError(
                f"corrupt result store at {self._path}: root is "
                f"{type(data).__name__}, not an object. Refusing to treat it "
                f"as empty, which would republish."
            )
        return data

    def record(self, request_id: str, publisher_id: str, verb: str,
               ref: ObjectRef, source: str | None = None) -> None:
        data = self._load()
        entry = asdict(ref)
        if source is not None:
            entry[_SOURCE_FIELD] = source
        data[_key(request_id, publisher_id, verb)] = entry
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Write via a uniquely named temp file so an interrupted write cannot
        # truncate the store, and two writers never share one temp path.
        # This does not serialize writers; see docs/operations.md.
        fd, tmp = tempfile.mkstemp(dir=self._path.parent,
                                   prefix=self._path.name, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2))
        Path(tmp).replace(self._path)

    def get(self, request_id: str, publisher_id: str,
            verb: str) -> ObjectRef | None:
        data = self._load()
        key = _key(request_id, publisher_id, verb)
        if key not in data:
            return None
        # A present-but-unreadable entry is corruption, not absence: reading
        # it as "never sent" would republish.
        entry = data[key]
        try:
            if not isinstance(entry, dict):
                raise TypeError(f"entry is {type(entry).__name__}, not an object")
            return ObjectRef(**{k: v for k, v in entry.items()
                                if k != _SOURCE_FIELD})
        except TypeError as exc:
            raise StoreError(
                f"malformed entry {key!r} in {self._path}: {exc}"
            ) from exc

    def succeeded(self, request_id: str, publisher_id: str, verb: str) -> bool:
        return self.get(request_id, publisher_id, verb) is not None
