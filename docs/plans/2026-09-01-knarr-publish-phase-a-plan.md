# Knarr Publish Phase A — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Knarr can publish one approved piece of content to several destinations at once, on destinations we already control, without reposting anything on a retry.

**Architecture:** The mirror of the inbound side. A `PublisherInstance` is `(platform, surface, access_path, scope, credentials_ref)`, exactly as `WatcherInstance` is for sources. Capabilities are *protocols a publisher implements* rather than a configured list, so a destination that cannot do a thing does not advertise it. A `PublishRequest` names targets by publisher id; the fan-out authorizes every target against the request's scope **before** resolving any credential, then publishes each independently and records the platform's own id. Retries are safe because `request_id` is derived from immutable input and per-target outcomes are persisted before the next target is attempted.

**Tech Stack:** Python 3.13, `click` (existing CLI), `matrix-nio` (already used by the router), `pytest`. No new runtime dependencies.

**Spec:** `docs/plans/2026-08-30-knarr-publish-design.md`

## Global Constraints

- **Workspace commit flow:** never `git add` / `git commit`. Every commit is `ws commit knarr .commits/<name>.md` with a bodyfile whose frontmatter lists the files. Bodyfile paths are relative to the **yggdrasil workspace root**; `add:` paths are relative to the **component root**.
- **Tests and lint:** `ws test knarr` and `ws lint knarr`. Both must pass before any commit.
- **Prose:** one line per paragraph and per bullet in Markdown. No hard wrapping.
- **Scope authority:** a target's scope comes from the resolved `PublisherInstance`, never from the request. A request may name destinations; it may not assert whose behalf it acts on.
- **No credential is resolved before authorization passes.** This ordering is a security property, not a style preference.
- **`validate()` is pure and local** — no network. Anything requiring a fetch belongs in `preflight()`.
- **Phase A ships no Meta adapters.** Facebook, Instagram and WhatsApp are Phase C and are blocked on developer-account verification.

---

## File Structure

**Create:**

| Path | Responsibility |
|---|---|
| `src/publisher/__init__.py` | Package marker |
| `src/publisher/schemas.py` | `ObjectRef`, `PublishResult`, `Violation`, `MediaRef`, `Target`, `PublishRequest` |
| `src/publisher/protocols.py` | `Publisher` base plus `Broadcaster` / `Commenter` / `Conversational` capability protocols |
| `src/publisher/registry.py` | Resolve publisher ids to instances; authorize scope and verb |
| `src/publisher/store.py` | `ResultStore` protocol + `JsonResultStore` |
| `src/publisher/fanout.py` | Idempotent per-target publish loop |
| `src/publisher/adapters/__init__.py` | Package marker |
| `src/publisher/adapters/matrix.py` | `MatrixPublisher` |
| `src/publisher/adapters/discord.py` | `DiscordPublisher` (via the Matrix bridge) |
| `tests/test_publisher_schemas.py` | Schema + `request_id` determinism |
| `tests/test_publisher_registry.py` | Authorization |
| `tests/test_publisher_store.py` | Persistence |
| `tests/test_publisher_fanout.py` | Idempotency and partial failure |
| `tests/test_publisher_matrix.py` | Matrix adapter |
| `tests/test_publisher_discord.py` | Discord adapter |

**Modify:**

| Path | Change |
|---|---|
| `src/admin/config_schema.py` | `PublisherConfig` dataclass, `publishers:` parsing, publisher validation |
| `src/admin/cli.py` | `knarr publish` command group |
| `tests/test_config_schema.py` | Publisher config parsing + validation cases |
| `config/knarr.yaml` | A `publishers:` block for the two Matrix-side destinations |
| `docs/operations.md` | Publishing section |

**Why a new `src/publisher/` package rather than extending `src/router/`:** the router *consumes* Kafka and writes to one room; this *is written to* and fans out to many. Sharing a module would mean one file holding both directions, and the existing `watchers/` + `router/` split is the pattern to follow.

---

## Task 1: Publish schemas

**Files:**
- Create: `components/knarr/src/publisher/__init__.py`
- Create: `components/knarr/src/publisher/schemas.py`
- Test: `components/knarr/tests/test_publisher_schemas.py`

**Interfaces:**
- Consumes: `src.watchers.schemas.Content` (reused verbatim — a thing you publish and a thing you watched have the same shape).
- Produces: `ObjectRef`, `PublishResult`, `Violation`, `MediaRef`, `Target`, `PublishRequest`, `derive_request_id(scope, targets, content_digest)`.

- [ ] **Step 1: Write the failing test**

Create `components/knarr/tests/test_publisher_schemas.py`:

```python
from src.publisher.schemas import (
    MediaRef,
    ObjectRef,
    PublishRequest,
    PublishResult,
    Target,
    Violation,
    derive_request_id,
)
from src.watchers.schemas import Content


def _content(body="hello"):
    return Content(type="post", title="T", body=body, author="knarr", attachments=[])


def _target(publisher_id="mtl-matrix", verb="post", body="hello"):
    return Target(publisher_id=publisher_id, verb=verb, content=_content(body), parent=None)


def test_object_ref_is_composite():
    """A bare platform id cannot select an adapter or its credentials later."""
    ref = ObjectRef(
        publisher_id="mtl-matrix", platform="matrix", surface="room",
        object_type="post", object_id="$abc",
    )
    assert ref.publisher_id == "mtl-matrix"
    assert ref.object_id == "$abc"


def test_request_id_is_stable_for_identical_input():
    a = derive_request_id("community/mtl", [_target()], "digest")
    b = derive_request_id("community/mtl", [_target()], "digest")
    assert a == b


def test_request_id_changes_with_content():
    a = derive_request_id("community/mtl", [_target(body="one")], "d1")
    b = derive_request_id("community/mtl", [_target(body="two")], "d2")
    assert a != b


def test_request_id_ignores_target_order():
    """Target order is not meaningful, so it must not change identity.

    Otherwise a retry that resolved targets in a different order would look
    like a different request and republish everything.
    """
    t1, t2 = _target("a"), _target("b")
    assert derive_request_id("s", [t1, t2], "d") == derive_request_id("s", [t2, t1], "d")


def test_request_id_changes_with_scope():
    assert derive_request_id("community/mtl", [_target()], "d") != \
        derive_request_id("group/pta", [_target()], "d")


def test_violation_carries_the_target_it_belongs_to():
    v = Violation(publisher_id="mtl-instagram", reason="media required")
    assert "media" in v.reason


def test_media_ref_round_trips():
    m = MediaRef(kind="public-url", locator="https://x/y.jpg",
                 content_type="image/jpeg", checksum="abc")
    assert m.kind == "public-url"


def test_publish_result_records_the_object_ref():
    ref = ObjectRef(publisher_id="p", platform="matrix", surface="room",
                    object_type="post", object_id="$1")
    assert PublishResult(ok=True, ref=ref, error=None).ref.object_id == "$1"


def test_publish_request_holds_targets():
    req = PublishRequest(
        request_id="r1", scope="community/mtl", targets=[_target()],
        origin="cli", approved_by="cervator", approved_at="2026-09-01T00:00:00Z",
    )
    assert req.targets[0].publisher_id == "mtl-matrix"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ws test knarr tests/test_publisher_schemas.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.publisher'`

- [ ] **Step 3: Write minimal implementation**

Create `components/knarr/src/publisher/__init__.py` (empty file).

Create `components/knarr/src/publisher/schemas.py`:

```python
"""Data types for the outbound publish path.

Mirrors src/watchers/schemas.py on the inbound side. Content is reused
verbatim rather than redefined: a thing you publish and a thing you watched
have the same shape, and duplicating it would guarantee they drift.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from src.watchers.schemas import Content


@dataclass(frozen=True)
class ObjectRef:
    """What a platform called something we created.

    Composite on purpose. A bare object id cannot select the adapter or the
    credentials a later reply needs, and two Pages can produce
    indistinguishable comment ids.
    """
    publisher_id: str
    platform: str
    surface: str
    object_type: str      # post | comment | message | media
    object_id: str


@dataclass
class PublishResult:
    ok: bool
    ref: ObjectRef | None
    error: str | None


@dataclass
class Violation:
    """A reason a target cannot be published as written."""
    publisher_id: str
    reason: str


@dataclass
class MediaRef:
    """A durable reference, NOT a fetchable URL.

    A signed URL written into a replayable record is dead by the time the
    record is replayed. Publishers mint a URL per delivery attempt from this.
    """
    kind: str             # garage-object | public-url
    locator: str
    content_type: str
    checksum: str = ""


@dataclass
class Target:
    publisher_id: str
    verb: str                       # post | comment | message
    content: Content
    parent: ObjectRef | None = None  # required for comment/message
    media: MediaRef | None = None


@dataclass
class PublishRequest:
    request_id: str
    scope: str
    targets: list[Target]
    origin: str                     # git-merge | cli | matrix
    approved_by: str
    approved_at: str
    media: MediaRef | None = None


def content_digest(content: Content) -> str:
    """Stable digest of a Content payload."""
    blob = json.dumps(asdict(content), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def derive_request_id(scope: str, targets: list[Target], digest: str) -> str:
    """Deterministic id derived from immutable input.

    Never generated fresh on retry: the whole point is that the same input
    yields the same id, so a retry can look up what already succeeded instead
    of republishing it. Target order is sorted out because order carries no
    meaning — leaving it in would make a re-resolved request look new.
    """
    parts = sorted(f"{t.publisher_id}:{t.verb}" for t in targets)
    blob = json.dumps(
        {"scope": scope, "targets": parts, "digest": digest},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ws test knarr tests/test_publisher_schemas.py`
