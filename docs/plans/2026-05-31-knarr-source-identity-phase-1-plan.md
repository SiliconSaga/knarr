# Knarr Source-Identity — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor today's `reddit_watcher.py` + `github_watcher.py` into a single multi-tenant `WatcherInstance` model driven by per-instance config rows. No new platform, no user-visible behaviour change — pure architectural lift that unblocks Phase 2+ (AI summarize, new sources, scrape adapters).

**Architecture:** Each watcher becomes a `WatcherInstance(platform, access_path, scope, credentials_ref, polling_config)` row in `config/test.yaml`. The shared runtime in `src/watchers/run.py` loads the instance list, instantiates the right `Adapter` per row (today: `RedditApiAdapter`, `GitHubApiAdapter`), polls each on its own cadence, and emits to Kafka with a richer event envelope (adds `instance_id`, `scope`, `access_path`, `platform_event_id`, `extracted_at`, `raw_post_ref`). One pod hosts all instances for now; future phases can split to per-instance pods if scale warrants. Router stays a thin pass-through — consumes the new envelope, posts one Matrix message per event, unchanged.

**Tech Stack:** Python 3.13, `httpx`, `confluent-kafka`, `pyyaml`, `pytest` (+ `pytest-bdd` for the integration scenarios). Kubernetes manifests via `kubectl apply`. Reconciler config schema lives in `src/admin/config_schema.py`. Tests run via `bash scripts/knarr` wrapper or `python3 -m pytest`.

**Working directory assumption:** all `bash` commands in this plan run from the **yggdrasil workspace root** (where `scripts/ws` lives). Paths like `components/knarr/...` are workspace-relative. When a step needs to run from inside the knarr component (e.g., `python3 -m pytest`), it `cd`s explicitly first. Agents executing this plan via worktrees should `cd` into the workspace root of the worktree.

**Spec:** `docs/plans/2026-05-30-knarr-source-identity-design.md`. This plan covers Phase 1 (the WatcherInstance refactor) plus the minimum slice of Phase 0 the refactor actually needs (the new config schema). Personal-space provisioning, the `knarr cred` CLI, and full Keycloak integration are deferred to later phase plans when they're actually exercised.

**Phase 0 deferred items** — explicitly not in this plan:

- `personal_space_room_id` user attribute + reconciler space-provisioning (no `user/<id>`-scoped instances ship in Phase 1)
- `knarr cred create | capture | rotate | revoke` CLI subcommand (current env-var-based secrets continue working for Phase 1)
- Keycloak introduction (continues using the config-driven `users:` map; Keycloak shape is what the new config schema mirrors)
- K8s secret-naming-convention enforcement (documented in design doc, not validated by tooling yet)

**Risk-mitigated rollout:** every task is TDD with a commit at the end. Watchers can be ported one at a time — Reddit-refactored + GitHub-still-old is a valid in-progress state for two tasks (the runtime loads both code paths during the transition). Final integration validates against live k3d-nordri-test before the PR opens.

---

## File map

