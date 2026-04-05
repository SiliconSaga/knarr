# Knarr

Integration/bridging layer for the Yggdrasil ecosystem. Self-hosted Matrix
homeserver with bridges, Kafka event bus, and platform watchers.

See [knarr-design.md](../yggdrasil/docs/plans/2026-04-02-knarr-design.md) for
the full design spec.

## Components

- **Synapse** — Matrix homeserver
- **mautrix-discord** — Discord bridge
- **Router** — Kafka-to-Matrix alert routing bot
- **Watchers** — Reddit, GitHub platform monitors