Expected: PASS (9 tests)

- [ ] **Step 5: Lint**

Run: `ws lint knarr`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

Write `.commits/publish-schemas.md` at the workspace root:

```markdown
---
message: "feat(publisher): data types for the outbound path"
add:
  - src/publisher/__init__.py
  - src/publisher/schemas.py
  - tests/test_publisher_schemas.py
---

Mirrors src/watchers/schemas.py for the write direction. `Content` is reused rather than redefined — a thing you publish and a thing you watched have the same shape, and a second copy would drift.

`ObjectRef` is composite because a bare platform id cannot select the adapter or credentials a later reply needs. `derive_request_id` sorts targets before hashing: order carries no meaning, so leaving it in would make a re-resolved request look new and republish everything it had already sent.
```

Run: `ws commit knarr .commits/publish-schemas.md`

---

## Task 2: Capability protocols

**Files:**
- Create: `components/knarr/src/publisher/protocols.py`
- Test: covered by the adapter tests in Tasks 8 and 9; this task has a small direct test.

**Interfaces:**
- Consumes: `ObjectRef`, `PublishResult`, `Violation`, `MediaRef`, `Content` from Task 1.
- Produces: `Publisher`, `Broadcaster`, `Commenter`, `Conversational`, `Constraints`, `capabilities_of(publisher) -> frozenset[str]`.

- [ ] **Step 1: Write the failing test**

Create `components/knarr/tests/test_publisher_protocols.py`:

```python
from src.publisher.protocols import Constraints, capabilities_of
from src.publisher.schemas import ObjectRef, PublishResult


class _BroadcastOnly:
    id = "b"
    scope = "community/x"
    platform = "fake"
    surface = "feed"
    constraints = Constraints()

    async def post(self, content, media=None):
        return PublishResult(ok=True, ref=None, error=None)

    def validate(self, verb, content, media=None):
        return []


class _ConversationalOnly:
    id = "c"
    scope = "community/x"
    platform = "fake"
    surface = "dm"
    constraints = Constraints()

    async def message(self, thread, content, media=None):
        return PublishResult(ok=True, ref=None, error=None)

    def validate(self, verb, content, media=None):
        return []


def test_capabilities_are_derived_from_implemented_methods():
    """Derived, never configured — a YAML list would drift from the code."""
    assert capabilities_of(_BroadcastOnly()) == frozenset({"post"})
    assert capabilities_of(_ConversationalOnly()) == frozenset({"message"})


def test_a_publisher_does_not_advertise_what_it_cannot_do():
    assert "message" not in capabilities_of(_BroadcastOnly())
    assert "post" not in capabilities_of(_ConversationalOnly())


def test_constraints_default_to_permissive():
    c = Constraints()
    assert c.media_required is False
    assert c.media_formats == ()
    assert c.max_length is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ws test knarr tests/test_publisher_protocols.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.publisher.protocols'`

- [ ] **Step 3: Write minimal implementation**

Create `components/knarr/src/publisher/protocols.py`:

```python
"""Capability protocols for outbound destinations.

One interface with every verb on it would promise that every destination can
do everything, and a `capabilities:` list in YAML would drift from the code
the first time an adapter changed. So a publisher implements only the
protocols its surface supports, and its capabilities are READ OFF the class.

The verbs are deliberately three, not two. Responding to a feed comment and
sending into a conversation obey different rules — the second has delivery
windows — and one word for both would hide exactly the constraint that
matters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from src.publisher.schemas import Content, MediaRef, ObjectRef, PublishResult, Violation


@dataclass
class Constraints:
    """What a destination will refuse, expressed so validate() can check it."""
    media_required: bool = False
    media_formats: tuple[str, ...] = ()      # empty means "any"
    max_length: int | None = None
    send_window_hours: int | None = None     # None means "no window"


class Publisher(Protocol):
    """Every publisher, regardless of what it can do."""
    id: str
    scope: str
    platform: str
    surface: str
    constraints: Constraints

    def validate(
        self, verb: str, content: Content, media: MediaRef | None = None
    ) -> list[Violation]:
        """Local, pure, no network. Fetchability belongs in preflight()."""
        ...


@runtime_checkable
class Broadcaster(Protocol):
    async def post(
        self, content: Content, media: MediaRef | None = None
    ) -> PublishResult: ...


@runtime_checkable
class Commenter(Protocol):
    async def comment(
        self, parent: ObjectRef, content: Content, media: MediaRef | None = None
    ) -> PublishResult: ...


@runtime_checkable
class Conversational(Protocol):
    async def message(
        self, thread: ObjectRef, content: Content, media: MediaRef | None = None
    ) -> PublishResult: ...


_VERB_METHODS = {
    "post": "post",
    "comment": "comment",
    "message": "message",
}


def capabilities_of(publisher: object) -> frozenset[str]:
    """Which verbs this publisher actually implements.

    Derived from the object rather than declared beside it, so the answer
    cannot disagree with the code.
    """
    return frozenset(
        verb for verb, method in _VERB_METHODS.items()
        if callable(getattr(publisher, method, None))
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ws test knarr tests/test_publisher_protocols.py`
Expected: PASS (3 tests)

- [ ] **Step 5: Lint**

Run: `ws lint knarr`

- [ ] **Step 6: Commit**

Write `.commits/publish-protocols.md`:

```markdown
---
message: "feat(publisher): capability protocols instead of one fat interface"
add:
  - src/publisher/protocols.py
  - tests/test_publisher_protocols.py
---

A single interface carrying every verb promises that every destination can do everything, which is false the moment Instagram (no conversations) and WhatsApp (no feed) are both in scope. Publishers now implement only the protocols their surface supports.

Capabilities are read off the class rather than declared in YAML, so the advertised set cannot disagree with the code. Three verbs rather than two: responding to a feed comment and sending into a conversation obey different rules — the second has delivery windows — and collapsing them would hide the constraint that matters most.
```

Run: `ws commit knarr .commits/publish-protocols.md`

---

## Task 3: Publisher config parsing

**Files:**
- Modify: `components/knarr/src/admin/config_schema.py`
- Test: `components/knarr/tests/test_config_schema.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (config layer is independent of the runtime types).
- Produces: `PublisherConfig` dataclass with fields `id, platform, surface, access_path, scope, credentials_ref`; `KnarrConfig.publishers: list[PublisherConfig]`.

- [ ] **Step 1: Write the failing test**

Append to `components/knarr/tests/test_config_schema.py`:

```python
VALID_PUBLISHER = {
    "id": "mtl-matrix",
    "platform": "matrix",
    "surface": "room",
    "access_path": "api",
    "scope": "community/mtl",
    "target": "#social-watch:knarr.local",
}


def test_parses_publishers_block():
    cfg = {**VALID_INDEX, "publishers": [VALID_PUBLISHER]}
    parsed = KnarrConfig.from_dict(cfg, community_loader=lambda _: VALID_COMMUNITY)
    assert len(parsed.publishers) == 1
    pub = parsed.publishers[0]
    assert pub.id == "mtl-matrix"
    assert pub.surface == "room"
    assert pub.credentials_ref is None


def test_publishers_defaults_to_empty():
    parsed = KnarrConfig.from_dict(VALID_INDEX, community_loader=lambda _: VALID_COMMUNITY)
    assert parsed.publishers == []


def test_parse_rejects_non_list_publishers():
    bad = {**VALID_INDEX, "publishers": "nope"}
    with pytest.raises(ConfigError, match="publishers must be a list"):
        KnarrConfig.from_dict(bad, community_loader=lambda _: VALID_COMMUNITY)


def test_parse_rejects_publisher_missing_required_field():
    bad = {**VALID_INDEX, "publishers": [{"id": "x", "platform": "matrix"}]}
    with pytest.raises(ConfigError, match="missing required field"):
        KnarrConfig.from_dict(bad, community_loader=lambda _: VALID_COMMUNITY)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ws test knarr tests/test_config_schema.py -k publisher`
Expected: FAIL — `AttributeError: 'KnarrConfig' object has no attribute 'publishers'`

- [ ] **Step 3: Write minimal implementation**

In `components/knarr/src/admin/config_schema.py`, add after the `InstanceConfig` class:

```python
@dataclass
class PublisherConfig:
    """A single PublisherInstance: one (platform, surface, scope) destination.

    The mirror of InstanceConfig. `surface` exists because a Facebook Page is
    two destinations — a broadcast feed and a conversational inbox — that share
    one credential but have different capabilities. Modelling them as one
    entry is what made Facebook look uniformly difficult.
    """
    id: str
    platform: str
    surface: str
    access_path: str
    scope: str
    target: str                          # room id/alias, channel id, page id
    credentials_ref: dict | None = None

    @classmethod
    def from_dict(cls, data: dict) -> PublisherConfig:
        required = ("id", "platform", "surface", "access_path", "scope", "target")
        for key in required:
            if key not in data:
                raise ConfigError(f"publisher is missing required field: {key}")
        pid = data["id"]
        return cls(
            id=_require_str(pid, "publisher.id"),
            platform=_require_str(data["platform"], f"publisher.{pid}.platform"),
            surface=_require_str(data["surface"], f"publisher.{pid}.surface"),
            access_path=_require_str(data["access_path"], f"publisher.{pid}.access_path"),
            scope=_require_str(data["scope"], f"publisher.{pid}.scope"),
            target=_require_str(data["target"], f"publisher.{pid}.target"),
            credentials_ref=data.get("credentials_ref"),
        )