**Create:**
- `src/watchers/instance.py` — `WatcherInstance` runtime class (owns cursor state)
- `src/watchers/adapters/__init__.py` — `Adapter` Protocol
- `src/watchers/adapters/reddit_api.py` — Reddit adapter
- `src/watchers/adapters/github_api.py` — GitHub adapter
- `tests/test_instance.py` — `WatcherInstance` unit tests
- `tests/test_adapters_api.py` — Adapter Protocol tests via a fake adapter
- `tests/test_adapters_reddit.py` — Reddit adapter tests (replaces today's reddit watcher tests)
- `tests/test_adapters_github.py` — GitHub adapter tests (replaces today's github watcher tests)

**No shared adapter base class.** Each platform's cursor shape is
different (Reddit fullname, GitHub notification id, future platforms'
timestamp / opaque-token / etc.); a base class would either be empty
or push platform-specifics down the type system. The shared
abstraction is the `Adapter` Protocol alone; `WatcherInstance` owns
the cursor's *lifecycle* (persist between polls), while each adapter
owns the cursor's *meaning*.

**Modify:**
- `src/watchers/schemas.py` — new envelope shape (clean break, no compat shim)
- `src/watchers/run.py` — load instance list from config, instantiate adapters, poll each
- `src/admin/config_schema.py` — add `InstanceConfig` dataclass + parse `instances:` key
- `src/router/kafka_consumer.py` — consume new envelope shape, same Matrix message output
- `config/test.yaml` — rewrite as `instances:` list rather than `rooms.watchers:`
- `k8s/watchers/reddit-github.yaml` → renamed `k8s/watchers/watchers.yaml`, ConfigMap-mounted instance config
- `tests/test_schemas.py` — assertions for new envelope fields
- `tests/test_router.py` — fixture updated for new envelope
- `tests/test_config_schema.py` — assertions for `instances:` parsing
- `tests/features/reconcile.feature` — adjusted (rooms-via-instances rather than rooms-with-inline-watchers)
- `tests/features/steps/reconcile_steps.py` — adjusted accordingly

**Delete:**
- `src/watchers/reddit_watcher.py` (logic moves to `adapters/reddit_api.py`)
- `src/watchers/github_watcher.py` (logic moves to `adapters/github_api.py`)
- `tests/test_reddit_watcher.py` (replaced by `test_adapters_reddit.py`)
- `tests/test_github_watcher.py` (replaced by `test_adapters_github.py`)

---

## Task 1 — Extend config_schema with InstanceConfig

**Files:**
- Modify: `components/knarr/src/admin/config_schema.py`
- Test: `components/knarr/tests/test_config_schema.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_config_schema.py`, add at the end of file:

```python
def test_parse_instances_block():
    """Top-level `instances:` parses into InstanceConfig dataclasses."""
    cfg = {
        **VALID_INDEX,
        "instances": [
            {
                "id": "reddit-terasology",
                "platform": "reddit",
                "access_path": "api",
                "scope": "community/terasology",
                "credentials_ref": None,
                "polling": {"interval_seconds": 21600},
                "platform_config": {"subreddit": "Terasology"},
                "target_room": "social-watch",
            },
            {
                "id": "github-terasology",
                "platform": "github",
                "access_path": "api",
                "scope": "community/terasology",
                "credentials_ref": {
                    "secret_name": "knarr-cred-community-terasology-gh-pat",
                    "secret_key": "token",
                },
                "polling": {"interval_seconds": 21600},
                "platform_config": {"repos": ["MovingBlocks/Terasology"]},
                "target_room": "social-watch",
            },
        ],
    }
    parsed = KnarrConfig.from_dict(cfg, community_loader=lambda _: VALID_COMMUNITY)
    assert len(parsed.instances) == 2
    assert parsed.instances[0].id == "reddit-terasology"
    assert parsed.instances[0].platform == "reddit"
    assert parsed.instances[0].access_path == "api"
    assert parsed.instances[0].scope == "community/terasology"
    assert parsed.instances[0].credentials_ref is None
    assert parsed.instances[0].polling["interval_seconds"] == 21600
    assert parsed.instances[0].platform_config["subreddit"] == "Terasology"
    assert parsed.instances[0].target_room == "social-watch"
    # GitHub instance has a credentials_ref
    assert parsed.instances[1].credentials_ref["secret_name"] == \
        "knarr-cred-community-terasology-gh-pat"


def test_parse_instances_defaults_to_empty():
    """No `instances:` key → instances is an empty list."""
    parsed = KnarrConfig.from_dict(VALID_INDEX, community_loader=lambda _: VALID_COMMUNITY)
    assert parsed.instances == []


def test_validate_rejects_instance_with_unknown_scope_type():
    """Scope must use the canonical prefixes: community/, user/, group/."""
    bad = {
        **VALID_INDEX,
        "instances": [{
            "id": "broken",
            "platform": "reddit",
            "access_path": "api",
            "scope": "garbage/terasology",
            "polling": {"interval_seconds": 100},
            "platform_config": {},
            "target_room": "social-watch",
        }],
    }
    config = KnarrConfig.from_dict(bad, community_loader=lambda _: VALID_COMMUNITY)
    with pytest.raises(ConfigError, match="scope"):
        validate_config(config)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd components/knarr && python3 -m pytest tests/test_config_schema.py::test_parse_instances_block tests/test_config_schema.py::test_parse_instances_defaults_to_empty tests/test_config_schema.py::test_validate_rejects_instance_with_unknown_scope_type -v`
Expected: 3 failures. First two fail with `AttributeError: 'KnarrConfig' object has no attribute 'instances'`. Third fails because the unknown-scope check doesn't exist yet.

- [ ] **Step 3: Add InstanceConfig dataclass + parsing**

In `src/admin/config_schema.py`, add after the `CommunityConfig` dataclass (above `KnarrConfig`):

```python
_VALID_SCOPE_PREFIXES = ("community/", "user/", "group/")


@dataclass
class InstanceConfig:
    """A single WatcherInstance: one (platform, access_path, scope) row.

    Each instance is independently configured and runs against one source
    on behalf of one identity scope. See
    docs/plans/2026-05-30-knarr-source-identity-design.md for the model.
    """
    id: str
    platform: str               # "reddit", "github", "facebook", "bluesky", ...
    access_path: str            # "api", "admin-app", "scrape", "email+scrape", "paid-api"
    scope: str                  # "community/<slug>" | "user/<id>" | "group/<id>"
    polling: dict               # {"interval_seconds": N, ...platform-tuned knobs...}
    platform_config: dict       # platform-specific config (subreddit, repos, channel_id, etc.)
    target_room: str            # config-key of the Matrix room to route alerts to.
                                # Stored here in Phase 1 but NOT consumed by the
                                # router yet (Phase 1 router still posts to the
                                # MATRIX_ROOM_ID env var, preserving today's
                                # behaviour). Phase 2's per-instance routing
                                # reads it.
    credentials_ref: dict | None = None  # {"secret_name": str, "secret_key": str}

    @classmethod
    def from_dict(cls, data: dict) -> "InstanceConfig":
        required = ("id", "platform", "access_path", "scope",
                    "polling", "platform_config", "target_room")
        for key in required:
            if key not in data:
                raise ConfigError(f"instance is missing required field: {key}")
        return cls(
            id=data["id"],
            platform=data["platform"],
            access_path=data["access_path"],
            scope=data["scope"],
            polling=_require_mapping(data["polling"], f"instance.{data['id']}.polling"),
            platform_config=_require_mapping(
                data["platform_config"], f"instance.{data['id']}.platform_config"),
            target_room=data["target_room"],
            credentials_ref=(
                _require_mapping(
                    data["credentials_ref"],
                    f"instance.{data['id']}.credentials_ref")
                if data.get("credentials_ref") is not None else None
            ),
        )
```

Then modify the `KnarrConfig` dataclass to include `instances`:

```python
@dataclass
class KnarrConfig:
    server_name: str
    secrets: dict[str, str] = field(default_factory=dict)
    users: dict[str, str] = field(default_factory=dict)
    communities: list[CommunityConfig] = field(default_factory=list)
    instances: list[InstanceConfig] = field(default_factory=list)   # NEW

    @classmethod
    def from_dict(
        cls,
        data: dict,
        community_loader: Callable[[str], dict],
    ) -> "KnarrConfig":
        if "server_name" not in data:
            raise ConfigError("server_name is required in the index config")

        community_paths = _require_str_list(data.get("communities"), "communities")
        communities = []
        for path in community_paths:
            community_data = community_loader(path)
            communities.append(CommunityConfig.from_dict(community_data))

        # NEW: parse instances list
        instances_raw = data.get("instances") or []
        if not isinstance(instances_raw, list):
            raise ConfigError(
                f"instances must be a list, got {type(instances_raw).__name__}"
            )
        instances = [InstanceConfig.from_dict(_require_mapping(i, "instance"))
                     for i in instances_raw]

        return cls(
            server_name=data["server_name"],
            secrets=_require_mapping(data.get("secrets"), "secrets"),
            users=_require_mapping(data.get("users"), "users"),
            communities=communities,
            instances=instances,                                    # NEW
        )
```

Add the scope-prefix check inside `validate_config`, immediately after the existing duplicate-key checks (right before `if errors:`):

```python
    # NEW: validate instance scope prefixes
    for inst in config.instances:
        if not any(inst.scope.startswith(p) for p in _VALID_SCOPE_PREFIXES):
            errors.append(
                f"Instance '{inst.id}': scope '{inst.scope}' must start with "
                f"one of {_VALID_SCOPE_PREFIXES}"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd components/knarr && python3 -m pytest tests/test_config_schema.py -v`
Expected: all tests pass (the three new ones plus the existing ~21).

- [ ] **Step 5: Lint passes**

Run: `cd components/knarr && python3 -m ruff check src/ tests/`
Expected: `All checks passed!` (fix any import-sort or unused-import nits with `--fix` if needed).

- [ ] **Step 6: Commit**

Write `.commits/task1-instance-config-schema.md`:

```markdown
---
message: "feat(config): add InstanceConfig dataclass + instances list parsing"
add:
  - src/admin/config_schema.py
  - tests/test_config_schema.py
---

Adds the top-level `instances:` list to KnarrConfig. Each instance row
is the (platform, access_path, scope, credentials_ref, polling,
platform_config, target_room) tuple per the source-identity design
doc. `validate_config` rejects scopes that don't start with the
canonical community/, user/, or group/ prefix.

No consumers yet — schema lands first so the rest of the refactor
can build on it.
```

Run: `bash scripts/ws commit knarr .commits/task1-instance-config-schema.md`

---

## Task 2 — Rewrite WatchAlert schema (clean break, no compat shim)

**Files:**
- Modify: `components/knarr/src/watchers/schemas.py` (full rewrite)
- Test: `components/knarr/tests/test_schemas.py`

- [ ] **Step 1: Write the failing test (new schema shape)**

Replace the entire contents of `tests/test_schemas.py` with:

```python
from datetime import UTC, datetime

from src.watchers.schemas import (
    Attachment,
    Content,
    WatchAlert,
)


def _sample_alert() -> WatchAlert:
    return WatchAlert(
        event_id="reddit_t3_abc123",
        instance_id="reddit-terasology",
        scope="community/terasology",
        access_path="api",
        platform="reddit",
        raw_post_ref="https://reddit.com/r/Terasology/comments/abc123/foo",
        content=Content(
            type="post",
            title="A new release",
            body="Body text here",
            author="cervator",
            attachments=[],
        ),
        timestamp="2026-05-31T12:00:00+00:00",
        extracted_at="2026-05-31T12:00:01+00:00",
    )


def test_watch_alert_serializes_to_dict():
    alert = _sample_alert()
    data = alert.to_kafka_dict()
    assert data["event_id"] == "reddit_t3_abc123"
    assert data["instance_id"] == "reddit-terasology"
    assert data["scope"] == "community/terasology"
    assert data["access_path"] == "api"
    assert data["platform"] == "reddit"
    assert data["raw_post_ref"].startswith("https://")
    assert data["content"]["type"] == "post"
    assert data["content"]["title"] == "A new release"
    assert data["content"]["body"] == "Body text here"
    assert data["content"]["author"] == "cervator"
    assert data["content"]["attachments"] == []
    assert data["timestamp"] == "2026-05-31T12:00:00+00:00"
    assert data["extracted_at"] == "2026-05-31T12:00:01+00:00"


def test_watch_alert_from_kafka_dict_roundtrip():
    original = _sample_alert()
    data = original.to_kafka_dict()
    rebuilt = WatchAlert.from_kafka_dict(data)
    assert rebuilt == original


def test_attachment_serializes():
    alert = WatchAlert(
        event_id="github_release_1.0",
        instance_id="github-terasology",
        scope="community/terasology",
        access_path="api",
        platform="github",
        raw_post_ref="https://github.com/MovingBlocks/Terasology/releases/tag/v1.0",
        content=Content(
            type="release",
            title="v1.0",
            body="Release notes",
            author="MovingBlocks",
            attachments=[
                Attachment(url="https://github.com/.../asset.zip", mime_hint="application/zip")
            ],
        ),
        timestamp="2026-05-31T12:00:00+00:00",
        extracted_at="2026-05-31T12:00:01+00:00",
    )
    data = alert.to_kafka_dict()
    assert data["content"]["attachments"][0]["url"].endswith("asset.zip")
    assert data["content"]["attachments"][0]["mime_hint"] == "application/zip"


def test_extracted_at_defaults_to_now():
    """Default extracted_at fires at construction time."""
    before = datetime.now(UTC).isoformat()
    alert = WatchAlert(
        event_id="x",
        instance_id="x",
        scope="community/x",
        access_path="api",
        platform="x",
        raw_post_ref="https://example.com",
        content=Content(type="post", title="", body="", author="", attachments=[]),
        timestamp="2026-05-31T00:00:00+00:00",
    )
    after = datetime.now(UTC).isoformat()
    assert before <= alert.extracted_at <= after
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd components/knarr && python3 -m pytest tests/test_schemas.py -v`
Expected: failures — `Attachment` doesn't exist, `Content` doesn't take `title`/`author`/`attachments`, `WatchAlert` doesn't take the new fields.

- [ ] **Step 3: Rewrite schemas.py**

Replace the entire contents of `src/watchers/schemas.py` with:

```python
"""Kafka event envelope schemas for Knarr watchers.

Schema corresponds to the `knarr.watch.alerts` topic. See
docs/plans/2026-05-30-knarr-source-identity-design.md § Kafka schemas.
"""

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime


@dataclass
class Attachment:
    """A non-text resource attached to a post (image, video, file, etc.)."""
    url: str
    mime_hint: str = ""   # best-guess; may be empty if upstream didn't say


@dataclass
class Content:
    """The human-facing payload of a watched event."""
    type: str                        # "post" | "comment" | "release" | "reaction" | ...
    title: str                       # platform's title; may be empty
    body: str                        # full text
    author: str                      # platform-handle (best-effort)
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class WatchAlert:
    """A single event emitted by a WatcherInstance to Kafka.

    Carries enough context for the router to make presentation/routing
    decisions and to link back to the platform original.
    """
    event_id: str                    # platform-stable, used for dedupe
    instance_id: str                 # which WatcherInstance produced this
    scope: str                       # identity scope ("community/terasology", "user/cervator", etc.)
    access_path: str                 # how it was sourced ("api", "scrape", ...)
    platform: str
    raw_post_ref: str                # canonical URL on the source platform
    content: Content
    timestamp: str                   # when the event was created upstream (ISO 8601)
    extracted_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def to_kafka_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_kafka_dict(cls, data: dict) -> "WatchAlert":
        content_raw = data["content"]
        content = Content(
            type=content_raw["type"],
            title=content_raw["title"],
            body=content_raw["body"],
            author=content_raw["author"],
            attachments=[
                Attachment(**a) for a in content_raw.get("attachments", [])
            ],
        )
        return cls(
            event_id=data["event_id"],
            instance_id=data["instance_id"],
            scope=data["scope"],
            access_path=data["access_path"],
            platform=data["platform"],
            raw_post_ref=data["raw_post_ref"],
            content=content,
            timestamp=data["timestamp"],
            extracted_at=data["extracted_at"],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd components/knarr && python3 -m pytest tests/test_schemas.py -v`
Expected: all 4 tests pass.

- [ ] **Step 5: Note other tests are now broken (expected)**

Run: `cd components/knarr && python3 -m pytest tests/ --ignore=tests/features 2>&1 | tail -20`
Expected: `test_reddit_watcher.py`, `test_github_watcher.py`, `test_router.py` fail — they reference the old `Source` dataclass which no longer exists. Those tests get rewritten / replaced in later tasks. **Do not "fix" them yet.** This is the intentional clean break.

- [ ] **Step 6: Lint passes**

Run: `cd components/knarr && python3 -m ruff check src/watchers/schemas.py tests/test_schemas.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

Write `.commits/task2-new-schema.md`:

```markdown
---
message: "feat(watchers): rewrite WatchAlert envelope for the WatcherInstance model"
add:
  - src/watchers/schemas.py
  - tests/test_schemas.py
---

Clean break — new envelope fields:
- `instance_id`, `scope`, `access_path` (the architectural additions)
- `raw_post_ref`, `extracted_at`, `platform`
- `Content` grows `title`, `author`, `attachments`

`Source` dataclass removed (community/channel/platform now derivable
from scope + platform + instance_id). No back-compat shim — nothing
live yet.

Consumer tests (router, reddit_watcher, github_watcher) intentionally
left broken; they get rewritten in subsequent tasks as the
adapter/instance refactor lands.
```

Run: `bash scripts/ws commit knarr .commits/task2-new-schema.md`

---

## Task 3 — Define the Adapter Protocol

**Files:**
- Create: `components/knarr/src/watchers/adapters/__init__.py`
- Test: `components/knarr/tests/test_adapters_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_adapters_api.py`:

```python
from datetime import UTC, datetime

import pytest

from src.watchers.adapters import Adapter
from src.watchers.schemas import Content, WatchAlert


class _FakeAdapter:
    """Minimal Protocol-satisfying adapter for testing."""

    def __init__(self):
        self._cursor: str | None = None

    async def fetch(self, since_cursor: str | None) -> tuple[list[WatchAlert], str | None]:
        if since_cursor is None:
            alerts = [
                WatchAlert(
                    event_id=f"fake_{i}",
                    instance_id="fake-instance",
                    scope="community/test",
                    access_path="api",
                    platform="fake",
                    raw_post_ref=f"https://fake/{i}",
                    content=Content(
                        type="post", title=f"Post {i}", body="body",
                        author="someone", attachments=[],
                    ),
                    timestamp=datetime.now(UTC).isoformat(),
                )
                for i in range(3)
            ]
            return alerts, "fake_2"
        return [], since_cursor


def test_adapter_protocol_accepts_fake():
    """Anything with the right shape satisfies the Protocol."""
    adapter: Adapter = _FakeAdapter()
    assert hasattr(adapter, "fetch")


@pytest.mark.asyncio
async def test_fake_adapter_returns_alerts_and_cursor():
    adapter = _FakeAdapter()
    alerts, cursor = await adapter.fetch(None)
    assert len(alerts) == 3
    assert cursor == "fake_2"
    # Second call with cursor returns nothing new
    alerts2, cursor2 = await adapter.fetch(cursor)
    assert alerts2 == []
    assert cursor2 == cursor
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd components/knarr && python3 -m pytest tests/test_adapters_api.py -v`
Expected: failure — `ModuleNotFoundError: No module named 'src.watchers.adapters'`.

- [ ] **Step 3: Create the Protocol**

Create `src/watchers/adapters/__init__.py`:

```python
"""Adapter Protocol — how WatcherInstance talks to platforms.

Each concrete adapter (RedditApiAdapter, GitHubApiAdapter, etc.) speaks
to one platform through one access path. The Protocol keeps WatcherInstance
adapter-agnostic.

See docs/plans/2026-05-30-knarr-source-identity-design.md § Pipeline
architecture for the WatcherInstance + adapter split rationale.
"""

from typing import Protocol

from src.watchers.schemas import WatchAlert


class Adapter(Protocol):
    """A platform-specific source adapter.

    `fetch` is called by the host WatcherInstance on each poll cycle. The
    cursor lets the adapter remember "where it left off"; what the cursor
    looks like is adapter-defined (a timestamp, a fullname id, an opaque
    token — whatever the platform supports). The adapter returns whatever
    new events it saw plus the new cursor value, which the instance
    persists.
    """

    async def fetch(
        self, since_cursor: str | None
    ) -> tuple[list[WatchAlert], str | None]:
        """Fetch events since the given cursor.

        Args:
            since_cursor: opaque value previously returned by this adapter;
                          None on first call.

        Returns:
            (alerts, new_cursor) — alerts may be empty; new_cursor may equal
            since_cursor if nothing new arrived.
        """
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd components/knarr && python3 -m pytest tests/test_adapters_api.py -v`
Expected: both tests pass. (If the `pytest.mark.asyncio` fails with "is async but not annotated", confirm `pytest-asyncio` is installed and `asyncio_mode = "auto"` is in `pyproject.toml` — it should already be.)

- [ ] **Step 5: Lint passes**

Run: `cd components/knarr && python3 -m ruff check src/watchers/adapters/ tests/test_adapters_api.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

Write `.commits/task3-adapter-protocol.md`:

```markdown
---
message: "feat(watchers): define Adapter Protocol"
add:
  - src/watchers/adapters/__init__.py
  - tests/test_adapters_api.py
---

Adapter is the platform-specific seam. Each concrete adapter (Reddit,
GitHub, future Bluesky, FB, etc.) implements one `fetch(cursor)` method
that returns (alerts, new_cursor). The cursor is opaque — adapters
choose what it looks like (timestamp, fullname id, page token, etc.).

WatcherInstance (next task) hosts an Adapter and persists the cursor
between poll cycles, keeping the runtime agnostic to platform shape.
```

Run: `bash scripts/ws commit knarr .commits/task3-adapter-protocol.md`

---

## Task 4 — Build the WatcherInstance runtime

**Files:**
- Create: `components/knarr/src/watchers/instance.py`
- Test: `components/knarr/tests/test_instance.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_instance.py`:

```python
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from src.admin.config_schema import InstanceConfig
from src.watchers.instance import WatcherInstance
from src.watchers.schemas import Content, WatchAlert


def _alert(event_id: str) -> WatchAlert:
    return WatchAlert(
        event_id=event_id,
        instance_id="test-instance",
        scope="community/test",
        access_path="api",
        platform="fake",
        raw_post_ref=f"https://fake/{event_id}",
        content=Content(
            type="post", title="T", body="B", author="a", attachments=[],
        ),
        timestamp=datetime.now(UTC).isoformat(),
    )


class _StubAdapter:
    """Returns scripted (alerts, cursor) sequences."""

    def __init__(self, scripted: list[tuple[list[WatchAlert], str | None]]):
        self._scripted = list(scripted)
        self.calls: list[str | None] = []

    async def fetch(self, since_cursor):
        self.calls.append(since_cursor)
        if self._scripted:
            return self._scripted.pop(0)
        return [], since_cursor


def _config() -> InstanceConfig:
    return InstanceConfig(
        id="test-instance",
        platform="fake",
        access_path="api",
        scope="community/test",
        polling={"interval_seconds": 60},
        platform_config={},
        target_room="some-room",
    )


@pytest.mark.asyncio
async def test_instance_passes_no_cursor_on_first_poll():
    adapter = _StubAdapter([([_alert("a")], "cursor1")])
    producer = MagicMock()
    inst = WatcherInstance(_config(), adapter, producer, "knarr.watch.alerts")

    await inst.poll_once()
    assert adapter.calls == [None]


@pytest.mark.asyncio
async def test_instance_stores_cursor_between_polls():
    adapter = _StubAdapter([
        ([_alert("a")], "cursor1"),
        ([_alert("b")], "cursor2"),
    ])
    producer = MagicMock()
    inst = WatcherInstance(_config(), adapter, producer, "knarr.watch.alerts")

    await inst.poll_once()
    await inst.poll_once()
    assert adapter.calls == [None, "cursor1"]


@pytest.mark.asyncio
async def test_instance_publishes_each_alert_to_kafka():
    adapter = _StubAdapter([([_alert("a"), _alert("b")], "c1")])
    producer = MagicMock()
    inst = WatcherInstance(_config(), adapter, producer, "knarr.watch.alerts")

    await inst.poll_once()

    # Producer.produce called once per alert
    assert producer.produce.call_count == 2
    first_call = producer.produce.call_args_list[0]
    assert first_call.args[0] == "knarr.watch.alerts"
    payload = json.loads(first_call.kwargs["value"])
    assert payload["event_id"] == "a"
    assert payload["instance_id"] == "test-instance"
    producer.flush.assert_called_once()


@pytest.mark.asyncio
async def test_instance_skips_publish_when_no_alerts():
    adapter = _StubAdapter([([], "c1")])
    producer = MagicMock()
    inst = WatcherInstance(_config(), adapter, producer, "knarr.watch.alerts")

    await inst.poll_once()
    producer.produce.assert_not_called()
    producer.flush.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd components/knarr && python3 -m pytest tests/test_instance.py -v`
Expected: `ModuleNotFoundError: No module named 'src.watchers.instance'`.

- [ ] **Step 3: Implement WatcherInstance**

Create `src/watchers/instance.py`:

```python
"""WatcherInstance — generic runtime that hosts an Adapter and publishes alerts.

One instance per (platform, scope) config row. See
docs/plans/2026-05-30-knarr-source-identity-design.md § Pipeline
architecture for the model.
"""

import json
import logging

from confluent_kafka import Producer

from src.admin.config_schema import InstanceConfig
from src.watchers.adapters import Adapter

logger = logging.getLogger(__name__)


class WatcherInstance:
    """Hosts an Adapter, persists the cursor, publishes alerts to Kafka.

    Stateless except for the dedup cursor, which lives in-memory for now
    (acceptable while the host pod is long-running; future phase moves
    this to Valkey for restart-resilience).
    """

    def __init__(
        self,
        config: InstanceConfig,
        adapter: Adapter,
        producer: Producer,
        kafka_topic: str,
    ):
        self.config = config
        self.adapter = adapter
        self.producer = producer
        self.kafka_topic = kafka_topic
        self._cursor: str | None = None

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def interval_seconds(self) -> int:
        return int(self.config.polling.get("interval_seconds", 300))

    async def poll_once(self) -> int:
        """One poll cycle. Returns the number of alerts published."""
        alerts, new_cursor = await self.adapter.fetch(self._cursor)
        self._cursor = new_cursor

        for alert in alerts:
            self.producer.produce(
                self.kafka_topic,
                key=f"{alert.platform}:{alert.instance_id}",
                value=json.dumps(alert.to_kafka_dict()),
            )

        if alerts:
            self.producer.flush()
            logger.info(
                "instance=%s published=%d new_cursor=%s",
                self.config.id, len(alerts), new_cursor,
            )

        return len(alerts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd components/knarr && python3 -m pytest tests/test_instance.py -v`
Expected: all 4 tests pass.

- [ ] **Step 5: Lint passes**

Run: `cd components/knarr && python3 -m ruff check src/watchers/instance.py tests/test_instance.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

Write `.commits/task4-watcher-instance.md`:

```markdown
---
message: "feat(watchers): add WatcherInstance runtime"
add:
  - src/watchers/instance.py
  - tests/test_instance.py
---

Generic instance hosts any Adapter, persists the per-instance dedup
cursor in-memory (acceptable while the host pod is long-running;
Valkey-backed persistence is a future phase concern), and publishes
alerts to Kafka with the instance's platform+id as the message key.

Skips the producer.flush() call when there's nothing to publish so
quiet poll cycles don't generate noise.
```

Run: `bash scripts/ws commit knarr .commits/task4-watcher-instance.md`

---

## Task 5 — Port reddit_watcher → RedditApiAdapter

**Files:**
- Create: `components/knarr/src/watchers/adapters/reddit_api.py`
- Create: `components/knarr/tests/test_adapters_reddit.py`
- Delete: `components/knarr/src/watchers/reddit_watcher.py`
- Delete: `components/knarr/tests/test_reddit_watcher.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_adapters_reddit.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from src.watchers.adapters.reddit_api import RedditApiAdapter


_SAMPLE_REDDIT_JSON = {
    "data": {
        "children": [
            {
                "data": {
                    "name": "t3_post1",
                    "id": "post1",
                    "title": "First post",
                    "author": "cervator",
                    "permalink": "/r/Terasology/comments/post1/first_post/",
                    "selftext": "Hello world",
                    "created_utc": 1748700000,
                }
            },
            {
                "data": {
                    "name": "t3_post2",
                    "id": "post2",
                    "title": "Second post",
                    "author": "[deleted]",
                    "permalink": "/r/Terasology/comments/post2/second/",
                    "selftext": "",
                    "created_utc": 1748700060,
                }
            },
        ]
    }
}


@pytest.mark.asyncio
async def test_first_fetch_returns_all_posts_and_cursor():
    adapter = RedditApiAdapter(
        instance_id="reddit-terasology",
        scope="community/terasology",
        subreddit="Terasology",
    )
    with patch.object(adapter, "_http_get",
                      new=AsyncMock(return_value=_SAMPLE_REDDIT_JSON)):
        alerts, cursor = await adapter.fetch(None)

    assert len(alerts) == 2
    # Cursor is the newest fullname
    assert cursor == "t3_post1"

    a = alerts[0]
    assert a.instance_id == "reddit-terasology"
    assert a.scope == "community/terasology"
    assert a.access_path == "api"
    assert a.platform == "reddit"
    assert a.event_id == "t3_post1"
    assert a.raw_post_ref == "https://www.reddit.com/r/Terasology/comments/post1/first_post/"
    assert a.content.type == "post"
    assert a.content.title == "First post"
    assert a.content.author == "cervator"
    assert "Hello world" in a.content.body


@pytest.mark.asyncio
async def test_second_fetch_filters_already_seen():
    """With a cursor, only posts newer than the cursor are returned."""
    adapter = RedditApiAdapter(
        instance_id="reddit-terasology",
        scope="community/terasology",
        subreddit="Terasology",
    )
    with patch.object(adapter, "_http_get",
                      new=AsyncMock(return_value=_SAMPLE_REDDIT_JSON)):
        alerts, cursor = await adapter.fetch("t3_post1")

    # post1 is the cursor — nothing newer than it in the fixture
    assert alerts == []
    assert cursor == "t3_post1"


@pytest.mark.asyncio
async def test_deleted_author_handled():
    adapter = RedditApiAdapter(
        instance_id="r", scope="community/x", subreddit="X",
    )
    with patch.object(adapter, "_http_get",
                      new=AsyncMock(return_value=_SAMPLE_REDDIT_JSON)):
        alerts, _ = await adapter.fetch(None)

    deleted_author_alert = [a for a in alerts if a.event_id == "t3_post2"][0]
    assert deleted_author_alert.content.author == "[deleted]"
    assert deleted_author_alert.content.body == ""  # empty selftext


@pytest.mark.asyncio
async def test_empty_response_returns_empty_alerts():
    adapter = RedditApiAdapter(
        instance_id="r", scope="community/x", subreddit="X",
    )
    empty = {"data": {"children": []}}
    with patch.object(adapter, "_http_get", new=AsyncMock(return_value=empty)):
        alerts, cursor = await adapter.fetch(None)
    assert alerts == []
    assert cursor is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd components/knarr && python3 -m pytest tests/test_adapters_reddit.py -v`
Expected: `ModuleNotFoundError: No module named 'src.watchers.adapters.reddit_api'`.

- [ ] **Step 3: Implement the adapter**

Create `src/watchers/adapters/reddit_api.py`:

```python
"""Reddit API adapter — polls a subreddit's /new endpoint.

Replaces the legacy `src/watchers/reddit_watcher.py`. Same wire behaviour;
new internal shape (Adapter Protocol + per-instance config).
"""

import logging
from datetime import UTC, datetime

import httpx

from src.watchers.schemas import Content, WatchAlert

logger = logging.getLogger(__name__)

_REDDIT_BASE = "https://www.reddit.com"
_USER_AGENT = "knarr-watcher/0.2 (by u/Cervator)"


class RedditApiAdapter:
    """Polls /r/<subreddit>/new.json; emits one WatchAlert per new post.

    Cursor is the Reddit `name` (e.g. `t3_xxxxxx`) of the newest post seen
    so far. Posts at or older than the cursor are filtered out.
    """

    def __init__(self, instance_id: str, scope: str, subreddit: str):
        self.instance_id = instance_id
        self.scope = scope
        self.subreddit = subreddit

    async def _http_get(self, url: str) -> dict:
        """Indirection point so tests can mock the HTTP layer."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            )
            response.raise_for_status()
            return response.json()

    async def fetch(
        self, since_cursor: str | None
    ) -> tuple[list[WatchAlert], str | None]:
        url = f"{_REDDIT_BASE}/r/{self.subreddit}/new.json?limit=10"
        payload = await self._http_get(url)
        posts = payload["data"]["children"]

        alerts: list[WatchAlert] = []
        newest_seen: str | None = None
        for post in posts:
            data = post["data"]
            name = data["name"]
            if newest_seen is None:
                newest_seen = name  # first item is newest (Reddit /new sorts desc)
            if since_cursor is not None and name == since_cursor:
                # Reached previously-seen tip; anything after is old too.
                break

            permalink = data["permalink"]
            created_ts = datetime.fromtimestamp(
                data.get("created_utc", 0), tz=UTC,
            ).isoformat()

            alerts.append(WatchAlert(
                event_id=name,
                instance_id=self.instance_id,
                scope=self.scope,
                access_path="api",
                platform="reddit",
                raw_post_ref=f"{_REDDIT_BASE}{permalink}",
                content=Content(
                    type="post",
                    title=data.get("title", ""),
                    body=data.get("selftext", ""),
                    author=data.get("author", "[deleted]"),
                    attachments=[],
                ),
                timestamp=created_ts,
            ))

        cursor = newest_seen if newest_seen is not None else since_cursor
        return alerts, cursor
```

- [ ] **Step 4: Delete the legacy reddit_watcher**

Run:

```bash
rm components/knarr/src/watchers/reddit_watcher.py
rm components/knarr/tests/test_reddit_watcher.py
```

- [ ] **Step 5: Run test to verify the adapter tests pass**

Run: `cd components/knarr && python3 -m pytest tests/test_adapters_reddit.py -v`
Expected: 4 tests pass.

- [ ] **Step 6: Lint passes**

Run: `cd components/knarr && python3 -m ruff check src/watchers/adapters/reddit_api.py tests/test_adapters_reddit.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

Write `.commits/task5-reddit-adapter.md`:

```markdown
---
message: "refactor(watchers): port reddit watcher to RedditApiAdapter"
add:
  - src/watchers/adapters/reddit_api.py
  - tests/test_adapters_reddit.py
delete:
  - src/watchers/reddit_watcher.py
  - tests/test_reddit_watcher.py
---

Same Reddit /new polling behaviour as before; new shape:
- Implements the Adapter Protocol (fetch(cursor) → (alerts, cursor))
- Cursor is the Reddit fullname (`t3_xxx`) of the newest seen post
- Emits WatchAlerts with the new envelope (instance_id, scope,
  access_path, raw_post_ref) populated from the adapter's config

The HTTP call goes through `_http_get` so tests can patch it
without touching httpx internals.
```

Run: `bash scripts/ws commit knarr .commits/task5-reddit-adapter.md`

**Note:** `ws commit` may not handle `delete:` frontmatter natively. If it doesn't, after the commit succeeds with the new files, run:

```bash
cd components/knarr && git rm src/watchers/reddit_watcher.py tests/test_reddit_watcher.py
git -C components/knarr commit -m "refactor(watchers): remove legacy reddit_watcher (replaced by RedditApiAdapter)"
```

---

## Task 6 — Port github_watcher → GitHubApiAdapter

**Files:**
- Create: `components/knarr/src/watchers/adapters/github_api.py`
- Create: `components/knarr/tests/test_adapters_github.py`
- Delete: `components/knarr/src/watchers/github_watcher.py`
- Delete: `components/knarr/tests/test_github_watcher.py`

- [ ] **Step 1: Inspect the legacy github_watcher to know what behaviour to preserve**

Run: `cat components/knarr/src/watchers/github_watcher.py`
Expected: see the `/notifications` polling, repo filter, parsing of `repository.full_name` + `subject.title` + `subject.type`. **Preserve this exact behaviour for Phase 1 — the `/notifications`-vs-repo-side decision per the design doc is a Phase 3+ concern, not a Phase 1 concern.**

- [ ] **Step 2: Write the failing test**

Create `tests/test_adapters_github.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from src.watchers.adapters.github_api import GitHubApiAdapter


_SAMPLE_NOTIFICATIONS = [
    {
        "id": "1001",
        "updated_at": "2026-05-31T11:55:00Z",
        "repository": {"full_name": "MovingBlocks/Terasology"},
        "subject": {
            "type": "Issue",
            "title": "Some bug",
            "url": "https://api.github.com/repos/MovingBlocks/Terasology/issues/42",
        },
    },
    {
        "id": "1002",
        "updated_at": "2026-05-31T11:50:00Z",
        "repository": {"full_name": "MovingBlocks/Terasology"},
        "subject": {
            "type": "PullRequest",
            "title": "Fix the thing",
            "url": "https://api.github.com/repos/MovingBlocks/Terasology/pulls/43",
        },
    },
    {
        "id": "1003",
        "updated_at": "2026-05-31T11:45:00Z",
        "repository": {"full_name": "SomeOther/Repo"},   # not in configured repos
        "subject": {
            "type": "Issue",
            "title": "Off-topic",
            "url": "https://api.github.com/repos/SomeOther/Repo/issues/1",
        },
    },
]


@pytest.mark.asyncio
async def test_first_fetch_returns_filtered_alerts():
    adapter = GitHubApiAdapter(
        instance_id="github-terasology",
        scope="community/terasology",
        repos=["MovingBlocks/Terasology"],
        token=None,
    )
    with patch.object(adapter, "_http_get",
                      new=AsyncMock(return_value=_SAMPLE_NOTIFICATIONS)):
        alerts, cursor = await adapter.fetch(None)

    # SomeOther/Repo filtered out
    assert len(alerts) == 2
    assert {a.event_id for a in alerts} == {"1001", "1002"}
    # Cursor is the highest notification id seen (most recent)
    assert cursor == "1001"

    issue = next(a for a in alerts if a.event_id == "1001")
    assert issue.platform == "github"
    assert issue.access_path == "api"
    assert issue.instance_id == "github-terasology"
    assert issue.scope == "community/terasology"
    assert issue.content.type == "issue"
    assert issue.content.title == "Some bug"
    # raw_post_ref converted from API URL to web URL
    assert "github.com/MovingBlocks/Terasology/issues/42" in issue.raw_post_ref


@pytest.mark.asyncio
async def test_pullrequest_type_normalized():
    adapter = GitHubApiAdapter(
        instance_id="g", scope="community/x",
        repos=["MovingBlocks/Terasology"], token=None,
    )
    with patch.object(adapter, "_http_get",
                      new=AsyncMock(return_value=_SAMPLE_NOTIFICATIONS)):
        alerts, _ = await adapter.fetch(None)

    pr = next(a for a in alerts if a.event_id == "1002")
    assert pr.content.type == "pull_request"
    assert "pull/43" in pr.raw_post_ref  # /pulls/ → /pull/ in web URL


@pytest.mark.asyncio
async def test_cursor_filter_excludes_seen():
    adapter = GitHubApiAdapter(
        instance_id="g", scope="community/x",
        repos=["MovingBlocks/Terasology"], token=None,
    )
    with patch.object(adapter, "_http_get",
                      new=AsyncMock(return_value=_SAMPLE_NOTIFICATIONS)):
        alerts, cursor = await adapter.fetch("1001")

    # 1001 is the cursor; nothing newer in the fixture (1002 < 1001 lexicographic? actually 1001 IS smaller as string; use int comparison in impl)
    # 1002 is older than 1001; 1001 is the newest. With cursor=1001 nothing new.
    assert alerts == []
    assert cursor == "1001"


@pytest.mark.asyncio
async def test_token_passed_in_authorization_header():
    """When a token is configured, it's sent as Bearer auth."""
    adapter = GitHubApiAdapter(
        instance_id="g", scope="community/x",
        repos=["MovingBlocks/Terasology"], token="ghp_FAKETOKEN",
    )
    captured = {}

    async def fake_get(url, headers=None):
        captured["headers"] = headers or {}
        return []

    with patch.object(adapter, "_http_get_raw", new=AsyncMock(side_effect=fake_get)):
        await adapter.fetch(None)
    assert captured["headers"].get("Authorization") == "Bearer ghp_FAKETOKEN"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd components/knarr && python3 -m pytest tests/test_adapters_github.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement the adapter**

Create `src/watchers/adapters/github_api.py`:

```python
"""GitHub API adapter — polls the authenticated user's /notifications.

Replaces `src/watchers/github_watcher.py`. Behaviour matches the legacy
implementation: read the unified notifications inbox, filter to the
configured repo list, emit one WatchAlert per notification.

The repo-side polling alternative (issues/PRs/discussions endpoints per
repo) is a separate decision from Phase 3+ of the source-identity work;
this adapter preserves the existing notifications-based behaviour.
"""

import logging

import httpx

from src.watchers.schemas import Content, WatchAlert

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"


class GitHubApiAdapter:
    """Polls GET /notifications; filters by configured repos.

    Cursor is the notification id (string of digits) of the most-recent
    notification seen. GitHub returns notifications sorted by updated_at
    desc; we use the id of the first row as the cursor.
    """

    def __init__(
        self,
        instance_id: str,
        scope: str,
        repos: list[str],
        token: str | None,
    ):
        self.instance_id = instance_id
        self.scope = scope
        self.repos = set(repos)
        self.token = token

    async def _http_get_raw(self, url: str, headers: dict | None = None) -> list[dict]:
        """Indirection point so token-header tests can intercept."""
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers or {})
            response.raise_for_status()
            return response.json()

    async def _http_get(self, url: str) -> list[dict]:
        """Auth-aware GET. Adds Bearer token when configured."""
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return await self._http_get_raw(url, headers=headers)

    @staticmethod
    def _normalize_type(github_subject_type: str) -> str:
        """Map GitHub's subject.type to our content.type vocabulary."""
        mapping = {
            "Issue": "issue",
            "PullRequest": "pull_request",
            "Release": "release",
            "Commit": "commit",
            "Discussion": "discussion",
        }
        return mapping.get(github_subject_type, github_subject_type.lower())

    @staticmethod
    def _api_url_to_web_url(api_url: str) -> str:
        """Convert api.github.com/repos/o/r/pulls/N → github.com/o/r/pull/N."""
        return (api_url
                .replace("api.github.com/repos/", "github.com/")
                .replace("/pulls/", "/pull/"))

    async def fetch(
        self, since_cursor: str | None
    ) -> tuple[list[WatchAlert], str | None]:
        url = f"{_GITHUB_API}/notifications?participating=false&all=false"
        notifications = await self._http_get(url)

        alerts: list[WatchAlert] = []
        newest_seen: str | None = None

        for notif in notifications:
            notif_id = notif["id"]
            if newest_seen is None:
                newest_seen = notif_id

            if since_cursor is not None and notif_id == since_cursor:
                break

            repo_full = notif["repository"]["full_name"]
            if repo_full not in self.repos:
                continue

            subject = notif["subject"]
            subject_type = subject["type"]
            title = subject["title"]
            api_url = subject.get("url", "")
            web_url = self._api_url_to_web_url(api_url) if api_url else ""

            alerts.append(WatchAlert(
                event_id=notif_id,
                instance_id=self.instance_id,
                scope=self.scope,
                access_path="api",
                platform="github",
                raw_post_ref=web_url,
                content=Content(
                    type=self._normalize_type(subject_type),
                    title=title,
                    body="",  # /notifications doesn't include body
                    author=repo_full,  # best we have without an extra request
                    attachments=[],
                ),
                timestamp=notif["updated_at"],
            ))

        cursor = newest_seen if newest_seen is not None else since_cursor
        return alerts, cursor
```

- [ ] **Step 5: Delete the legacy github_watcher**

Run:

```bash
rm components/knarr/src/watchers/github_watcher.py
rm components/knarr/tests/test_github_watcher.py
```

- [ ] **Step 6: Run test to verify the adapter tests pass**

Run: `cd components/knarr && python3 -m pytest tests/test_adapters_github.py -v`
Expected: all 4 tests pass.

- [ ] **Step 7: Lint passes**

Run: `cd components/knarr && python3 -m ruff check src/watchers/adapters/github_api.py tests/test_adapters_github.py`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

Write `.commits/task6-github-adapter.md`:

```markdown
---
message: "refactor(watchers): port github watcher to GitHubApiAdapter"
add:
  - src/watchers/adapters/github_api.py
  - tests/test_adapters_github.py
---

Same `/notifications` polling + repo-filter behaviour as the legacy
github_watcher; new shape:
- Implements the Adapter Protocol
- Cursor is the most-recent notification id
- Maps GitHub subject.type (Issue, PullRequest, Release, Discussion)
  to our content.type vocabulary
- Converts api.github.com URLs to web URLs in raw_post_ref
- Token (when configured) sent as Bearer auth

Legacy github_watcher.py removed in this commit (or a follow-up if
ws commit doesn't handle delete frontmatter).
```

Run: `bash scripts/ws commit knarr .commits/task6-github-adapter.md`

If `ws commit` skipped the deletes, run the cleanup commit:

```bash
cd components/knarr && git rm src/watchers/github_watcher.py tests/test_github_watcher.py
git -C components/knarr commit -m "refactor(watchers): remove legacy github_watcher (replaced by GitHubApiAdapter)"
```

---

## Task 7 — Rewrite run.py to load instances from config

**Files:**
- Modify: `components/knarr/src/watchers/run.py` (full rewrite)
- Test: handled via integration smoke test in Task 11; this task's commit lands the new entry point.

- [ ] **Step 1: Replace run.py with the instance-loading version**

Replace the entire contents of `src/watchers/run.py` with:

```python
"""Watcher runner — loads instance configs and polls each on its own cadence.

Replaces the hardcoded reddit+github wiring with a config-driven instance
list. See docs/plans/2026-05-30-knarr-source-identity-design.md.

Environment variables:
    KAFKA_BOOTSTRAP    Kafka bootstrap servers (required)
    KAFKA_TOPIC        Target topic (default: knarr.watch.alerts)
    KNARR_CONFIG_PATH  Path to the Knarr config YAML
                       (default: /etc/knarr/config.yaml)

Credentials are looked up from environment variables named in each
instance's `credentials_ref.secret_key`, which K8s mounts into the pod
via secretKeyRef. Adapter classes for the config's `platform` x
`access_path` are resolved by the dispatch table in this file.
"""

import asyncio
import logging
import os
import signal

from confluent_kafka import Producer

from src.admin.config_schema import InstanceConfig, load_config
from src.watchers.adapters.github_api import GitHubApiAdapter
from src.watchers.adapters.reddit_api import RedditApiAdapter
from src.watchers.instance import WatcherInstance

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class CredentialError(RuntimeError):
    """An instance declared credentials_ref but the secret is missing at runtime."""


def _resolve_credential(ref: dict | None, instance_id: str) -> str | None:
    """Read the secret value from the env var named by ref.secret_key.

    K8s mounts the secret via valueFrom.secretKeyRef into an env var. The
    env var name matches the secret's key field. Returns None if ref is
    None (instance requires no auth).

    Fails fast when a credentials_ref IS configured but the env var is
    unset or empty — silently degrading to anonymous calls masks real
    secret-wiring mistakes (e.g., the secretKeyRef points at a Secret
    that doesn't exist, or the Secret key was renamed).
    """
    if ref is None:
        return None
    env_var = ref.get("secret_key")
    if not env_var:
        raise CredentialError(
            f"instance={instance_id}: credentials_ref configured but "
            f"secret_key missing from the ref dict ({ref!r})"
        )
    value = os.environ.get(env_var)
    if not value:
        raise CredentialError(
            f"instance={instance_id}: credentials_ref names env var "
            f"{env_var!r} but it is unset or empty. Check the pod's "
            f"secretKeyRef wiring against Secret "
            f"{ref.get('secret_name')!r}."
        )
    return value


def build_adapter(config: InstanceConfig):
    """Dispatch (platform, access_path) → concrete Adapter constructor."""
    p = config.platform
    a = config.access_path
    if p == "reddit" and a == "api":
        return RedditApiAdapter(
            instance_id=config.id,
            scope=config.scope,
            subreddit=config.platform_config["subreddit"],
        )
    if p == "github" and a == "api":
        return GitHubApiAdapter(
            instance_id=config.id,
            scope=config.scope,
            repos=config.platform_config["repos"],
            token=_resolve_credential(config.credentials_ref, config.id),
        )
    raise ValueError(
        f"No adapter registered for platform={p!r} access_path={a!r} "
        f"(instance={config.id!r}). Phase 1 supports reddit/api and github/api."
    )


def build_instances(config_path: str, producer: Producer,
                    kafka_topic: str) -> list[WatcherInstance]:
    """Load config, build adapters, wrap in WatcherInstances."""
    knarr_config = load_config(config_path)
    instances: list[WatcherInstance] = []
    for inst_cfg in knarr_config.instances:
        adapter = build_adapter(inst_cfg)
        instances.append(WatcherInstance(
            config=inst_cfg,
            adapter=adapter,
            producer=producer,
            kafka_topic=kafka_topic,
        ))
    return instances


async def _poll_loop(instance: WatcherInstance, stop_event: asyncio.Event):
    """Per-instance poll loop. Sleeps interval between cycles; bails on stop."""
    while not stop_event.is_set():
        try:
            await instance.poll_once()
        except Exception:
            logger.exception("instance=%s poll cycle failed", instance.id)
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=instance.interval_seconds,
            )
        except asyncio.TimeoutError:
            continue  # interval elapsed; loop


async def main():
    kafka_bootstrap = os.environ["KAFKA_BOOTSTRAP"]
    kafka_topic = os.environ.get("KAFKA_TOPIC", "knarr.watch.alerts")
    config_path = os.environ.get("KNARR_CONFIG_PATH", "/etc/knarr/config.yaml")

    producer = Producer({"bootstrap.servers": kafka_bootstrap})
    instances = build_instances(config_path, producer, kafka_topic)

    if not instances:
        logger.warning("no instances configured at %s; nothing to poll", config_path)
        return

    logger.info(
        "watcher started — %d instance(s): %s",
        len(instances),
        ", ".join(i.id for i in instances),
    )

    stop_event = asyncio.Event()

    def handle_signal(signum, frame):
        logger.info("signal %s received; shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    await asyncio.gather(
        *[_poll_loop(inst, stop_event) for inst in instances]
    )


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Lint passes**

Run: `cd components/knarr && python3 -m ruff check src/watchers/run.py`
Expected: `All checks passed!`

- [ ] **Step 3: Verify the existing test suite still passes (modulo router test which is intentionally still broken until Task 8)**

Run: `cd components/knarr && python3 -m pytest tests/test_instance.py tests/test_adapters_reddit.py tests/test_adapters_github.py tests/test_schemas.py tests/test_config_schema.py tests/test_adapters_api.py -v`
Expected: all pass.

- [ ] **Step 4: Commit**

Write `.commits/task7-run-loop.md`:

```markdown
---
message: "refactor(watchers): rewrite run.py to load instances from config"
add:
  - src/watchers/run.py
---

Replaces the hardcoded reddit+github wiring with config-driven instance
loading. Each instance gets its own asyncio poll loop using its
configured interval_seconds. Credentials come from env vars named by
the instance's credentials_ref.secret_key (which K8s mounts via
secretKeyRef).

Adapter dispatch is a small `build_adapter` function keyed on
(platform, access_path). Phase 1 supports reddit/api and github/api;
new (platform, access_path) pairs land in later phases by extending
this dispatch.

The new config path defaults to /etc/knarr/config.yaml — k8s manifest
update in a follow-up task mounts the ConfigMap there.
```

Run: `bash scripts/ws commit knarr .commits/task7-run-loop.md`

---

## Task 8 — Update router to consume the new envelope

**Files:**
- Modify: `components/knarr/src/router/kafka_consumer.py`
- Modify: `components/knarr/tests/test_router.py`

- [ ] **Step 1: Inspect the current router code**

Run: `cat components/knarr/src/router/kafka_consumer.py`
Expected: see how `format_alert_message` consumes the old `Source/Content` shape. Note what fields it uses for the Matrix message text.

- [ ] **Step 2: Rewrite the router test to use the new envelope**

Replace `tests/test_router.py` entirely with:

```python
from src.router.kafka_consumer import deserialize_alert, format_alert_message
from src.watchers.schemas import Attachment, Content, WatchAlert


def _reddit_alert() -> WatchAlert:
    return WatchAlert(
        event_id="t3_post1",
        instance_id="reddit-terasology",
        scope="community/terasology",
        access_path="api",
        platform="reddit",
        raw_post_ref="https://www.reddit.com/r/Terasology/comments/post1/title/",
        content=Content(
            type="post",
            title="First post",
            body="Hello world",
            author="cervator",
            attachments=[],
        ),
        timestamp="2026-05-31T12:00:00+00:00",
    )


def _github_alert() -> WatchAlert:
    return WatchAlert(
        event_id="1001",
        instance_id="github-terasology",
        scope="community/terasology",
        access_path="api",
        platform="github",
        raw_post_ref="https://github.com/MovingBlocks/Terasology/issues/42",
        content=Content(
            type="issue",
            title="Some bug",
            body="",
            author="MovingBlocks/Terasology",
            attachments=[
                Attachment(url="https://github.com/.../screen.png",
                           mime_hint="image/png"),
            ],
        ),
        timestamp="2026-05-31T12:00:00+00:00",
    )


def test_deserialize_round_trips_new_envelope():
    original = _reddit_alert()
    data = original.to_kafka_dict()
    rebuilt = deserialize_alert(data)
    assert rebuilt == original


def test_format_reddit_alert_includes_title_author_and_link():
    msg = format_alert_message(_reddit_alert())
    assert "First post" in msg
    assert "cervator" in msg
    assert "https://www.reddit.com/r/Terasology/comments/post1/title/" in msg


def test_format_github_alert_includes_type_title_and_repo():
    msg = format_alert_message(_github_alert())
    assert "Some bug" in msg
    # type or repo appears so a reader can see this is a github issue
    assert "issue" in msg.lower()
    assert "MovingBlocks/Terasology" in msg
    assert "https://github.com/MovingBlocks/Terasology/issues/42" in msg
```

- [ ] **Step 3: Run tests to verify failure**

Run: `cd components/knarr && python3 -m pytest tests/test_router.py -v`
Expected: tests fail because `deserialize_alert` / `format_alert_message` still consume the old `Source/Content` shape.

- [ ] **Step 4: Rewrite kafka_consumer.py for the new shape**

Replace `src/router/kafka_consumer.py` with:

```python
"""Router-side Kafka consumer helpers.

Phase 1: thin pass-through — read one event, format one Matrix message,
post it. Phase 2 introduces presentation_mode (summarised vs raw-threaded)
along with the AI summarize stage.
"""

from src.watchers.schemas import WatchAlert


def deserialize_alert(data: dict) -> WatchAlert:
    return WatchAlert.from_kafka_dict(data)


def format_alert_message(alert: WatchAlert) -> str:
    """Format a WatchAlert as a single Matrix message (Phase 1 behaviour).

    Shape: short header line with platform + type + title, then author/source
    on the next line, then the link. Body preview included when present
    (capped at ~200 chars to avoid eating channel real-estate).
    """
    c = alert.content
    header = f"[{alert.platform}] {c.type}: {c.title}".strip()

    parts = [header]
    if c.author:
        parts.append(f"by {c.author}")
    if c.body:
        preview = c.body[:200] + ("…" if len(c.body) > 200 else "")
        parts.append(preview)
    parts.append(alert.raw_post_ref)

    return "\n".join(parts)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd components/knarr && python3 -m pytest tests/test_router.py -v`
Expected: all 3 tests pass.

- [ ] **Step 6: Run full unit suite — should be green now**

Run: `cd components/knarr && python3 -m pytest tests/ --ignore=tests/features -v`
Expected: all tests pass. BDD tests still pending (Task 10).

- [ ] **Step 7: Lint passes**

Run: `cd components/knarr && python3 -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

Write `.commits/task8-router-new-envelope.md`:

```markdown
---
message: "refactor(router): consume the new WatchAlert envelope"
add:
  - src/router/kafka_consumer.py
  - tests/test_router.py
---

Phase 1 router stays a thin pass-through — format one Matrix message
per WatchAlert, post it. presentation_mode (summarised vs raw-threaded)
arrives with the AI summarize stage in Phase 2.

Message shape: `[platform] type: title` header, optional `by author`,
optional ~200-char body preview, raw_post_ref link.
```

Run: `bash scripts/ws commit knarr .commits/task8-router-new-envelope.md`

---

## Task 9 — Rewrite config/test.yaml into the instance shape

**Files:**
- Modify: `components/knarr/config/test.yaml`

- [ ] **Step 1: Read the current config to know what to preserve**

Run: `cat components/knarr/config/test.yaml`
Expected: see the current `rooms.<room>.watchers:` block structure with reddit + github configured inline on `social-watch`.

- [ ] **Step 2: Rewrite test.yaml**

Replace the entire contents of `components/knarr/config/test.yaml` with:

```yaml
community: knarr-test
display_name: "Knarr Test"

# NOTE: Room members are NOT inherited from parent spaces. Each room
# must list every user that should be a member.
#
# Phase 1 watcher refactor: watchers are no longer inline under rooms
# (see docs/plans/2026-05-30-knarr-source-identity-design.md). Instead,
# the top-level `instances:` block declares (platform, access_path,
# scope, target_room) tuples. The reconciler still creates the rooms
# below; the watcher process reads `instances:` from the merged config
# and spins up one WatcherInstance per row.

spaces:
  knarr-test:
    name: "Knarr Test"
    visibility: private
    members: [admin, knarr]

    rooms:
      social-watch:
        alias: social-watch
        name: "#social-watch"
        topic: "Platform monitoring alerts"
        # router posts watcher alerts; admin/knarr are explicit here
        # because the reconciler does not inherit space.members into
        # child rooms.
        members: [admin, knarr, router]

      bridge-test:
        alias: bridge-test
        name: "#autoboros-testing [test]"
        topic: "Bridged to Demicracy Discord #autoboros-testing"
        members: [admin, knarr]
        bridge:
          discord:
            channel_id: "1342947610008485921"
            relay: true

    spaces:
      feeds:
        name: "Feeds"
        visibility: private
        rooms:
          feed-reddit:
            alias: feed-reddit
            name: "#feed-reddit"
            topic: "Reddit watcher alerts"
          feed-github:
            alias: feed-github
            name: "#feed-github"
            topic: "GitHub watcher alerts (issues, PRs, discussions, releases)"
```

Then **add the new top-level `instances:` block to `components/knarr/config/knarr.yaml`** (the index config that references this community config). Open `components/knarr/config/knarr.yaml` and append:

```yaml
instances:
  - id: reddit-terasology
    platform: reddit
    access_path: api
    scope: community/terasology
    polling:
      interval_seconds: 21600
    platform_config:
      subreddit: Terasology
    target_room: social-watch
    # no credentials_ref — Reddit /new.json is anonymous

  - id: github-terasology
    platform: github
    access_path: api
    scope: community/terasology
    polling:
      interval_seconds: 21600
    platform_config:
      repos:
        - MovingBlocks/Terasology
    target_room: social-watch
    credentials_ref:
      secret_name: knarr-cred-community-terasology-gh-pat
      secret_key: GITHUB_TOKEN
```

- [ ] **Step 3: Add a parse-test for the new config shape**

In `tests/test_config_schema.py`, add at the end:

```python
def test_loads_real_test_config_with_instances(tmp_path):
    """Walking the actual config/test.yaml + config/knarr.yaml from this repo
    parses cleanly and produces 2 instances."""
    import pathlib
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    config_path = repo_root / "config" / "knarr.yaml"
    if not config_path.exists():
        import pytest
        pytest.skip(f"{config_path} not present in this checkout")
    parsed = load_config(str(config_path))
    assert len(parsed.instances) == 2
    assert {i.id for i in parsed.instances} == {
        "reddit-terasology", "github-terasology",
    }
    gh = next(i for i in parsed.instances if i.id == "github-terasology")
    assert gh.credentials_ref["secret_key"] == "GITHUB_TOKEN"
    validate_config(parsed)  # must pass validation too
```

- [ ] **Step 4: Run the test**

Run: `cd components/knarr && python3 -m pytest tests/test_config_schema.py::test_loads_real_test_config_with_instances -v`
Expected: passes.

- [ ] **Step 5: Sanity-check the CLI loads the config without error**

Run: `cd components/knarr && bash scripts/knarr config validate --config config/knarr.yaml`
Expected: prints `Config valid: ...` with no errors. (If `bash scripts/knarr` is unavailable in this environment, run `python3 -m src.admin.cli config validate --config config/knarr.yaml`.)

- [ ] **Step 6: Commit**

Write `.commits/task9-config-instances.md`:

```markdown
---
message: "config(test): rewrite watchers as top-level instances list"
add:
  - config/test.yaml
  - config/knarr.yaml
  - tests/test_config_schema.py
---

- config/test.yaml: removes the inline `rooms.social-watch.watchers`
  block. Rooms continue to be declared the same way; watcher behaviour
  moves to the top-level `instances:` list in the index config.
- config/knarr.yaml: adds the `instances:` list with two rows
  (reddit-terasology, github-terasology) preserving today's polling
  cadence and target room.
- New parse-test confirms the real config files parse cleanly + pass
  validation.
```

Run: `bash scripts/ws commit knarr .commits/task9-config-instances.md`

---

## Task 10 — Update BDD lifecycle test for the new shape

**Files:**
- Read: `components/knarr/tests/features/reconcile.feature`
- Modify (if needed): `components/knarr/tests/features/steps/reconcile_steps.py`

- [ ] **Step 1: Inspect the BDD feature file and steps**

Run: `cat components/knarr/tests/features/reconcile.feature components/knarr/tests/features/steps/reconcile_steps.py | head -200`
Expected: read through to confirm what assertions the test makes. The lifecycle expects rooms to exist and a clean audit; nothing in the feature file directly mentions the watcher schema, so the BDD test should largely just work after Task 9's config rewrite. Two-room creation may have changed (was 3 rooms — social-watch, bridge-test, feed-reddit, feed-github — that count is unchanged).

- [ ] **Step 2: Verify BDD reads our updated config**

Run: (with k3d-nordri-test running) `cd components/knarr && env $(grep -v '^#' knarr.env | xargs) python3 -m pytest tests/features/steps/reconcile_steps.py --override-ini=testpaths= -v`
Expected: 2 scenarios pass. **If dangling-alias flake fires** (per the known issue in the `bdd-test-reliability` arc), manually purge with `.tmp/purge_dangling_aliases.py` and retry.

- [ ] **Step 3: If the BDD test fails for non-flake reasons, fix the failure**

The most likely cause is an assertion that references the old config shape (e.g. checking that watchers landed on rooms). Adjust the assertion to match the new instance-list shape. **If no failure**, skip directly to Step 5 — no commit needed.

- [ ] **Step 4: Commit the BDD adjustments (only if Step 3 made changes)**

Write `.commits/task10-bdd-adjustments.md`:

```markdown
---
message: "test(bdd): adjust reconcile lifecycle for instance-list config shape"
add:
  - tests/features/steps/reconcile_steps.py
---

Mechanical adjustment for the Phase 1 config rewrite (instances are
top-level now, rooms no longer carry inline watcher blocks). Lifecycle
assertions otherwise unchanged.
```

Run: `bash scripts/ws commit knarr .commits/task10-bdd-adjustments.md`

- [ ] **Step 5: Mark BDD validated**

Note in your local notes: BDD lifecycle green against k3d. Move on.

---

## Task 11 — Update K8s manifest for ConfigMap-driven instances

**Files:**
- Modify (rename): `components/knarr/k8s/watchers/reddit-github.yaml` → `components/knarr/k8s/watchers/watchers.yaml`
- Modify: any kustomization or apply-time script that references the old filename.

- [ ] **Step 1: Inspect the current deployment manifest**

Run: `cat components/knarr/k8s/watchers/reddit-github.yaml`
Expected: see the Deployment with the hardcoded env vars (REDDIT_SUBREDDIT, GITHUB_REPOS, etc.).

- [ ] **Step 2: Replace the deployment with the new shape**

Write the new content to `components/knarr/k8s/watchers/reddit-github.yaml` (replacing the file contents — we'll rename after):

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: knarr-watcher-config
  namespace: knarr
data:
  # config/knarr.yaml + config/test.yaml merged into one document for
  # the watcher pod. Apply this ConfigMap from a config artifact
  # generated by the reconciler at apply time, or maintain it manually
  # for now. Phase 1: maintained manually here; matches the in-tree
  # config/knarr.yaml + config/test.yaml content.
  knarr.yaml: |
    server_name: knarr.local

    secrets:
      discord_token: DISCORD_TOKEN
      github_token: GITHUB_TOKEN

    users:
      admin: "@admin:knarr.local"
      knarr: "@knarr:knarr.local"
      router: "@knarr-router:knarr.local"
      bridge_bot: "@discordbot:knarr.local"

    # Path is relative to knarr.yaml's directory inside the pod
    # (/etc/knarr/). Both knarr.yaml and test.yaml are mounted as flat
    # keys of the same ConfigMap, so the community path here is the
    # bare filename, NOT `config/test.yaml`. The in-tree
    # `config/knarr.yaml` uses `config/test.yaml` because the on-disk
    # layout differs from the pod's mount layout — that's expected.
    communities:
      - test.yaml

    instances:
      - id: reddit-terasology
        platform: reddit
        access_path: api
        scope: community/terasology
        polling:
          interval_seconds: 21600
        platform_config:
          subreddit: Terasology
        target_room: social-watch

      - id: github-terasology
        platform: github
        access_path: api
        scope: community/terasology
        polling:
          interval_seconds: 21600
        platform_config:
          repos:
            - MovingBlocks/Terasology
        target_room: social-watch
        credentials_ref:
          secret_name: knarr-cred-community-terasology-gh-pat
          secret_key: GITHUB_TOKEN

  # community config carried alongside under the same /etc/knarr/
  # mount so load_config()'s community_loader can find `test.yaml`
  # relative to knarr.yaml's directory.
  test.yaml: |
    community: knarr-test
    display_name: "Knarr Test"
    spaces:
      knarr-test:
        name: "Knarr Test"
        visibility: private
        members: [admin, knarr]
        rooms:
          social-watch:
            alias: social-watch
            name: "#social-watch"
            topic: "Platform monitoring alerts"
            members: [admin, knarr, router]
        spaces:
          feeds:
            name: "Feeds"
            visibility: private
            rooms:
              feed-reddit:
                alias: feed-reddit
                name: "#feed-reddit"
                topic: "Reddit watcher alerts"
              feed-github:
                alias: feed-github
                name: "#feed-github"
                topic: "GitHub watcher alerts"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: knarr-watchers
  namespace: knarr
  labels:
    app: knarr-watchers
spec:
  replicas: 1
  selector:
    matchLabels:
      app: knarr-watchers
  template:
    metadata:
      labels:
        app: knarr-watchers
    spec:
      containers:
        - name: watchers
          image: knarr-watchers:dev
          imagePullPolicy: Never
          env:
            - name: KAFKA_BOOTSTRAP
              value: "knarr-kafka-8r9tn-kafka-bootstrap.kafka.svc.cluster.local:9092"
            - name: KAFKA_TOPIC
              value: "knarr.watch.alerts"
            - name: KNARR_CONFIG_PATH
              value: "/etc/knarr/knarr.yaml"
            # Credentials referenced by instance configs land here as env vars,
            # one per credentials_ref.secret_key. The watcher pod resolves them
            # via os.environ at instance build time and fails fast (per Task 7's
            # _resolve_credential) when a credentials_ref is configured but the
            # env var is unset — so the Secret here must exist. Operators who
            # want anonymous github polling instead should remove the
            # credentials_ref block from the github instance in the embedded
            # knarr.yaml above, AND remove this secretKeyRef.
            - name: GITHUB_TOKEN
              valueFrom:
                secretKeyRef:
                  name: knarr-cred-community-terasology-gh-pat
                  key: GITHUB_TOKEN
          volumeMounts:
            - name: config
              mountPath: /etc/knarr
              readOnly: true
          resources:
            requests:
              cpu: 25m
              memory: 64Mi
            limits:
              memory: 128Mi
      volumes:
        - name: config
          configMap:
            name: knarr-watcher-config
```

- [ ] **Step 3: Validate the manifest YAML parses**

Run: `kubectl --dry-run=client apply -f components/knarr/k8s/watchers/reddit-github.yaml`
Expected: `configmap/knarr-watcher-config created (dry run)` + `deployment.apps/knarr-watchers configured (dry run)`. No YAML errors.

- [ ] **Step 4: Commit**

Write `.commits/task11-k8s-configmap.md`:

```markdown
---
message: "k8s(watchers): drive watcher pod from a ConfigMap'd knarr.yaml"
add:
  - k8s/watchers/reddit-github.yaml
---

The watcher Deployment used to take all config via env vars
(REDDIT_SUBREDDIT, GITHUB_REPOS). Phase 1 moves that into a ConfigMap
mounted at /etc/knarr — the same knarr.yaml + test.yaml the CLI
consumes. Watcher pod loads the instance list via load_config() and
spins up the right adapters.

GITHUB_TOKEN now wired via secretKeyRef. The Secret is required at
pod startup — Task 7's `_resolve_credential` fails fast when an
instance declares `credentials_ref` but the named env var is unset,
to surface secret-wiring mistakes rather than silently degrade to
anonymous calls. If an operator wants anonymous github polling
(low rate limits but no secret to manage), they remove both the
`credentials_ref` block from the github instance in the embedded
knarr.yaml AND the corresponding secretKeyRef entry from this pod's
env list.

The Phase 0 follow-up to generate the ConfigMap from the source
config/knarr.yaml automatically (rather than hand-duplicating it here)
is deferred — see the source-identity arc for when this gets
reconciler-driven.
```

Run: `bash scripts/ws commit knarr .commits/task11-k8s-configmap.md`

---

## Task 12 — Live-cluster end-to-end validation

**No commits in this task — purely validation.**

- [ ] **Step 1: Confirm k3d-nordri-test is up and Synapse healthy**

Run: `kubectl --context k3d-nordri-test get pods -n knarr`
Expected: `synapse-...` is Running. If not, repair the cluster before continuing (out of scope for this plan; see `docs/troubleshooting.md`).

- [ ] **Step 2: Build the new watcher image**

Run: `cd components/knarr && docker build -f src/watchers/Dockerfile -t knarr-watchers:dev .`
Expected: image builds cleanly.

- [ ] **Step 3: Import the image into k3d**

Run: `k3d image import knarr-watchers:dev -c nordri-test`
Expected: image imported.

- [ ] **Step 4: Apply the new ConfigMap + Deployment**

Run: `kubectl --context k3d-nordri-test apply -f components/knarr/k8s/watchers/reddit-github.yaml`
Expected: `configmap/knarr-watcher-config configured`, `deployment.apps/knarr-watchers configured`.

- [ ] **Step 5: Roll out the new pod**

Run: `kubectl --context k3d-nordri-test rollout restart deploy/knarr-watchers -n knarr && kubectl --context k3d-nordri-test rollout status deploy/knarr-watchers -n knarr`
Expected: `deployment "knarr-watchers" successfully rolled out`.

- [ ] **Step 6: Check the new pod's logs for instance startup**

Run: `kubectl --context k3d-nordri-test logs -n knarr -l app=knarr-watchers --tail=50`
Expected: `watcher started — 2 instance(s): reddit-terasology, github-terasology`.

- [ ] **Step 7: Trigger a fresh Reddit poll to verify end-to-end emit**

Wait for the next poll cycle (or temporarily reduce `interval_seconds` to 60 in the ConfigMap, apply, and wait 60s) — confirm via logs that `published=N` lines appear for `instance=reddit-terasology`.

- [ ] **Step 8: Confirm Matrix message arrives in #social-watch**

Open Element pointed at `http://matrix.knarr.local`, view `#social-watch:knarr.local`. Expected: a new message in the Phase 1 router shape (`[reddit] post: <title>\nby <author>\n<body preview>\n<link>`).

- [ ] **Step 9: Reset polling interval if you reduced it**

If Step 7 required interval tuning, restore `interval_seconds: 21600` in the ConfigMap, apply, and rollout-restart.

- [ ] **Step 10: Make a note for the PR description**

Capture in the eventual CR body: "Phase 1 refactor validated end-to-end against k3d-nordri-test; reddit-terasology + github-terasology instances start cleanly, fresh post landed in #social-watch in the expected message shape."

---

## Task 13 — Update docs

**Files:**
- Modify: `components/knarr/docs/architecture.md`
- Modify: `components/knarr/docs/operations.md`

- [ ] **Step 1: Update the architecture doc**

Open `components/knarr/docs/architecture.md`. Find the section that describes the watcher layer (currently talks about reddit_watcher / github_watcher). Update it to describe the WatcherInstance model. Suggested addition (place near the existing "Layer 3: Agents" or equivalent section):

```markdown
### Watcher instances (since Phase 1 of source-identity refactor)

The watcher pod hosts one or more `WatcherInstance` objects, each
configured by a row in the top-level `instances:` list of the merged
knarr config. An instance is the tuple
`(platform, access_path, scope, target_room, credentials_ref, polling)`;
each runs its own poll cadence and emits to Kafka with full envelope
metadata.

The `Adapter` Protocol (`src/watchers/adapters/__init__.py`) is the
platform seam. Today Phase 1 ships `RedditApiAdapter` and
`GitHubApiAdapter`; later phases add `AdminAppAdapter`, `ScrapeAdapter`,
and `EmailExtractAdapter` against the same Protocol.

See `docs/plans/2026-05-30-knarr-source-identity-design.md` for the
broader design.
```

- [ ] **Step 2: Update operations doc with the new troubleshooting trio**

Open `components/knarr/docs/operations.md`. Find the rebuild section and add (or update) the instance-related notes:

```markdown
### Adding a new watcher instance (Phase 1+)

To add a new platform polling instance:

1. Append a new row to `instances:` in `config/knarr.yaml`.
2. If the platform needs credentials, create the K8s secret:
   `kubectl create secret generic knarr-cred-<scope>-<purpose> --from-literal=<KEY>=<value> -n knarr`
3. Add a `secretKeyRef` env entry to `k8s/watchers/reddit-github.yaml`
   pointing at the new secret + key (matching `credentials_ref.secret_key`
   in the instance config).
4. Update the embedded ConfigMap data in the same manifest to mirror
   the new `instances:` row (Phase 0 follow-up will automate this).
5. `kubectl apply -f k8s/watchers/reddit-github.yaml` + rollout-restart.

Each instance gets its own asyncio poll loop on the configured
`interval_seconds`. Logs are tagged with `instance=<id>` so you can
filter per-instance with `kubectl logs ... | grep instance=<id>`.
```

- [ ] **Step 3: Lint-check the markdown roughly**

Run: `cd components/knarr && bash scripts/ws exec knarr 'echo ok'` (no markdown linter wired; eyeball the diffs to confirm fences are balanced and links resolve).

- [ ] **Step 4: Commit**

Write `.commits/task13-docs.md`:

```markdown
---
message: "docs: describe the WatcherInstance model + add-an-instance operations"
add:
  - docs/architecture.md
  - docs/operations.md
---

- architecture.md: new section describing the WatcherInstance +
  Adapter Protocol model. References the source-identity design doc
  for full context.
- operations.md: how to add a new watcher instance (manual ConfigMap
  duplication noted as a Phase 0 follow-up to automate via the
  reconciler).
```

Run: `bash scripts/ws commit knarr .commits/task13-docs.md`

---

## Task 14 — Branch hygiene + open the PR

- [ ] **Step 1: Confirm all tasks are committed on the right branch**

Run: `cd components/knarr && git log --oneline main..HEAD`
Expected: ~12-13 commits (one per task except Task 12 which has no commits). If the work was done directly on main, see "If you committed to main" recovery below.

**If you committed to main** (branch-protected, push will be rejected — same trap as PR #4):
```
cd components/knarr
git branch design/source-identity-phase-1 HEAD
git reset --keep <commit-sha-of-last-merged-main>   # e.g. 85b2d51 unless main has moved
git checkout design/source-identity-phase-1
```

- [ ] **Step 2: Full local test + lint pass**

Run: `cd components/knarr && python3 -m ruff check src/ tests/ && python3 -m pytest tests/ --ignore=tests/features`
Expected: lint clean + all unit tests pass.

- [ ] **Step 3: Push the branch**

Run: `bash scripts/ws push knarr`
Expected: branch pushed; remote prints the PR-create URL.

- [ ] **Step 4: Draft the CR body**

Write `.crs/knarr-source-identity-phase-1.md`:

```markdown
> **AI-assisted change proposal.** Filed by agent driven by @HUMAN_ACCOUNT via [GDD](@GDD_HOME).

## Summary

Implements Phase 1 of the source-identity design
(`docs/plans/2026-05-30-knarr-source-identity-design.md`): refactor
today's reddit_watcher / github_watcher into a multi-tenant
`WatcherInstance` model driven by per-instance config rows. No new
platform, no user-visible behaviour change — pure architectural lift
that unblocks Phase 2 (AI summarize) and Phase 3+ (new sources, scrape
adapters).

### What lands

- `WatcherInstance` runtime + `Adapter` Protocol.
- `RedditApiAdapter`, `GitHubApiAdapter` — port of the legacy watchers.
- New `WatchAlert` envelope (instance_id, scope, access_path,
  raw_post_ref, extracted_at, content.title/author/attachments). Clean
  break — no compat shim since nothing is live.
- Top-level `instances:` list in the knarr config; reconciler config
  schema extends with `InstanceConfig`.
- `run.py` rewritten to load instances from the config and spin up one
  asyncio poll loop per instance.
- K8s manifest moves from env-var-driven to ConfigMap-mounted config.
- Docs updated.

### What's deliberately deferred (per the design doc's Phase 0 split)

- `personal_space_room_id` user attribute + per-user space provisioning
  (no `user/<id>`-scoped instances ship here).
- `knarr cred create | capture | rotate | revoke` CLI.
- Keycloak integration (config-driven users map continues; scope
  identifiers mirror Keycloak's eventual shape).
- AI summarize stage + presentation_mode routing (Phase 2).

## Test plan

- [x] `python3 -m pytest tests/ --ignore=tests/features` — full unit
      suite green.
- [x] `python3 -m ruff check src/ tests/` — lint clean.
- [x] BDD lifecycle (`tests/features/steps/reconcile_steps.py`) green
      against k3d-nordri-test.
- [x] Live cluster validation: reddit-terasology + github-terasology
      instances start cleanly, fresh Reddit post lands in #social-watch
      in the expected `[platform] type: title` shape.

## Related

- Spec: `docs/plans/2026-05-30-knarr-source-identity-design.md`
  (PR #4 — landing or landed).
- Closes Phase 1 of the multi-phase rollout. Phases 2-6 get their own
  plans when their turn comes.
```

- [ ] **Step 5: Open the PR**

Run: `bash scripts/ws cr knarr "feat(watchers): Phase 1 — WatcherInstance refactor" .crs/knarr-source-identity-phase-1.md`
Expected: PR URL printed.

- [ ] **Step 6: Tag the source-identity arc as Phase 1 in flight**

Edit `hoards/thalami-Cervator/rasmuss-mbp-2-thalamus.md`. Update the `knarr-source-identity` arc's `next:` to reference the PR (e.g. `"Phase 1 PR open: <url>. Phase 2 (AI summarize) plan to draft after Phase 1 merges."`). **Do not commit/push the thalamus immediately** — fold into the next cadence push per the cadence convention.

---

## Self-review checklist (run before declaring done)

After all tasks are complete:

- [ ] All 13 task commits on the branch (Task 12 has none).
- [ ] No file references the deleted `Source` dataclass.
- [ ] No file references `reddit_watcher.py` or `github_watcher.py`.
- [ ] `config/knarr.yaml` has the `instances:` block and round-trips through `bash scripts/knarr config validate`.
- [ ] K8s manifest applies cleanly (`kubectl --dry-run=client apply -f ...`).
- [ ] Live k3d cluster shows the new pod healthy and emitting (logs show `published=N`).
- [ ] Matrix `#social-watch` receives a Phase 1-shaped message from a real upstream event.
- [ ] PR open with the test-plan checklist filled in.
