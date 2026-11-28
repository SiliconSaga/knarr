# Knarr Config Reconciler Design

**Date:** 2026-04-25
**Status:** Implemented (knarr#2, 2026-04-28). Map of all Knarr notes: [README](README.md).
**Component:** knarr

> **Where the shipped reconciler differs from this design (checked 2026-11-27):**
> - Watcher settings are reported as `noop` ("apply not yet implemented"). They don't patch the Deployment.
> - `knarr config apply` doesn't log the bridge into Discord. That stays `knarr bridge login-bot`, a manual step.
> - If a managed room loses its alias, it isn't recovered automatically. No `org.knarr.managed` marker scan runs before create.
>
> The [plan](2026-04-25-config-reconciler-plan.md) is kept as written, as the record of how knarr#2 was built. Its sample code isn't maintained against `src/admin/reconciler.py`.

---

## Overview

A GitOps-style configuration reconciler for Knarr. Desired state for Matrix
room topology, bridge mappings, and watcher config lives in version-controlled
YAML files. The reconciler reads this config, diffs it against the live Matrix
homeserver, and applies changes to converge actual state to desired state.

### Goals

1. **Single source of truth** — room hierarchy, bridges, watchers, and
   membership all defined in Git-committed config files
2. **Idempotent apply** — running the reconciler twice produces zero changes
   on the second run
3. **Audit mode** — dry-run that reports drift without making changes
4. **Reproducibility** — wipe all rooms, re-apply, get the same result
5. **Testable** — unit tests with mocked Matrix API, integration tests
   against real Synapse, BDD scenarios for reconciliation behavior
6. **Observable** — structured reports that can eventually feed Grafana/Prometheus

### Non-Goals (v1)

- Periodic/continuous reconciliation (CronJob or controller loop)
- Identity/subscription model (Keycloak, Valkey)
- Routing rules engine (threaded digests, approval workflows)
- Cross-community federation
- Grafana integration (data model supports it, plumbing deferred)

---

## Config File Schema

### Top-level index

`config/knarr.yaml` — references global settings and community config files:

```yaml
server_name: knarr.local

# Secrets read from environment variables at apply time.
# Config documents what's required; values stay in knarr.env or k8s Secrets.
secrets:
  discord_token: DISCORD_TOKEN
  github_token: GITHUB_TOKEN

# Users referenced by shorthand in community configs.
# Not created by the reconciler — must pre-exist.
users:
  admin: "@admin:knarr.local"
  knarr: "@knarr:knarr.local"
  router: "@knarr-router:knarr.local"
  bridge_bot: "@discordbot:knarr.local"

# Community config files, applied in order.
communities:
  - config/test.yaml
```

### Community config

`config/test.yaml` — self-contained per community:

```yaml
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
        members: [router]
        watchers:
          reddit:
            subreddit: Terasology
            interval_seconds: 21600
          github:
            repos: ["MovingBlocks/Terasology"]
            interval_seconds: 21600
            token_env: GITHUB_TOKEN

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
```

### Schema rules

- `alias` becomes `#<alias>:knarr.local` — the stable identifier for
  idempotent room lookup
- `members` reference shorthand names from the index `users` map
- `bridge_bot` is auto-invited when a `bridge` block is present
- `visibility`: `public` (anyone can join) or `private` (invite-only)
- Spaces can nest (spaces within spaces)
- Watchers are declared per-room; the reconciler aggregates them into
  deployment config
- `secrets` entries document required env vars; reconciler validates they
  exist before applying

---

## Room Identity and Idempotency

The reconciler needs to map config entries to existing Matrix rooms across
multiple apply runs.

**Primary: room aliases.** Each room in config has an `alias` field that
becomes `#<alias>:<server_name>`. The reconciler queries
`GET /_matrix/client/v3/directory/room/{alias}` to check existence. Aliases
are human-readable and built into the Matrix protocol.

**Safety net: managed state event.** When the reconciler creates or adopts a
room, it writes a custom state event `org.knarr.managed` containing:

```json
{
  "config_key": "knarr-test/social-watch",
  "managed_by": "knarr-reconciler",
  "last_reconciled": "2026-04-25T12:00:00Z"
}
```

This marks the room as reconciler-managed. Even if an alias is tampered with,
the reconciler can find its rooms by scanning for this state event. It also
prevents the reconciler from accidentally modifying user-created rooms.

---

## Reconciler Architecture

### Pipeline

```text
Load Config → Read Current State → Compute Diff → Apply (or Audit)
```

### Step 1: Load Config

Parse `config/knarr.yaml`, resolve community file references, validate:
- Required fields present
- User references resolve to the `users` map
- Aliases are unique across all community files
- Secret env var references are set in the environment (apply mode only)

Fail fast on validation errors before touching Matrix.

### Step 2: Read Current State

For each alias in the config, query Matrix:
- Does the alias resolve to a room? (directory API)
- If yes: read room name, topic, members, power levels, `org.knarr.managed`
  state event
- For spaces: check child room relationships
- For bridges: check if bridge bot is a member

### Step 3: Compute Diff

Compare desired vs actual for each config entry. Produce a list of `Action`
objects:

```python
@dataclass
class Action:
    resource: str        # "space:knarr-test", "room:social-watch"
    operation: str       # "create", "update", "invite", "bridge", "skip"
    details: str         # human-readable description

@dataclass
class ReconcileReport:
    actions: list[Action]
    errors: list[str]

    @property
    def has_drift(self) -> bool:
        return any(a.operation != "skip" for a in self.actions)
```

### Step 4: Apply

Execute actions in dependency order:
1. **Spaces** (parents before children)
2. **Rooms** (within their parent space)
3. **Membership** (invites to rooms and spaces)
4. **Bridges** (channel mapping + relay webhook)
5. **State events** (`org.knarr.managed` marker)
6. **Watcher config** (aggregate and update k8s deployment)

Audit mode runs steps 1-3 and prints the report without step 4.

Errors during apply are logged per-action. The reconciler continues with
remaining items and reports failures at the end. Re-running apply picks up
where it left off (idempotent).

---

## New MatrixAdminClient Methods

The existing client needs these additions for the reconciler:

| Method | Purpose |
|--------|---------|
| `resolve_alias(alias) → room_id or None` | Check if a room exists by alias |
| `get_room_state(room_id) → dict` | Read room name, topic, join rules |
| `get_room_members(room_id) → list[str]` | List current members |
| `set_room_alias(room_id, alias)` | Assign an alias to a room |
| `create_space(name, alias, ...) → room_id` | Create a space (room with `m.space` type) |
| `add_space_child(space_id, child_id)` | Add room/space as child of a space |
| `set_room_state_event(room_id, type, content)` | Write custom state events |
| `get_room_state_event(room_id, type) → dict or None` | Read custom state events |

---

## Watcher Config Reconciliation

Watchers are declared per-room in config but deployed as a single pod with
env vars. The reconciler aggregates across all community files:

- Collects all `watchers.reddit` entries → `REDDIT_SUBREDDIT` (unique subreddits)
- Collects all `watchers.github` entries → `GITHUB_REPOS` (comma-joined)
- Takes the shortest `interval_seconds` across all entries → `POLL_INTERVAL_SECONDS`
- Resolves `token_env` references → env var names for the deployment

The reconciler generates a k8s deployment patch and applies it via
`kubectl apply`. The watcher pod restarts with updated config.

---

## CLI Interface

```bash
# Apply config — converge live state to match config
knarr config apply [--config config/knarr.yaml]

# Audit — dry-run, report drift without making changes
knarr config audit [--config config/knarr.yaml]

# Validate — check config syntax without touching Matrix
knarr config validate [--config config/knarr.yaml]
```

Default config path: `config/knarr.yaml` (relative to knarr component root).

### Output

Apply:

```text
Loading config: config/knarr.yaml (1 community)
Reading Matrix state...

  space:knarr-test         create   "Knarr Test" (private)
  room:social-watch        create   "#social-watch" in knarr-test
    invite @knarr-router   invite   router bot
    watcher:reddit         config   r/Terasology every 21600s
    watcher:github         config   MovingBlocks/Terasology every 21600s
  room:bridge-test         skip     already exists (#bridge-test:knarr.local)
  space:feeds              create   "Feeds" in knarr-test
    room:feed-reddit       create   "#feed-reddit" in feeds

Applied: 4 created, 1 invite, 2 watcher configs, 1 skipped, 0 errors
```

Audit:

```text
Audit: 4 would be created, 1 invite needed, 2 watcher configs to update, 1 up to date
```

### Error handling

- Config validation errors → fail before touching Matrix, exit code 1
- Matrix API errors during apply → log per-action, continue, report at end,
  exit code 1 if any failures
- Partial success is safe — re-run picks up where it left off

---

## Secrets Handling

Config files reference environment variable names, never secret values:

```yaml
secrets:
  discord_token: DISCORD_TOKEN    # reconciler reads os.environ["DISCORD_TOKEN"]
```

At apply time, the reconciler validates that required env vars are set. For
local use, `knarr.env` (gitignored) provides values. For k8s/CI, env vars
come from Secrets.

The bridge login step (`login-token bot <token>`) uses the Discord token from
the environment. The reconciler handles this as part of bridge setup — no
manual bridge login needed after the first `knarr config apply`.

---

## Testing Strategy

### Layer 1: Unit tests (mocked Matrix API)

No live server. httpx mock transport (existing pattern). Tests verify:
- Config loading and validation (bad YAML, missing fields, duplicate aliases)
- Diff computation (given config + current state → expected actions)
- Action ordering (spaces before rooms, rooms before bridges)
- Audit mode produces report without calling mutation methods
- Idempotency (apply with matching state → zero actions)

Runs in < 1 second.

### Layer 2: Integration tests (real Synapse, BDD)

pytest-bdd features against the local k3d Synapse:

```gherkin
Feature: Room reconciliation

  Scenario: Apply creates rooms from config
    Given a clean Matrix homeserver
    And a config with space "test" containing rooms "room-a" and "room-b"
    When I run config apply
    Then room "#room-a:knarr.local" exists in Matrix
    And room "#room-b:knarr.local" exists in Matrix
    And both rooms are children of space "test"

  Scenario: Apply is idempotent
    Given the config has been applied once
    When I run config apply again
    Then the report shows zero actions

  Scenario: Audit detects drift
    Given the config has been applied
    And room "#room-a:knarr.local" is manually deleted
    When I run config audit
    Then the report shows "create room:room-a"

  Scenario: Wipe and rebuild
    Given the config has been applied
    When all managed rooms are deleted
    And I run config apply
    Then all rooms exist with correct names, topics, and members
    And bridges are connected with relay webhooks
```

Uses a `test-` alias prefix convention to avoid colliding with real rooms.

### Layer 3: kuttl tests (k8s resource assertions)

For watcher config reconciliation — verify the deployment has correct env
vars after apply:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: knarr-watchers
  namespace: knarr
spec:
  template:
    spec:
      containers:
        - name: watchers
          env:
            - name: REDDIT_SUBREDDIT
              value: "Terasology"
```

Optional for v1, natural follow-up.

---

## File Structure

```text
components/knarr/
  config/
    knarr.yaml                 # Top-level index
    test.yaml                  # Test community config
  src/admin/
    client.py                  # MatrixAdminClient (extended)
    bridge.py                  # BridgeManager (existing)
    reconciler.py              # Reconciler (new)
    config_schema.py           # Config loading and validation (new)
    cli.py                     # CLI (extended with config subgroup)
  tests/
    test_admin_client.py       # Extended with new method tests
    test_reconciler.py         # Unit tests for diff/apply logic (new)
    test_config_schema.py      # Config validation tests (new)
    features/                  # BDD integration tests (new)
      reconcile.feature
      steps/
        reconcile_steps.py
```

---

## Observability (Future)

The `ReconcileReport` data model is designed to support structured metrics:

- `knarr_reconcile_actions_total{operation, resource_type}`
- `knarr_reconcile_drift_detected{community}`
- `knarr_reconcile_errors_total{resource}`
- `knarr_reconcile_duration_seconds`

Not implemented in v1. The report structure supports it when Grafana
integration is built.

---

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Build vs adopt matrix-corporal | Build our own | corporal is an inline proxy (SPOF, latency), covers only user/room membership, doesn't handle bridges/watchers. Our admin library already exists. |
| Config structure | Split by community with index | Scales for multiple communities, keeps PRs focused |
| Room identity | Aliases (primary) + state event (safety net) | Aliases are human-readable and queryable. State event prevents confusion with user-created rooms. |
| Secret handling | Env var references in config | Config is fully committable. Secrets stay in knarr.env or k8s Secrets. |
| Reconciler execution model | CLI (apply/audit), not daemon | Simplest, testable, debuggable. CronJob upgrade path is trivial. |
| Watcher config | Reconciler generates k8s patch | Watchers declared per-room, aggregated into single deployment config |
| Testing | Unit (mocked) + BDD integration (real Synapse) + kuttl (k8s) | Three layers: fast feedback, real behavior, infrastructure correctness |