```

In the `KnarrConfig` dataclass, add the field:

```python
    publishers: list[PublisherConfig] = field(default_factory=list)
```

In `KnarrConfig.from_dict`, after the instances parsing, add:

```python
        publishers_raw = data.get("publishers", [])
        if not isinstance(publishers_raw, list):
            raise ConfigError("publishers must be a list")
        publishers = [PublisherConfig.from_dict(p) for p in publishers_raw]
```

and pass `publishers=publishers` into the returned `cls(...)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `ws test knarr`
Expected: PASS — all existing tests plus the 4 new ones.

- [ ] **Step 5: Lint**

Run: `ws lint knarr`

- [ ] **Step 6: Commit**

Write `.commits/publisher-config-parsing.md`:

```markdown
---
message: "feat(config): parse a publishers block"
add:
  - src/admin/config_schema.py
  - tests/test_config_schema.py
---

`PublisherConfig` mirrors `InstanceConfig` for the write direction. `surface` is the one field with no inbound equivalent: a Facebook Page is two destinations sharing one credential — a broadcast feed and a conversational inbox — and modelling them as a single entry is what made Facebook look uniformly hard.

Absent `publishers:` parses to an empty list, so existing configs are unaffected.
```

Run: `ws commit knarr .commits/publisher-config-parsing.md`

---

## Task 4: Publisher config validation

**Files:**
- Modify: `components/knarr/src/admin/config_schema.py`
- Test: `components/knarr/tests/test_config_schema.py`

**Interfaces:**
- Consumes: `PublisherConfig` from Task 3.
- Produces: publisher checks inside the existing `validate_config`; `_SUPPORTED_PUBLISHERS` frozenset of `(platform, surface)`.

- [ ] **Step 1: Write the failing test**

Append to `components/knarr/tests/test_config_schema.py`:

```python
def _publisher(**overrides):
    base = dict(VALID_PUBLISHER)
    base.update(overrides)
    return {**VALID_INDEX, "publishers": [base]}


def test_validate_rejects_publisher_with_bad_scope():
    _expect_config_error(_publisher(scope="garbage/mtl"), "scope")


def test_validate_rejects_publisher_without_an_adapter():
    _expect_config_error(
        _publisher(platform="facebook", surface="page-feed"),
        "no publisher adapter for facebook/page-feed",
    )


def test_validate_rejects_duplicate_publisher_ids():
    cfg = {**VALID_INDEX, "publishers": [VALID_PUBLISHER, VALID_PUBLISHER]}
    _expect_config_error(cfg, "duplicate publisher id")


def test_validate_rejects_publisher_credentials_ref_missing_key():
    _expect_config_error(
        _publisher(credentials_ref={"secret_name": "s"}),
        "credentials_ref.secret_key",
    )


def test_validate_accepts_a_good_publisher():
    parsed = KnarrConfig.from_dict(
        _publisher(), community_loader=lambda _: VALID_COMMUNITY,
    )
    validate_config(parsed)  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ws test knarr tests/test_config_schema.py -k publisher`
Expected: FAIL — `DID NOT RAISE` on the rejection cases.

- [ ] **Step 3: Write minimal implementation**

In `components/knarr/src/admin/config_schema.py`, add beside `_SUPPORTED_ADAPTERS`:

```python
# (platform, surface) pairs that src/publisher has an adapter for. Phase A is
# deliberately the two destinations we already run; Meta surfaces land in
# Phase C, gated on developer-account verification.
_SUPPORTED_PUBLISHERS = frozenset({
    ("matrix", "room"),
    ("discord", "channel"),
})
```

In `validate_config`, after the instances loop, add:

```python
    seen_publisher_ids: set[str] = set()
    for pub in config.publishers:
        if pub.id in seen_publisher_ids:
            errors.append(f"duplicate publisher id: {pub.id}")
        seen_publisher_ids.add(pub.id)

        if not any(pub.scope.startswith(p) for p in _VALID_SCOPE_PREFIXES):
            errors.append(
                f"Publisher '{pub.id}': scope '{pub.scope}' must start with "
                f"one of {_VALID_SCOPE_PREFIXES}"
            )

        if (pub.platform, pub.surface) not in _SUPPORTED_PUBLISHERS:
            supported = ", ".join(
                f"{p}/{s}" for p, s in sorted(_SUPPORTED_PUBLISHERS)
            )
            errors.append(
                f"Publisher '{pub.id}': no publisher adapter for "
                f"{pub.platform}/{pub.surface} (supported: {supported})"
            )

        if pub.credentials_ref is not None:
            for key in ("secret_name", "secret_key"):
                value = pub.credentials_ref.get(key)
                if not isinstance(value, str) or not value:
                    errors.append(
                        f"Publisher '{pub.id}': credentials_ref.{key} is "
                        f"required and must be a non-empty string"
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ws test knarr`

- [ ] **Step 5: Lint**

Run: `ws lint knarr`

- [ ] **Step 6: Commit**

Write `.commits/publisher-config-validation.md`:

```markdown
---
message: "feat(config): validate publishers before anything tries to use them"
add:
  - src/admin/config_schema.py
  - tests/test_config_schema.py
---

Same posture as the instance validation: a destination that no adapter can serve, a scope outside the canonical prefixes, or a half-filled credentials_ref is rejected with the publisher named, rather than failing later somewhere that does not know which entry was wrong.

Duplicate ids are rejected outright — the fan-out resolves targets by id, so two entries sharing one would make which credential gets used a matter of dict ordering.
```

Run: `ws commit knarr .commits/publisher-config-validation.md`

---

## Task 5: The registry — resolution and authorization

**Files:**
- Create: `components/knarr/src/publisher/registry.py`
- Test: `components/knarr/tests/test_publisher_registry.py`

**Interfaces:**
- Consumes: `PublisherConfig` (Task 3), `capabilities_of` (Task 2), `Target` (Task 1).
- Produces: `PublisherRegistry(publishers: list[Publisher])` with `resolve(target, scope) -> Publisher` and `AuthorizationError`.

- [ ] **Step 1: Write the failing test**

Create `components/knarr/tests/test_publisher_registry.py`:

```python
import pytest

from src.publisher.protocols import Constraints
from src.publisher.registry import AuthorizationError, PublisherRegistry
from src.publisher.schemas import PublishResult, Target
from src.watchers.schemas import Content


class _FakePublisher:
    def __init__(self, pid, scope, verbs=("post",)):
        self.id = pid
        self.scope = scope
        self.platform = "fake"
        self.surface = "feed"
        self.constraints = Constraints()
        self.credentials_resolved = False
        if "post" in verbs:
            self.post = self._post
        if "message" in verbs:
            self.message = self._message

    async def _post(self, content, media=None):
        return PublishResult(ok=True, ref=None, error=None)

    async def _message(self, thread, content, media=None):
        return PublishResult(ok=True, ref=None, error=None)

    def validate(self, verb, content, media=None):
        return []

    def resolve_credentials(self):
        self.credentials_resolved = True


def _target(pid="p1", verb="post"):
    return Target(
        publisher_id=pid, verb=verb,
        content=Content(type="post", title="", body="b", author="a", attachments=[]),
    )


def test_resolves_a_known_publisher():
    reg = PublisherRegistry([_FakePublisher("p1", "community/mtl")])
    assert reg.resolve(_target(), "community/mtl").id == "p1"


def test_rejects_unknown_publisher_id():
    reg = PublisherRegistry([_FakePublisher("p1", "community/mtl")])
    with pytest.raises(AuthorizationError, match="unknown publisher"):
        reg.resolve(_target("nope"), "community/mtl")


def test_rejects_cross_scope_target():
    """The worst failure this system could have: one community's content
    published through another's credentials."""
    reg = PublisherRegistry([_FakePublisher("p1", "community/mtl")])
    with pytest.raises(AuthorizationError, match="scope"):
        reg.resolve(_target(), "group/pta")


def test_rejects_a_verb_the_publisher_cannot_do():
    reg = PublisherRegistry([_FakePublisher("p1", "community/mtl", verbs=("post",))])
    with pytest.raises(AuthorizationError, match="cannot message"):
        reg.resolve(_target(verb="message"), "community/mtl")


def test_credentials_are_not_resolved_before_authorization_fails():
    """Ordering is a security property: a rejected target must never have
    reached a secret."""
    pub = _FakePublisher("p1", "community/mtl")
    reg = PublisherRegistry([pub])
    with pytest.raises(AuthorizationError):
        reg.resolve(_target(), "group/pta")
    assert pub.credentials_resolved is False


def test_authorize_all_rejects_the_whole_request_on_one_bad_target():
    """A request naming a destination it has no business touching is not
    partially trustworthy."""
    reg = PublisherRegistry([_FakePublisher("p1", "community/mtl")])
    with pytest.raises(AuthorizationError):
        reg.authorize_all([_target("p1"), _target("nope")], "community/mtl")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ws test knarr tests/test_publisher_registry.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.publisher.registry'`

- [ ] **Step 3: Write minimal implementation**

