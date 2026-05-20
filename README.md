# Knarr

<img src="docs/knarr-banner.png" alt="Knarr — integration hub" width="100%">

Integration/bridging layer for the Yggdrasil ecosystem. Self-hosted Matrix
homeserver with bridges, Kafka event bus, and platform watchers.

The metaphor: Knarr is the merchant ship and its trade routes — carrying messages
between platform ports, knowing the routes (routing rules), and keeping a manifest
(Kafka event log). Autoboros is the crew — chatbots and content adaptation.

## Documentation

| Doc | Description |
|-----|-------------|
| [Architecture](docs/architecture.md) | Two-plane design (Matrix + Kafka), data flows, testing strategies |
| [CLI Usage](docs/cli-usage.md) | Operational CLI and Python library for room/user/bridge management |
| [Deploy](docs/deploy.md) | First-time setup against a fresh k3s/k3d cluster |
| [Operations](docs/operations.md) | Day-2 procedures: status checks, Kafka tools, rebuilds, env vars |
| [Troubleshooting](docs/troubleshooting.md) | Known failure modes and workarounds |
| [Discord Bridge Setup](docs/discord-bridge-setup.md) | mautrix-discord setup, identity mapping, troubleshooting |
| [Tailscale Setup](docs/tailscale-setup.md) | Remote access via Tailscale Serve for phone/off-LAN |
| Design Spec | Full design in the yggdrasil workspace: `docs/plans/2026-04-02-knarr-design.md` |

## Architecture

```
Reddit/GitHub/etc → Watchers → Kafka → Router Bot → Matrix (#social-watch)
Discord ←→ mautrix-discord ←→ Synapse ←→ Element (phone/desktop)
WhatsApp ←→ mautrix-whatsapp ←→ Synapse    (Phase 2)
```

**Layer 1 — Nervous System:** Synapse homeserver + mautrix bridges (transparent chat bridging)
**Layer 2 — Routing:** Kafka event bus + router service (curated fan-out, approval workflows)
**Layer 3 — Agents:** Platform watchers + Autoboros bots (monitoring, content adaptation)

## Components

| Component | Image | What it does |
|-----------|-------|-------------|
| Synapse | `matrixdotorg/synapse:v1.122.0` | Matrix homeserver, backed by PostgreSQL via Mimir |
| Router Bot | `knarr-router:dev` | Consumes Kafka alerts, posts to Matrix rooms |
| Watchers | `knarr-watchers:dev` | Polls Reddit/GitHub, publishes to Kafka |
| mautrix-discord | *(not yet deployed)* | Bridges Discord ↔ Matrix |
| Kafka UI | `provectuslabs/kafka-ui:v0.7.2` | Web dashboard for browsing topics/messages |
| Config Reconciler | Python (built-in) | Reads YAML config, diffs against Matrix state, applies changes |

## Kafka topics

| Topic | Purpose |
|-------|---------|
| `knarr.messages.inbound` | Messages arriving from bridges |
| `knarr.messages.outbound` | Messages approved for publishing |
| `knarr.watch.alerts` | Platform watcher alerts |
| `knarr.routing.pending` | Awaiting human approval |
| `knarr.routing.decisions` | Approval/rejection events |
| `knarr.content.draft` | Content being shaped before publishing |

## Tests and lint

```bash
python3 -m pytest tests/ --ignore=tests/features   # unit tests
python3 -m ruff check src/ tests/                  # lint
```

BDD scenarios live under `tests/features/` and need a live homeserver
plus `KNARR_ADMIN_PASSWORD` to run; they skip otherwise. See
[CLI Usage](docs/cli-usage.md) for details on the reconciler/CLI those
scenarios drive.
