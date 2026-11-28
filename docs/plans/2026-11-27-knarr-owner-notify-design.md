# Knarr Owner Notify Design: the personal inbox room, and the seam with the Borgr vault

**Date:** 2026-11-27
**Status:** In progress. Agreed with the Borgr PKM v2 design. Steps 2 and 3 of the order of work are in code; applying the config and the first real send wait on the cluster.
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

- **Destination.** A personal room under scope `user/cervator`, reached by a Matrix publisher: publisher `owner-matrix`, room `#cervator-inbox:knarr.local`, read by `@cervator:knarr.local`. Declared in `config/knarr.yaml` and `config/owner.yaml`.
- **Origins, in order.**
  1. The `cli` origin (`knarr publish --scope user/cervator --targets owner-matrix ...`), which exists in Phase A. It runs on the Knarr host, because a real send reads the bridge login there. A caller on Dionysus invokes it remotely over the tailnet (SSH to a forced command) and never receives the Matrix credential.
  2. An HTTP origin: `POST /publish` reachable only over the tailnet, authenticated with a bearer token scoped to `user/cervator`. Same `PublishRequest` body, same scope check before any send.
- **Not allowed:** callers sending straight to Matrix with a bot token. Every owner message goes through scope authorization and the result store.

## Idempotency

- Phase A skips an identical request as a retry once the first success is recorded (`derive_request_id` over publisher, verb, parent and content). Delivery is at-least-once: if the send succeeds and the result write fails, the CLI exits 2 with a warning, and a retry sends again.
- The HTTP origin closes that gap by sending with a Matrix transaction id derived from the request id, the publisher, and the target's verb and parent (the same identity `derive_request_id` uses), so the homeserver drops a resent duplicate even when no record was written. Matrix scopes transaction ids to the sending device, so this holds only while retries use the same login; after a re-login or a token change, delivery is at-least-once again.
- The HTTP origin adds an explicit `idempotency_key` field. When present it replaces the derived id, so a caller can retry a changed rendering of the same item without a duplicate.
- Until then, callers put the date, ISO week or item id in the text, so a new day's hit list is a new request and a retried one is not.

## Source tags and cadence

Requests from the vault side carry a `source` tag. Phase A has nowhere to put one, so the first cut adds an optional `source` field to `PublishRequest` and a `--source` flag to `knarr publish`. The HTTP body carries the same field. The tag is recorded with the result and does not change routing.

| Tag | When |
|---|---|
| `borgr/hit-list` | Daily, morning; at most five highlights, the full list stays in the vault |
| `borgr/review-queue` | Weekly, only when the queue is non-empty |
| `borgr/urgent` | Ad hoc |
| `borgr/run-failed` | When a scheduled vault run fails |

The cadence belongs to the caller. Knarr does not schedule these.

## Callers

- **Dionysus**, for testing: plain Python, no containers, reaching Knarr over Tailscale.
- **Later**, a Linux VM on the homelab.

## Later, not in the first cut

- **Personal digest of bridged rooms.** Discord first, then WhatsApp, produced by Knarr into the owner room. The vault may read the digest output but never the rooms.
- **Inbox-zero room.** Each item is a message; a reaction or reply completes it, and the completion flows back to the vault. This needs the Matrix-to-Knarr return path from the source-identity design's reply-from-Matrix work, with the item id as the correlation id. Emoji reactions are the intended workflow, as long as they leave an audit trail: the vault records each applied reaction as a commit, so the return event must carry the item id, the reaction, who reacted and when. "Erindi" is a possible name for this, parked until it exists.

## Order of work

1. knarr#11 merges (publish Phase A).
2. Declare `owner-matrix` and the owner room through the config reconciler; confirm the final names to the Borgr side.
3. The `source` field and `--source` flag; owner notify over the CLI origin, invoked from Dionysus.
4. The HTTP origin with bearer token, `idempotency_key` and the derived transaction id.
5. Digest, then inbox-zero, each designed in its own note when picked up.

## Open questions

- Where the bearer token lives on the caller side, and how it rotates.
- Whether `borgr/run-failed` should bypass quiet hours once those exist.