Create `components/knarr/src/publisher/registry.py`:

```python
"""Resolve publisher ids to instances, and authorize before anything else.

The ordering here is the point. A PublishRequest names targets by id; nothing
in the id says whose behalf it acts on. Checking the RESOLVED instance's scope
against the request's — before a credential is touched — is what stops a typo
or a compromised manifest publishing one community's content through
another's Page.
"""

from __future__ import annotations

from src.publisher.protocols import capabilities_of
from src.publisher.schemas import Target


class AuthorizationError(Exception):
    """A target is unknown, out of scope, or asks for a verb it cannot do."""


class PublisherRegistry:
    def __init__(self, publishers: list):
        self._by_id = {p.id: p for p in publishers}

    def resolve(self, target: Target, request_scope: str):
        """Resolve and authorize one target. Raises rather than returning None."""
        publisher = self._by_id.get(target.publisher_id)
        if publisher is None:
            raise AuthorizationError(
                f"unknown publisher '{target.publisher_id}' "
                f"(known: {sorted(self._by_id)})"
            )

        # Scope is taken from the RESOLVED instance, never from the request.
        if publisher.scope != request_scope:
            raise AuthorizationError(
                f"scope mismatch: request is '{request_scope}' but publisher "
                f"'{publisher.id}' serves '{publisher.scope}'. Refusing — this "
                f"is a bug or an attack, never a routine condition."
            )

        if target.verb not in capabilities_of(publisher):
            raise AuthorizationError(
                f"'{publisher.id}' cannot {target.verb}; it can "
                f"{sorted(capabilities_of(publisher)) or 'nothing'}"
            )

        return publisher

    def authorize_all(self, targets: list[Target], request_scope: str) -> list:
        """Authorize every target, or reject the whole request.

        Whole-request rather than per-target on purpose: a request naming a
        destination it has no business touching is not partially trustworthy.
        """
        return [self.resolve(t, request_scope) for t in targets]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ws test knarr tests/test_publisher_registry.py`
Expected: PASS (6 tests)

- [ ] **Step 5: Lint**

Run: `ws lint knarr`

- [ ] **Step 6: Commit**

Write `.commits/publisher-registry.md`:

```markdown
---
message: "feat(publisher): authorize every target before resolving a credential"
add:
  - src/publisher/registry.py
  - tests/test_publisher_registry.py
---

A request names targets by id, and nothing in an id says whose behalf it acts on. Scope is therefore taken from the resolved instance and compared with the request's — before any credential is touched. A typo or a careless site repo must not be able to publish one community's content through another's Page, and that is the worst failure this system could have.

Authorization is whole-request: one bad target rejects everything. A request naming a destination it has no business touching is not partially trustworthy.
```

Run: `ws commit knarr .commits/publisher-registry.md`

---

## Task 6: Result store

**Files:**
- Create: `components/knarr/src/publisher/store.py`
- Test: `components/knarr/tests/test_publisher_store.py`

**Interfaces:**
- Consumes: `ObjectRef` (Task 1).
- Produces: `ResultStore` protocol; `JsonResultStore(path)` with `record(request_id, publisher_id, verb, ref)`, `get(request_id, publisher_id, verb) -> ObjectRef | None`, `succeeded(request_id, publisher_id, verb) -> bool`.

**Note for the implementer:** the design's open question 1 presumes Postgres via a Mimir `DataService` eventually. Phase A ships a file-backed store behind a protocol so that swap is a new class, not a rewrite. Do not add a database here.

- [ ] **Step 1: Write the failing test**

Create `components/knarr/tests/test_publisher_store.py`:

```python
from src.publisher.schemas import ObjectRef
from src.publisher.store import JsonResultStore


def _ref(object_id="$1"):
    return ObjectRef(publisher_id="p1", platform="matrix", surface="room",
                     object_type="post", object_id=object_id)


def test_records_and_reads_back(tmp_path):
    store = JsonResultStore(tmp_path / "results.json")
    store.record("r1", "p1", "post", _ref())
    assert store.get("r1", "p1", "post").object_id == "$1"


def test_unknown_key_returns_none(tmp_path):
    store = JsonResultStore(tmp_path / "results.json")
    assert store.get("r1", "p1", "post") is None
    assert store.succeeded("r1", "p1", "post") is False


def test_survives_a_new_process(tmp_path):
    """Persistence is the whole point — an in-memory store would make a
    retry after a crash republish everything."""
    path = tmp_path / "results.json"
    JsonResultStore(path).record("r1", "p1", "post", _ref("$abc"))
    assert JsonResultStore(path).get("r1", "p1", "post").object_id == "$abc"


def test_keys_are_composite(tmp_path):
    """Same request, different target — must not collide."""
    store = JsonResultStore(tmp_path / "results.json")
    store.record("r1", "p1", "post", _ref("$one"))
    store.record("r1", "p2", "post", _ref("$two"))
    assert store.get("r1", "p1", "post").object_id == "$one"
    assert store.get("r1", "p2", "post").object_id == "$two"


def test_same_publisher_different_verb_does_not_collide(tmp_path):
    store = JsonResultStore(tmp_path / "results.json")
    store.record("r1", "p1", "post", _ref("$post"))
    store.record("r1", "p1", "comment", _ref("$comment"))
    assert store.get("r1", "p1", "post").object_id == "$post"
    assert store.get("r1", "p1", "comment").object_id == "$comment"


def test_corrupt_store_file_does_not_crash(tmp_path):
    """A store that cannot be read must not take the publish down — but it
    must also not silently report 'nothing published', which would repost."""
    path = tmp_path / "results.json"
    path.write_text("{ not json")
    store = JsonResultStore(path)
    try:
        store.get("r1", "p1", "post")
    except Exception as exc:  # noqa: BLE001
        assert "corrupt" in str(exc).lower()
    else:
        raise AssertionError("a corrupt store must raise, not report empty")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ws test knarr tests/test_publisher_store.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.publisher.store'`

- [ ] **Step 3: Write minimal implementation**

Create `components/knarr/src/publisher/store.py`:

```python
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

    def record(self, request_id, publisher_id, verb, ref: ObjectRef) -> None:
        data = self._load()
        data[_key(request_id, publisher_id, verb)] = asdict(ref)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Write via a temp file so an interrupted write cannot truncate the
        # store into the corrupt state above.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, request_id, publisher_id, verb) -> ObjectRef | None:
        raw = self._load().get(_key(request_id, publisher_id, verb))
        return ObjectRef(**raw) if raw else None

    def succeeded(self, request_id, publisher_id, verb) -> bool:
        return self.get(request_id, publisher_id, verb) is not None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ws test knarr tests/test_publisher_store.py`
Expected: PASS (6 tests)

- [ ] **Step 5: Lint**

Run: `ws lint knarr`

- [ ] **Step 6: Commit**

Write `.commits/publisher-store.md`:

```markdown
---
message: "feat(publisher): persist what the platform called each post"
add:
  - src/publisher/store.py
  - tests/test_publisher_store.py
---

Kafka is a log, not a lookup, and these ids are the hinge of the engagement half — a comment on a Page post is only connectable to the announcement it belongs to because we recorded the platform's own id.

File-backed behind a protocol, because the design's open question about Postgres via a Mimir DataService is genuinely open; swapping it later is a new implementation, not a rewrite of the callers.

A corrupt store raises rather than reporting empty. Reporting empty would make the fan-out repost everything it had already sent, which is precisely what the store exists to prevent. Writes go through a temp file so an interrupted write cannot create that state.
```

Run: `ws commit knarr .commits/publisher-store.md`

---

## Task 7: Idempotent fan-out

**Files:**
- Create: `components/knarr/src/publisher/fanout.py`
- Test: `components/knarr/tests/test_publisher_fanout.py`

**Interfaces:**
- Consumes: `PublisherRegistry` (Task 5), `ResultStore` (Task 6), `PublishRequest`/`Target`/`PublishResult`/`Violation` (Task 1), `capabilities_of` (Task 2).
- Produces: `async fan_out(request, registry, store, dry_run=False) -> FanOutReport` with `FanOutReport(published, skipped, failed, violations)`.

- [ ] **Step 1: Write the failing test**

Create `components/knarr/tests/test_publisher_fanout.py`:

```python
import pytest

from src.publisher.fanout import fan_out
from src.publisher.protocols import Constraints
from src.publisher.registry import PublisherRegistry
from src.publisher.schemas import ObjectRef, PublishRequest, PublishResult, Target, Violation
from src.publisher.store import JsonResultStore
from src.watchers.schemas import Content


class _Pub:
    def __init__(self, pid, scope="community/mtl", fail=False, violations=()):
        self.id = pid
        self.scope = scope
        self.platform = "fake"
        self.surface = "feed"
        self.constraints = Constraints()
        self.calls = 0
        self._fail = fail
        self._violations = list(violations)

    async def post(self, content, media=None):
        self.calls += 1
        if self._fail:
            return PublishResult(ok=False, ref=None, error="boom")
        return PublishResult(
            ok=True,
            ref=ObjectRef(publisher_id=self.id, platform="fake", surface="feed",
                          object_type="post", object_id=f"${self.id}"),
            error=None,
        )

    def validate(self, verb, content, media=None):
        return list(self._violations)


def _request(targets, rid="r1"):
    return PublishRequest(
        request_id=rid, scope="community/mtl", targets=targets,
        origin="cli", approved_by="cervator", approved_at="2026-09-01T00:00:00Z",
    )


def _target(pid):
    return Target(
        publisher_id=pid, verb="post",
        content=Content(type="post", title="", body="b", author="a", attachments=[]),
    )


@pytest.mark.asyncio
async def test_publishes_every_target(tmp_path):
    a, b = _Pub("a"), _Pub("b")
    report = await fan_out(
        _request([_target("a"), _target("b")]),
        PublisherRegistry([a, b]),
        JsonResultStore(tmp_path / "s.json"),
    )
    assert report.published == 2
    assert a.calls == 1 and b.calls == 1


@pytest.mark.asyncio
async def test_rerun_reposts_nothing(tmp_path):
    """The Phase A exit criterion. A retry must skip what already landed."""
    a, b = _Pub("a"), _Pub("b")
    store = JsonResultStore(tmp_path / "s.json")
    req = _request([_target("a"), _target("b")])

    await fan_out(req, PublisherRegistry([a, b]), store)
    second = await fan_out(req, PublisherRegistry([a, b]), store)

    assert second.published == 0
    assert second.skipped == 2
    assert a.calls == 1 and b.calls == 1


@pytest.mark.asyncio
async def test_partial_failure_retries_only_the_failed_target(tmp_path):
    a, b = _Pub("a"), _Pub("b", fail=True)
    store = JsonResultStore(tmp_path / "s.json")
    req = _request([_target("a"), _target("b")])

    first = await fan_out(req, PublisherRegistry([a, b]), store)
    assert first.published == 1 and first.failed == 1

    b._fail = False
    second = await fan_out(req, PublisherRegistry([a, b]), store)
    assert second.published == 1 and second.skipped == 1
    assert a.calls == 1, "the target that already succeeded must not be retried"
    assert b.calls == 2


@pytest.mark.asyncio
async def test_dry_run_publishes_nothing_and_records_nothing(tmp_path):
    a = _Pub("a")
    store = JsonResultStore(tmp_path / "s.json")
    report = await fan_out(_request([_target("a")]), PublisherRegistry([a]),
                           store, dry_run=True)
    assert a.calls == 0
    assert report.published == 0
    assert store.succeeded("r1", "a", "post") is False


@pytest.mark.asyncio
async def test_validation_failure_blocks_that_target(tmp_path):
    bad = _Pub("a", violations=[Violation(publisher_id="a", reason="media required")])
    report = await fan_out(_request([_target("a")]), PublisherRegistry([bad]),
                           JsonResultStore(tmp_path / "s.json"))
    assert bad.calls == 0
    assert len(report.violations) == 1


@pytest.mark.asyncio
async def test_dry_run_reports_violations(tmp_path):
    """A dry run's whole value is finding these before the platform does."""
    bad = _Pub("a", violations=[Violation(publisher_id="a", reason="JPEG only")])
    report = await fan_out(_request([_target("a")]), PublisherRegistry([bad]),
                           JsonResultStore(tmp_path / "s.json"), dry_run=True)
    assert "JPEG only" in report.violations[0].reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ws test knarr tests/test_publisher_fanout.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.publisher.fanout'`

- [ ] **Step 3: Write minimal implementation**

Create `components/knarr/src/publisher/fanout.py`:

```python
"""Publish one request to many destinations, safely on a retry.

Fan-out is partial by nature: N destinations is N independent calls, any of
which can fail. So each target's outcome is recorded before the next is
attempted, and a retry skips what already succeeded. Without that, the second
run of a half-failed publish sends the community the same announcement twice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.publisher.registry import PublisherRegistry
from src.publisher.schemas import PublishRequest, Violation
from src.publisher.store import ResultStore

logger = logging.getLogger(__name__)


@dataclass
class FanOutReport:
    published: int = 0
    skipped: int = 0
    failed: int = 0
    violations: list[Violation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def fan_out(
    request: PublishRequest,
    registry: PublisherRegistry,
    store: ResultStore,
    dry_run: bool = False,
) -> FanOutReport:
    """Authorize, validate, then publish each target independently."""
    report = FanOutReport()

    # Whole-request authorization first. A request naming a destination it has
    # no business touching is not partially trustworthy, so nothing is
    # attempted until every target checks out.
    publishers = registry.authorize_all(request.targets, request.scope)

    for target, publisher in zip(request.targets, publishers, strict=True):
        if store.succeeded(request.request_id, publisher.id, target.verb):
            logger.info(
                "skip %s/%s — already published for request %s",
                publisher.id, target.verb, request.request_id,
            )
            report.skipped += 1
            continue

        violations = publisher.validate(target.verb, target.content, target.media)
        if violations:
            report.violations.extend(violations)
            logger.warning(
                "%s/%s blocked by %d validation failure(s)",
                publisher.id, target.verb, len(violations),
            )
            continue

        if dry_run:
            logger.info(
                "DRY RUN would %s to %s: %r",
                target.verb, publisher.id, target.content.body[:80],
            )
            continue

        method = getattr(publisher, target.verb)
        args = (target.content,) if target.verb == "post" else (target.parent, target.content)
        result = await method(*args, media=target.media)

        if not result.ok or result.ref is None:
            report.failed += 1
            report.errors.append(f"{publisher.id}/{target.verb}: {result.error}")
            logger.error(
                "%s/%s FAILED: %s", publisher.id, target.verb, result.error,
            )
            continue

        # Recorded before the next target is attempted, so an interruption
        # mid-fan-out still leaves an accurate account of what landed.
        store.record(request.request_id, publisher.id, target.verb, result.ref)
        report.published += 1
        logger.info(
            "%s/%s published as %s",
            publisher.id, target.verb, result.ref.object_id,
        )

    return report
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ws test knarr tests/test_publisher_fanout.py`
Expected: PASS (6 tests)

- [ ] **Step 5: Lint**

Run: `ws lint knarr`

- [ ] **Step 6: Commit**

Write `.commits/publisher-fanout.md`:

```markdown
---
message: "feat(publisher): idempotent fan-out across targets"
add:
  - src/publisher/fanout.py
  - tests/test_publisher_fanout.py
---

Fan-out is partial by nature — N destinations is N independent calls, any of which can fail — so each target's outcome is recorded before the next is attempted and a retry skips what already succeeded. Without that, the second run of a half-failed publish sends the community the same announcement twice.

Authorization runs whole-request before anything is attempted. Validation runs per target and blocks only that one, because a caption too long for Instagram says nothing about whether the Matrix post should go out.

A dry run walks the identical path — resolve, authorize, validate — and stops short of the call. That is what makes it worth trusting: it is the same code, not a simulation of it.
```

Run: `ws commit knarr .commits/publisher-fanout.md`

---

## Task 8: Matrix publisher

**Files:**
- Create: `components/knarr/src/publisher/adapters/__init__.py`
- Create: `components/knarr/src/publisher/adapters/matrix.py`
- Test: `components/knarr/tests/test_publisher_matrix.py`

**Interfaces:**
- Consumes: `Constraints` (Task 2), `ObjectRef`/`PublishResult`/`Violation` (Task 1).
- Produces: `MatrixPublisher(id, scope, room_id, client)` implementing `post` and `comment`.

- [ ] **Step 1: Write the failing test**

Create `components/knarr/tests/test_publisher_matrix.py`:

```python
import pytest

from src.publisher.adapters.matrix import MatrixPublisher
from src.publisher.protocols import capabilities_of
from src.publisher.schemas import ObjectRef
from src.watchers.schemas import Content


class _FakeSendResponse:
    def __init__(self, event_id):
        self.event_id = event_id


class _FakeClient:
    def __init__(self, response=None):
        self.sent = []
        self._response = response or _FakeSendResponse("$evt1")

    async def room_send(self, room_id, message_type, content, tx_id=None):
        self.sent.append({"room_id": room_id, "content": content, "tx_id": tx_id})
        return self._response


def _content(body="hello", title=""):
    return Content(type="post", title=title, body=body, author="knarr", attachments=[])


def _pub(client):
    return MatrixPublisher(
        id="mtl-matrix", scope="community/mtl",
        room_id="!room:knarr.local", client=client,
    )


def test_declares_post_and_comment_but_not_message():
    caps = capabilities_of(_pub(_FakeClient()))
    assert "post" in caps and "comment" in caps
    assert "message" not in caps


@pytest.mark.asyncio
async def test_post_returns_the_matrix_event_id():
    client = _FakeClient(_FakeSendResponse("$abc"))
    result = await _pub(client).post(_content())
    assert result.ok is True
    assert result.ref.object_id == "$abc"
    assert result.ref.publisher_id == "mtl-matrix"
    assert result.ref.object_type == "post"


@pytest.mark.asyncio
async def test_post_escapes_html_in_the_formatted_body():
    client = _FakeClient()
    await _pub(client).post(_content(body="<img src=x onerror=y>"))
    sent = client.sent[0]["content"]
    assert "<img" not in sent["formatted_body"]
    assert "&lt;img" in sent["formatted_body"]
    assert sent["body"] == "<img src=x onerror=y>", "plain body stays literal"


@pytest.mark.asyncio
async def test_comment_threads_under_the_parent():
    client = _FakeClient()
    parent = ObjectRef(publisher_id="mtl-matrix", platform="matrix",
                       surface="room", object_type="post", object_id="$root")
    await _pub(client).comment(parent, _content(body="reply"))
    rel = client.sent[0]["content"]["m.relates_to"]
    assert rel["event_id"] == "$root"


@pytest.mark.asyncio
async def test_a_rejected_send_is_reported_not_swallowed():
    """nio RETURNS errors rather than raising. An unchecked send reports a
    success that never happened — the exact bug the router shipped."""
    class _Err:
        message = "M_FORBIDDEN"

    result = await _pub(_FakeClient(_Err())).post(_content())
    assert result.ok is False
    assert result.ref is None
    assert "M_FORBIDDEN" in result.error


@pytest.mark.asyncio
async def test_transport_exception_becomes_a_failed_result():
    class _Boom:
        async def room_send(self, *a, **k):
            raise ConnectionResetError("reset")

    result = await _pub(_Boom()).post(_content())
    assert result.ok is False
    assert "reset" in result.error


def test_validate_rejects_an_empty_body():
    assert _pub(_FakeClient()).validate("post", _content(body="")) != []


def test_validate_accepts_a_normal_post():
    assert _pub(_FakeClient()).validate("post", _content()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ws test knarr tests/test_publisher_matrix.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.publisher.adapters'`

