# Knarr Owner Notify Design: the personal inbox room, and the seam with the Borgr vault

**Date:** 2026-11-27
**Status:** Draft. Agreed with the Borgr PKM v2 design; implementation starts after knarr#11 (publish Phase A) merges.
**Component:** knarr
**Builds on:** [Publish Design](2026-08-30-knarr-publish-design.md) (PublisherInstance, scope authorization, origins, idempotent fan-out) · [Source-Identity Design](2026-05-30-knarr-source-identity-design.md) (scope axis, reply-from-Matrix)
**Related:** the Borgr PKM v2 workflow (in the Borgr vault) and the Muninn and Huginn Thunderbird designs (realm-siliconsaga `docs/plans`)

---

## Overview

The PKM v2 design splits personal organizing three ways: the workflow lives in the Borgr vault, the Thunderbird pieces (Muninn, Huginn) become their own components, and delivery to the owner is Knarr's. This note is the Knarr part. It adds no new pipeline stage: owner notify is an ordinary `post` to a `user/`-scoped Matrix publisher through the origins the publish design already names.

## The line

- The vault, Thunderbird and the ravens own **durable content and review surfaces**: the hit list, the review queue, notes, mail.
- Knarr owns **conversational content and delivery**: rooms, bridges, digests of chat.
- Knarr never reads the vault. The vault holds no chat credentials. Content crosses the seam only as a publish request the vault side composes.

## Owner notify

- **Destination.** A personal room under scope `user/cervator`, reached by a Matrix publisher. Tentative names, fixed when the reconciler declares them: publisher `owner-matrix`, room `#cervator-inbox:knarr.local`.
- **Origins, in order.**
  1. The `cli` origin (`knarr publish --scope user/cervator --targets owner-matrix ...`), which exists in Phase A.
  2. An HTTP origin: `POST /publish` reachable only over the tailnet, authenticated with a bearer token scoped to `user/cervator`. Same `PublishRequest` body, same scope check before any send.
- **Not allowed:** callers sending straight to Matrix with a bot token. Every owner message goes through scope authorization and the result store.

## Idempotency

- Phase A already skips an identical request as a retry (`derive_request_id` over publisher, verb, parent and content).
- The HTTP origin adds an explicit `idempotency_key` field. When present it replaces the derived id, so a caller can retry a changed rendering of the same item without a duplicate.
- Until then, callers put the date, ISO week or item id in the text, so a new day's hit list is a new request and a retried one is not.

## Source tags and cadence

Requests from the vault side carry a `source` tag:

| Tag | When |
|---|---|
| `borgr/hit-list` | Daily, morning |
| `borgr/review-queue` | Weekly, only when the queue is non-empty |
| `borgr/urgent` | Ad hoc |
| `borgr/run-failed` | When a scheduled vault run fails |

The cadence belongs to the caller. Knarr does not schedule these.

## Callers

- **Dionysus**, for testing: plain Python, no containers, reaching Knarr over Tailscale.
- **Later**, a Linux VM on the homelab.

## Later, not in the first cut

- **Personal digest of bridged rooms.** Discord first, then WhatsApp, produced by Knarr into the owner room. The vault may read the digest output but never the rooms.
- **Inbox-zero room.** Each item is a message; a reaction or reply completes it, and the completion flows back to the vault. This needs the Matrix-to-Knarr return path from the source-identity design's reply-from-Matrix work, with the item id as the correlation id. "Erindi" is a possible name for this, parked until it exists.

## Order of work

1. knarr#11 merges (publish Phase A).
2. Declare `owner-matrix` and the owner room through the config reconciler; confirm the final names to the Borgr side.
3. Owner notify over the CLI origin, exercised from Dionysus.
4. The HTTP origin with bearer token and `idempotency_key`.
5. Digest, then inbox-zero, each designed in its own note when picked up.

## Open questions

- Where the bearer token lives on the caller side, and how it rotates.
- Whether `borgr/run-failed` should bypass quiet hours once those exist.
