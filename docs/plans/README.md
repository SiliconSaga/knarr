# Knarr design notes — the map

Four design notes and one implementation plan describe Knarr. Each owns one axis; together they are the specification. Read this page first to find the one you need and to see what is real versus designed.

| Note | Owns | Status (2026-09-27) |
|---|---|---|
| [Knarr design (2026-04-02)](../../../../realms/realm-siliconsaga/docs/plans/2026-04-02-knarr-design.md), in the realm | The whole system: Matrix and Kafka planes, bridge fleet, room topology, routing and approval, the Keycloak subscription model, federation, future explorations (Scribe archive, forum as memory, Cactus comments) | Foundational. Its "Event Schema" section describes the pre-Phase-1 envelope and is superseded by the source-identity note; its phase timeline is historical. |
| [Source-identity design (2026-05-30)](2026-05-30-knarr-source-identity-design.md) | **Inbound.** Source taxonomy (scope × access path), `WatcherInstance`, credentials, AI summarize stage and presentation modes, headed-browser sidecar, email-extract, reply-from-Matrix, satellites | **Phase 1 implemented** (knarr#8, 2026-09-01): `WatcherInstance`, adapters, the flat alert envelope, config-driven watcher pod. Phases 2–6 open; tracker [knarr#6](https://github.com/SiliconSaga/knarr/issues/6). |
| [Phase 1 plan (2026-05-31)](2026-05-31-knarr-source-identity-phase-1-plan.md) | The task list that produced knarr#8 | Done. Kept as the record of how Phase 1 was built. |
| [Publish design (2026-08-30)](2026-08-30-knarr-publish-design.md) | **Outbound.** `PublisherInstance`, capability protocols (post / comment / message), origins (git-merge, cli, matrix), scope authorization, idempotent fan-out, `ObjectRef`, media handling, the volundr boundary | Design merged (knarr#9, 2026-09-01). No publisher exists yet; Phases A–E all open. Meta destinations gated on a developer-account verification loop outside our control. |
| [Community reach design (2026-09-27)](2026-09-27-knarr-community-reach-design.md) | **Group C delivery.** Calendar as a source, platform-native membership (Google Group, WhatsApp) as the subscription store, digests as a `schedule` origin, the district scope model | Draft. Phase 0 needs no Knarr and is in flight on wopta.org. |

## What is running

As of 2026-08-29 on the Idunn k3d cluster (`k3d-nordri-test`): Synapse with its database vended by a Mimir `DataService` (knarr#7), the Kafka cluster with all six topics, the router, the config-driven watcher pod with the Reddit and GitHub instances (Reddit's anonymous endpoint has returned 403 since August 2026, so that instance fails every poll until it gets OAuth credentials), and the Discord bridge deployed but not logged in. Everything else in these notes is design.

## How the notes relate

- The original design names the layers and the topics. The source-identity note refines how content gets *in*; the publish note refines how content gets *out*; the community-reach note refines who is on the far end of *out* and adds the calendar as an *in*.
- A new platform is a new adapter on the inbound side and a new publisher on the outbound side, each declaring what it can do. Nothing in the pipeline changes for it.
- The scope axis (`community/`, `group/`, `user/`) is shared by all four and is what keeps one community's credentials away from another's content.

## Conventions

- Notes are dated and never renamed; a superseded section gets a line saying what replaced it rather than being deleted.
- One tracker issue per multi-phase design, with a checkbox per phase (the convention from SiliconSaga/yggdrasil#82). knarr#6 is the live example.
- Implementation plans live beside their design and are marked done here when the PR merges.