- [ ] **Step 3: Write minimal implementation**

Create `components/knarr/src/publisher/adapters/__init__.py` (empty file).

Create `components/knarr/src/publisher/adapters/matrix.py`:

```python
"""Publish into a Matrix room.

Implements Broadcaster and Commenter. Not Conversational — a room is a feed,
and DMs would be a separate publisher with its own target.
"""

from __future__ import annotations

import logging
from html import escape

from src.publisher.protocols import Constraints
from src.publisher.schemas import MediaRef, ObjectRef, PublishResult, Violation
from src.watchers.schemas import Content

logger = logging.getLogger(__name__)


class MatrixPublisher:
    platform = "matrix"
    surface = "room"

    def __init__(self, id: str, scope: str, room_id: str, client):
        self.id = id
        self.scope = scope
        self.room_id = room_id
        self.client = client
        self.constraints = Constraints()   # Matrix imposes none of ours

    def validate(self, verb: str, content: Content,
                 media: MediaRef | None = None) -> list[Violation]:
        """Pure and local — no network."""
        if not content.body.strip() and not content.title.strip():
            return [Violation(publisher_id=self.id,
                              reason="nothing to send: title and body are both empty")]
        return []

    def _payload(self, content: Content) -> dict:
        text = "\n".join(p for p in (content.title, content.body) if p)
        # formatted_body is escaped separately rather than derived from the
        # plain text. Deriving it is how the router shipped unescaped
        # attacker-controlled HTML into every operator's client.
        html = "<br>".join(escape(p).replace("\n", "<br>")
                           for p in (content.title, content.body) if p)
        return {
            "msgtype": "m.text",
            "body": text,
            "format": "org.matrix.custom.html",
            "formatted_body": html,
        }

    async def _send(self, payload: dict, object_type: str) -> PublishResult:
        try:
            response = await self.client.room_send(
                self.room_id, message_type="m.room.message", content=payload,
            )
        except Exception as exc:  # noqa: BLE001 — transport errors are varied
            return PublishResult(ok=False, ref=None,
                                 error=f"{type(exc).__name__}: {exc}")

        # nio RETURNS errors rather than raising them, so an unchecked send
        # reports a success that never happened.
        event_id = getattr(response, "event_id", None)
        if not event_id:
            return PublishResult(ok=False, ref=None, error=str(
                getattr(response, "message", response)))

        return PublishResult(
            ok=True,
            ref=ObjectRef(publisher_id=self.id, platform=self.platform,
                          surface=self.surface, object_type=object_type,
                          object_id=event_id),
            error=None,
        )

    async def post(self, content: Content,
                   media: MediaRef | None = None) -> PublishResult:
        return await self._send(self._payload(content), "post")

    async def comment(self, parent: ObjectRef, content: Content,
                      media: MediaRef | None = None) -> PublishResult:
        payload = self._payload(content)
        payload["m.relates_to"] = {
            "rel_type": "m.thread",
            "event_id": parent.object_id,
        }
        return await self._send(payload, "comment")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ws test knarr tests/test_publisher_matrix.py`
Expected: PASS (8 tests)

- [ ] **Step 5: Lint**

Run: `ws lint knarr`

- [ ] **Step 6: Commit**

Write `.commits/publisher-matrix.md`:

```markdown
---
message: "feat(publisher): Matrix adapter"
add:
  - src/publisher/adapters/__init__.py
  - src/publisher/adapters/matrix.py
  - tests/test_publisher_matrix.py
---

Implements Broadcaster and Commenter, and deliberately not Conversational — a room is a feed, and DMs would be a separate publisher with its own target.

Two lessons from the router carried over rather than rediscovered. nio returns errors instead of raising, so the send result is checked and a rejection becomes a failed PublishResult rather than a logged success that never happened. And `formatted_body` is escaped independently of the plain text, because deriving one from the other is how unescaped content reached every operator's client.
```

Run: `ws commit knarr .commits/publisher-matrix.md`

---

## Task 9: Discord publisher

**Files:**
- Create: `components/knarr/src/publisher/adapters/discord.py`
- Test: `components/knarr/tests/test_publisher_discord.py`

**Interfaces:**
- Consumes: `MatrixPublisher` (Task 8) — composition, not inheritance.
- Produces: `DiscordPublisher(id, scope, room_id, client)` implementing `post`.

**Note for the implementer:** the design's open question 3 says to try the bridge first and **record what it cannot express**. This adapter therefore posts into a Matrix room that mautrix-discord bridges to a Discord channel. That is genuinely useful and genuinely limited — write the limits down in the module docstring rather than pretending it is a native client.

- [ ] **Step 1: Write the failing test**

Create `components/knarr/tests/test_publisher_discord.py`:

```python
import pytest

from src.publisher.adapters.discord import DiscordPublisher
from src.publisher.protocols import capabilities_of
from src.watchers.schemas import Content


class _FakeSendResponse:
    def __init__(self, event_id="$evt"):
        self.event_id = event_id


class _FakeClient:
    def __init__(self):
        self.sent = []

    async def room_send(self, room_id, message_type, content, tx_id=None):
        self.sent.append({"room_id": room_id, "content": content})
        return _FakeSendResponse()


def _content(body="hello"):
    return Content(type="post", title="", body=body, author="knarr", attachments=[])


def _pub(client):
    return DiscordPublisher(
        id="mtl-discord", scope="community/mtl",
        room_id="!bridged:knarr.local", client=client,
    )


def test_reports_discord_as_the_platform_not_matrix():
    """The bridge is transport. A caller asking where this went must be told
    Discord, or the ObjectRef cannot route a reply."""
    client = _FakeClient()
    pub = _pub(client)
    assert pub.platform == "discord"
    assert pub.surface == "channel"


@pytest.mark.asyncio
async def test_post_records_discord_in_the_object_ref():
    client = _FakeClient()
    result = await _pub(client).post(_content())
    assert result.ok is True
    assert result.ref.platform == "discord"
    assert result.ref.publisher_id == "mtl-discord"


def test_declares_post_only():
    """Via the bridge there is no comment or message surface — Discord
    threads and DMs are not expressible this way."""
    assert capabilities_of(_pub(_FakeClient())) == frozenset({"post"})


@pytest.mark.asyncio
async def test_sends_through_the_bridged_room():
    client = _FakeClient()
    await _pub(client).post(_content("hi"))
    assert client.sent[0]["room_id"] == "!bridged:knarr.local"


def test_validate_rejects_over_discord_message_limit():
    """2000 characters is Discord's limit, and the bridge does not raise it."""
    long_body = "x" * 2100
    assert _pub(_FakeClient()).validate("post", _content(long_body)) != []


def test_validate_accepts_a_normal_message():
    assert _pub(_FakeClient()).validate("post", _content()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ws test knarr tests/test_publisher_discord.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.publisher.adapters.discord'`

- [ ] **Step 3: Write minimal implementation**

Create `components/knarr/src/publisher/adapters/discord.py`:

```python
"""Publish to Discord THROUGH the existing mautrix-discord bridge.

Deliberately not a native Discord client. The design asks Phase A to try the
bridge first and record what it cannot express, so:

WHAT THIS CANNOT DO, and why a native adapter may eventually be worth it:
  - No embeds. The bridge relays a Matrix message; Discord embeds have no
    Matrix equivalent that survives the trip.
  - No threads or DMs — hence `post` only. Discord threads are not reachable
    by sending into the bridged room.
  - No per-message username/avatar override (webhook-style relay).
  - Edits and deletes are not exposed here even though Discord supports both.
  - Delivery is confirmed by MATRIX, not by Discord. A successful result means
    the bridge accepted it; if the bridge is logged out, this reports success
    for a message Discord never saw. That is the sharpest limitation and the
    strongest argument for a native adapter later.

WHAT IT BUYS: no second credential, no new dependency, and it works today.
"""

from __future__ import annotations

from src.publisher.adapters.matrix import MatrixPublisher
from src.publisher.protocols import Constraints
from src.publisher.schemas import MediaRef, ObjectRef, PublishResult, Violation
from src.watchers.schemas import Content

# Discord's per-message ceiling. The bridge relays rather than raises it, so
# an over-long message is truncated or dropped downstream where we cannot see.
_DISCORD_MAX_CHARS = 2000


class DiscordPublisher:
    platform = "discord"
    surface = "channel"

    def __init__(self, id: str, scope: str, room_id: str, client):
        self.id = id
        self.scope = scope
        self.room_id = room_id
        self.constraints = Constraints(max_length=_DISCORD_MAX_CHARS)
        # Composition, not inheritance: this IS a Matrix send, but it is not a
        # Matrix destination, and the ObjectRef must say Discord or a later
        # reply cannot be routed back to the right place.
        self._transport = MatrixPublisher(
            id=id, scope=scope, room_id=room_id, client=client,
        )

    def validate(self, verb: str, content: Content,
                 media: MediaRef | None = None) -> list[Violation]:
        violations = self._transport.validate(verb, content, media)
        length = len(content.title) + len(content.body)
        if length > _DISCORD_MAX_CHARS:
            violations.append(Violation(
                publisher_id=self.id,
                reason=f"{length} characters exceeds Discord's "
                       f"{_DISCORD_MAX_CHARS}-character limit",
            ))
        return violations

    async def post(self, content: Content,
                   media: MediaRef | None = None) -> PublishResult:
        result = await self._transport.post(content, media)
        if not result.ok or result.ref is None:
            return result
        return PublishResult(
            ok=True,
            ref=ObjectRef(
                publisher_id=self.id,
                platform=self.platform,
                surface=self.surface,
                object_type="post",
                object_id=result.ref.object_id,   # the Matrix event id
            ),
            error=None,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ws test knarr tests/test_publisher_discord.py`
Expected: PASS (6 tests)

- [ ] **Step 5: Lint**

Run: `ws lint knarr`

- [ ] **Step 6: Commit**

Write `.commits/publisher-discord.md`:

```markdown
---
message: "feat(publisher): Discord adapter via the existing bridge"
add:
  - src/publisher/adapters/discord.py
  - tests/test_publisher_discord.py
---

The design asked Phase A to try the bridge first and record what it cannot express, so the module docstring is the deliverable as much as the code: no embeds, no threads or DMs, no per-message identity override, no edits or deletes — and, most importantly, delivery is confirmed by Matrix rather than by Discord. A logged-out bridge would report success for a message Discord never saw. That is the strongest argument for a native adapter later, and it is written down rather than discovered.

Composition rather than inheritance, because this is a Matrix send but not a Matrix destination: the ObjectRef has to say Discord or a later reply cannot be routed back to the right place.

It buys no second credential, no new dependency, and it works today.
```

Run: `ws commit knarr .commits/publisher-discord.md`

---

## Task 10: The `cli` origin

**Files:**
- Modify: `components/knarr/src/admin/cli.py`
- Create: `components/knarr/src/publisher/factory.py`
- Test: `components/knarr/tests/test_publisher_factory.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `build_publishers(config, client) -> list`; `knarr publish` CLI command.

- [ ] **Step 1: Write the failing test**

Create `components/knarr/tests/test_publisher_factory.py`:

```python
import pytest

from src.admin.config_schema import PublisherConfig
from src.publisher.factory import UnsupportedPublisher, build_publishers


def _cfg(pid="p", platform="matrix", surface="room"):
    return PublisherConfig(
        id=pid, platform=platform, surface=surface, access_path="api",
        scope="community/mtl", target="!room:knarr.local",
    )


def test_builds_a_matrix_publisher():
    built = build_publishers([_cfg()], client=object())
    assert built[0].platform == "matrix"
    assert built[0].id == "p"


def test_builds_a_discord_publisher():
    built = build_publishers([_cfg("d", "discord", "channel")], client=object())
    assert built[0].platform == "discord"


def test_unsupported_pair_raises_naming_the_publisher():
    with pytest.raises(UnsupportedPublisher, match="p2"):
        build_publishers([_cfg("p2", "instagram", "feed")], client=object())


def test_builds_several():
    built = build_publishers(
        [_cfg("a"), _cfg("b", "discord", "channel")], client=object(),
    )
    assert [p.id for p in built] == ["a", "b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ws test knarr tests/test_publisher_factory.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.publisher.factory'`

- [ ] **Step 3: Write minimal implementation**

Create `components/knarr/src/publisher/factory.py`:

```python
"""Build publisher instances from config.

The mirror of build_adapter in src/watchers/run.py, and it fails the same
way: an unsupported pair raises with the publisher named, rather than
returning something that half works.
"""

from __future__ import annotations

from src.admin.config_schema import PublisherConfig
from src.publisher.adapters.discord import DiscordPublisher
from src.publisher.adapters.matrix import MatrixPublisher


class UnsupportedPublisher(Exception):
    """No adapter for this (platform, surface) pair."""


def build_publishers(configs: list[PublisherConfig], client) -> list:
    built = []
    for cfg in configs:
        pair = (cfg.platform, cfg.surface)
        if pair == ("matrix", "room"):
            built.append(MatrixPublisher(
                id=cfg.id, scope=cfg.scope, room_id=cfg.target, client=client,
            ))
        elif pair == ("discord", "channel"):
            built.append(DiscordPublisher(
                id=cfg.id, scope=cfg.scope, room_id=cfg.target, client=client,
            ))
        else:
            raise UnsupportedPublisher(
                f"publisher '{cfg.id}': no adapter for "
                f"{cfg.platform}/{cfg.surface}"
            )
    return built
```

In `components/knarr/src/admin/cli.py`, add the import block near the existing imports:

```python
from src.publisher.factory import build_publishers
from src.publisher.fanout import fan_out
from src.publisher.registry import PublisherRegistry
from src.publisher.schemas import (
    PublishRequest,
    Target,
    content_digest,
    derive_request_id,
)
from src.publisher.store import JsonResultStore
from src.watchers.schemas import Content
```

and append this command at the end of the file, before the `if __name__` guard if one exists:

```python
@cli.command("publish")
@click.option("--config", "config_path", default="config/knarr.yaml",
              help="Config file path")
@click.option("--scope", required=True, help="Identity scope, e.g. community/mtl")
@click.option("--targets", required=True,
              help="Comma-separated publisher ids")
@click.option("--body", default=None, help="Message body")
@click.option("--body-file", type=click.Path(exists=True), default=None,
              help="Read the body from a file")
@click.option("--title", default="", help="Optional title")
@click.option("--verb", default="post",
              type=click.Choice(["post", "comment", "message"]))
@click.option("--dry-run", is_flag=True,
              help="Resolve, authorize and validate, but send nothing")
@click.option("--store", "store_path", default=".knarr/publish-results.json",
              help="Where publish results are recorded")
def publish(config_path, scope, targets, body, body_file, title, verb,
            dry_run, store_path):
    """Publish one piece of content to several destinations.

    Approval is that you ran this command. With one operator that is a
    complete and honest answer, and it is recorded as such.
    """
    import asyncio

    if not body and not body_file:
        click.echo("Provide --body or --body-file", err=True)
        sys.exit(1)
    if body_file:
        body = open(body_file, encoding="utf-8").read()

    try:
        config = load_config(config_path)
        validate_config(config)
    except ConfigError as e:
        click.echo(f"Config error: {e}", err=True)
        sys.exit(1)

    client = client_from_env(
        "KNARR_BRIDGE_USER", "KNARR_BRIDGE_PASSWORD", "knarr",
        lambda: (click.echo("KNARR_BRIDGE_PASSWORD is required", err=True),
                 sys.exit(1)),
    )

    publishers = build_publishers(config.publishers, client)
    registry = PublisherRegistry(publishers)

    content = Content(type="post", title=title, body=body,
                      author="knarr", attachments=[])
    target_list = [
        Target(publisher_id=pid.strip(), verb=verb, content=content)
        for pid in targets.split(",") if pid.strip()
    ]
    request = PublishRequest(
        request_id=derive_request_id(scope, target_list, content_digest(content)),
        scope=scope,
        targets=target_list,
        origin="cli",
        approved_by=f"cli:{os.environ.get('USER', 'unknown')}",
        approved_at=__import__("datetime").datetime.now(
            __import__("datetime").UTC).isoformat(),
    )

    click.echo(f"request {request.request_id}  scope {scope}  "
               f"{len(target_list)} target(s)"
               f"{'  [DRY RUN]' if dry_run else ''}")

    report = asyncio.run(fan_out(
        request, registry, JsonResultStore(store_path), dry_run=dry_run,
    ))

    for v in report.violations:
        click.echo(f"  {_ACTION_ICONS['error']} {v.publisher_id}: {v.reason}")
    for err in report.errors:
        click.echo(f"  {_ACTION_ICONS['error']} {err}")
    click.echo(
        f"published {report.published}  skipped {report.skipped}  "
        f"failed {report.failed}  blocked {len(report.violations)}"
    )
    if report.failed or report.violations:
        sys.exit(2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ws test knarr`
Expected: PASS — all tests including the 4 new factory tests.

Then confirm the command is wired:

Run: `ws exec knarr python3 -m src.admin.cli publish --help`
Expected: the option list above, no traceback.

- [ ] **Step 5: Lint**

Run: `ws lint knarr`

- [ ] **Step 6: Commit**

Write `.commits/publisher-cli.md`:

```markdown
---
message: "feat(cli): knarr publish, with a dry run that runs the real path"
add:
  - src/publisher/factory.py
  - src/admin/cli.py
  - tests/test_publisher_factory.py
---

The `cli` origin from the design: no repo, no PR, no site — the operator has content and wants it in several places. Approval is that they ran the command, and it is recorded as exactly that rather than dressed up as a review.

`--dry-run` walks the identical path — resolve, authorize, validate — and stops before the send. Being the same code rather than a simulation of it is what makes it worth trusting for a destination nobody has published to before.

`request_id` is derived from scope, targets and a content digest, so re-running the same command is recognisably the same request and skips what already landed.
```

Run: `ws commit knarr .commits/publisher-cli.md`

---

## Task 11: Wire the real destinations and document

**Files:**
- Modify: `components/knarr/config/knarr.yaml`
- Modify: `components/knarr/k8s/watchers/reddit-github.yaml`
- Modify: `components/knarr/docs/operations.md`
- Test: `components/knarr/tests/test_config_schema.py`

**Interfaces:**
- Consumes: `PublisherConfig` validation (Task 4).
- Produces: a `publishers:` block naming the two destinations Phase A ships.

- [ ] **Step 1: Write the failing test**

Append to `components/knarr/tests/test_config_schema.py`:

```python
def test_real_config_declares_the_phase_a_publishers():
    """The shipped config must actually carry the destinations Phase A adds."""
    config_path = _REPO_ROOT / "config" / "knarr.yaml"
    if not config_path.exists():
        pytest.skip(f"{config_path} not present in this checkout")
    parsed = load_config(str(config_path))
    ids = {p.id for p in parsed.publishers}
    assert {"mtl-matrix", "mtl-discord"} <= ids
    validate_config(parsed)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ws test knarr tests/test_config_schema.py -k phase_a_publishers`
Expected: FAIL — `AssertionError` (empty publisher set).

- [ ] **Step 3: Write minimal implementation**

Append to `components/knarr/config/knarr.yaml`:

```yaml
publishers:
  # Phase A ships only destinations we already run. Meta surfaces are Phase C,
  # gated on developer-account verification.
  - id: mtl-matrix
    platform: matrix
    surface: room
    access_path: api
    scope: community/mtl
    target: "#social-watch:knarr.local"

  - id: mtl-discord
    platform: discord
    surface: channel
    access_path: bridge
    scope: community/mtl
    # A Matrix room that mautrix-discord bridges to a Discord channel.
    # Delivery is confirmed by Matrix, not by Discord — see the adapter
    # docstring for what the bridge cannot express.
    target: "#autoboros-testing:knarr.local"
```

Mirror the same block into the embedded `knarr.yaml` inside the `knarr-watcher-config` ConfigMap in `components/knarr/k8s/watchers/reddit-github.yaml`, so the existing drift guard (`test_watcher_configmap_instances_match_the_source_config`) keeps passing and the pod's copy does not diverge.

Append to `components/knarr/docs/operations.md`:

```markdown
## Publishing (Phase A)

One approved thing, several destinations, nothing reposted on a retry.

```bash
# See exactly what would go where — resolves, authorizes and validates for
# real, then stops short of sending.
python3 -m src.admin.cli publish \
  --scope community/mtl \
  --targets mtl-matrix,mtl-discord \
  --body "Fall registration is open." \
  --dry-run

# Send it.
python3 -m src.admin.cli publish \
  --scope community/mtl \
  --targets mtl-matrix,mtl-discord \
  --body "Fall registration is open."
```

Re-running an identical command republishes nothing: `request_id` is derived from the scope, targets and content, and each target's result is recorded under it.

**Exit code 2 means something was blocked or failed** — read the lines above the summary. A validation failure names the destination and the rule; a send failure names the destination and the platform's error.

**Results live in `.knarr/publish-results.json`** by default (`--store` to override). Deleting that file makes the next run republish everything, because it is the only record of what already landed.

**`--targets` names publisher ids, not platforms.** A target whose scope does not match `--scope` is refused, and refusal rejects the whole request rather than the one target.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ws test knarr`
Expected: PASS — including the drift guard and the new publisher-config test.

- [ ] **Step 5: Lint**

Run: `ws lint knarr`

- [ ] **Step 6: Commit**

Write `.commits/publisher-wiring.md`:

```markdown
---
message: "feat(config): declare the Phase A publishers, and document publishing"
add:
  - config/knarr.yaml
  - k8s/watchers/reddit-github.yaml
  - docs/operations.md
  - tests/test_config_schema.py
---

Two destinations, both already running: a Matrix room and a Discord channel reached through the existing bridge. Meta surfaces are Phase C and stay out until developer-account verification clears.

The ConfigMap copy is updated in the same commit so the drift guard keeps passing — the pod's config and the repo's must not diverge, and that guard exists because they already did once.

The operations note says the two things an operator will otherwise learn the hard way: deleting the results file makes the next run republish everything, and `--targets` takes publisher ids rather than platform names.
```

Run: `ws commit knarr .commits/publisher-wiring.md`

---

## Task 12: Live validation on k3d

**No commits in this task — purely validation.** Its output is the evidence the CR body cites.

**Prerequisites:** `k3d-nordri-test` running with the `knarr` namespace healthy (Synapse `1/1`, `/health` returning `OK`), and `knarr.env` populated with `KNARR_BRIDGE_USER` / `KNARR_BRIDGE_PASSWORD`.

- [ ] **Step 1: Confirm the homeserver is reachable**

Run: `curl -s -o /dev/null -w "%{http_code}" http://matrix.knarr.local/health`
Expected: `200`. If not, see `docs/deploy.md` — do not continue.

- [ ] **Step 2: Dry run**

Run from `components/knarr/`:

```bash
env KNARR_HOMESERVER=http://matrix.knarr.local \
    KNARR_BRIDGE_USER=knarr KNARR_BRIDGE_PASSWORD=knarr-bridge-2026 \
    python3 -m src.admin.cli publish \
      --scope community/mtl --targets mtl-matrix \
      --body "Phase A validation — please ignore." --dry-run
```

Expected: a `request <id>` line, a `DRY RUN would post to mtl-matrix` log line, and `published 0`. **Nothing appears in the room.**

- [ ] **Step 3: Publish for real**

Re-run the same command without `--dry-run`.

Expected: `published 1`, and a log line carrying the Matrix event id.

- [ ] **Step 4: Verify it actually landed**

Check the room in Element, or:

```bash
curl -s "http://matrix.knarr.local/_matrix/client/v3/rooms/<ROOM_ID>/messages?dir=b&limit=1" \
  -H "Authorization: Bearer <TOKEN>"
```

Expected: the message body, and an `event_id` matching the one the CLI logged. **The logged id and the room's id must be the same** — a publisher that reports an id nothing can find is the silent-success bug pointed outward.

- [ ] **Step 5: Prove idempotency**

Run the real command a third time, unchanged.

Expected: `published 0  skipped 1`, and **no second message in the room**.

- [ ] **Step 6: Prove authorization**

Run with a deliberately wrong scope:

```bash
... --scope group/pta --targets mtl-matrix --body "should be refused"
```

Expected: a scope-mismatch error, non-zero exit, and **nothing sent**.

- [ ] **Step 7: Record the evidence**

Capture the four outcomes — dry run silent, real publish with matching event id, re-run skipped, wrong scope refused — for the CR body. These are the Phase A exit criteria.

---

## Task 13: Open the CR

- [ ] **Step 1: Run the full suite one more time**

Run: `ws test knarr` and `ws lint knarr`

- [ ] **Step 2: Push**

Run: `ws push knarr`

- [ ] **Step 3: Draft the CR body**

Run: `cp templates/change.md .crs/publish-phase-a.md` from the workspace root, then fill in Summary, Test plan and Related. The test plan must cite Task 12's four outcomes, not merely that unit tests pass.

- [ ] **Step 4: Open it**

Run: `ws cr knarr "feat: publish Phase A — capability protocols, idempotent fan-out, Matrix + Discord" .crs/publish-phase-a.md`

---

## Self-review notes

Checked against the spec:

- **Capability protocols, three verbs, derived capabilities** — Task 2.
- **`PublisherInstance` with `surface`** — Tasks 3 and 4.
- **Scope authorization before credentials** — Task 5, with a test asserting the ordering specifically.
- **Idempotency across restarts and partial fan-out** — Tasks 6 and 7.
- **`ObjectRef` composite identity** — Tasks 1 and 6.
- **`cli` origin with a real `--dry-run`** — Tasks 7 and 10.
- **Matrix and Discord adapters** — Tasks 8 and 9.
- **`validate()` pure, no network** — Tasks 2, 8, 9.

**Deliberately deferred within Phase A, and why:**

- **`preflight()` and `MediaRef` resolution are defined but unused.** Both Phase A destinations take text; media matters when Instagram arrives in Phase C. `MediaRef` exists in the schema so the shape is settled, but no task implements fetch-checking, and adding it now would be building against a destination we cannot test.
- **`knarr.publish.requests` / `.results` topics are not created.** The `cli` origin hands a request straight to the fan-out in-process. The topics matter when a second origin exists — that is Task B work, and creating them now would ship two unused topics.
- **No Kafka producer in the publish path**, for the same reason.

**Known risk carried into execution:** the Discord adapter confirms delivery via Matrix, not Discord. If the bridge is logged out, Task 12 Step 3 will report success for a message Discord never saw. Task 12 therefore validates against `mtl-matrix` only; verifying the Discord leg needs the bridge logged in (`login-token bot <token>` in the management room), which is a human step.
