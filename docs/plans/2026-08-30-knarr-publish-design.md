# Knarr Publish Design — outbound fan-out, capabilities, and the origin axis

**Date:** 2026-08-30 (revised 2026-08-31 — capability model, scope authorization, idempotency, media handling)
**Status:** Draft, in review
**Component:** knarr
**Builds on:** [Source-Identity Design](2026-05-30-knarr-source-identity-design.md) (the inbound complement — watcher instances, adapters, scope, credentials) · [the OG 2026-04-02 design](../../../../realms/realm-siliconsaga/docs/plans/2026-04-02-knarr-design.md) (Matrix/Kafka layers, the approve-then-fan-out topics)

---

## Overview

Knarr can read from platforms and route what it finds into Matrix. It cannot write anything back out. This design adds the write half: publishing original content to several platforms at once, and — because they share a transport — replying to the things the watchers surface.

Three ideas carry the design:

- **Destination** — *where content goes*, as a `PublisherInstance` mirroring `WatcherInstance`.
- **Capability** — *what a given destination can actually do*, because platforms differ sharply and pretending otherwise pushes the differences into runtime failures.
- **Origin** — *how a publish request gets authored and approved*, which differs per community and must not be hardcoded.

The last two are both corrections to earlier drafts, and both came from the same instinct: **an abstraction that flattens real differences does not remove them, it relocates them to somewhere worse.**

---

## Why this exists

### The gap is narrower than it looks, and in an unexpected place

The OG design already specified the outbound *topology*. Four of the six Kafka topics Knarr provisions exist for this flow:

| Topic | Purpose (OG design) |
|---|---|
| `knarr.content.draft` | Content being shaped before publishing |
| `knarr.routing.pending` | Awaiting human approval before fan-out |
| `knarr.routing.decisions` | Approval/rejection from power users |
| `knarr.messages.outbound` | Messages approved for publishing to platforms |

with a worked example: *"Blog post draft approved → adapts per platform → publish via `knarr.messages.outbound`."*

So the pipeline was designed. **What was never built is any publisher at all** — `src/` holds `admin`, `router` and `watchers`, and the router only consumes `knarr.watch.alerts` and posts into one Matrix room. Nothing in the codebase writes to an external platform.

### The user-facing problem

One person supports several communities as "the computer guy" rather than as a member of an organizing team. A new soccer season means the same information — dates, times, pricing, a registration link, a flyer image — has to appear on a website, a Facebook Page, an Instagram account, and increasingly a WhatsApp group, each with its own format, its own limits, and its own way of telling you afterwards that somebody replied.

The value is not automation for its own sake. It is that **one approved thing becomes N correctly-formatted posts, and everything that comes back lands in one place.**

---

## Audience reality, and what it removes from v1

**For the foreseeable future there is exactly one operator.** Not a team with a volunteer rota — one person supporting three communities from outside them.

Taken deliberately:

- **No multi-person approval routing in v1.** `routing.pending` / `routing.decisions` stay provisioned but unused by the publish path.
- **No assignment, claiming, or "who is handling this".**
- **No role model beyond "the operator".**

A v1 scope decision, not an architectural one. Everything below works unchanged when a second person appears.

---

## Axis 1 — Destination and capability

### The `PublisherInstance`

Mirrors `WatcherInstance`, because the two problems are the same problem pointed in opposite directions.

```yaml
publishers:
  - id: mtl-facebook-page
    platform: facebook
    surface: page-feed              # a Page is TWO destinations — see below
    access_path: admin-app
    scope: community/mtl
    credentials_ref:
      secret_name: knarr-cred-community-mtl-page-token
      secret_key: FB_PAGE_TOKEN

  - id: mtl-facebook-messenger
    platform: facebook
    surface: messenger              # same Page, same token, different capabilities
    access_path: admin-app
    scope: community/mtl
    credentials_ref:
      secret_name: knarr-cred-community-mtl-page-token
      secret_key: FB_PAGE_TOKEN

  - id: mtl-instagram
    platform: instagram
    surface: feed
    access_path: admin-app
    scope: community/mtl
    credentials_ref:
      secret_name: knarr-cred-community-mtl-ig
      secret_key: IG_TOKEN
```

**`surface` is why a Facebook Page appears twice.** The Page's feed is broadcast-plus-comments and genuinely awkward to work with; Messenger is conversational and behaves far more like Discord or Matrix. Modelling "facebook" as one destination is what made it look uniformly difficult. Splitting by surface lets each half declare honestly what it can do — and Messenger turns out to be one of the *easier* destinations, not one of the hardest.

### Three verbs, not two

An earlier draft had `post()` and `reply()`, which was wrong twice over: it implied every destination could do both, and it collapsed two genuinely different operations into one word.

| Verb | Means | Example destinations |
|---|---|---|
| `post` | Create a top-level item on a feed others follow | FB Page feed, Instagram, Matrix room, Discord channel |
| `comment` | Respond to a specific feed item | FB Page feed, Instagram, Matrix thread, Discord thread |
| `message` | Send into a conversation with someone | FB Messenger, WhatsApp, Matrix DM, Discord DM |

Replying to a feed comment and sending into a conversation obey different rules — the second has delivery windows, the first does not. One word for both would hide exactly the constraint that matters.

### Capability protocols, not one interface

A publisher implements only what its surface can do. Calling code asks rather than assumes.

```python
class Broadcaster(Protocol):
    async def post(self, content: Content) -> PublishResult: ...

class Commenter(Protocol):
    async def comment(self, parent: ObjectRef, content: Content) -> PublishResult: ...

class Conversational(Protocol):
    async def message(self, thread: ThreadRef, content: Content) -> PublishResult: ...

class Reactor(Protocol):
    async def react(self, target: ObjectRef, emoji: str) -> PublishResult: ...

class Editable(Protocol):
    async def edit(self, target: ObjectRef, content: Content) -> PublishResult: ...
```

An Instagram publisher implements `Broadcaster` + `Commenter` and simply does not implement `Conversational` — it has no conversations. A WhatsApp publisher implements `Conversational` and nothing else — it has no feed. **A `social.yml` targeting WhatsApp with a `post` fails validation at authoring time**, with "whatsapp cannot post; it can message", rather than failing at publish time with a platform error.

Every publisher additionally implements a small shared surface:

```python
class Publisher(Protocol):
    id: str
    scope: str
    capabilities: frozenset[str]        # {"post", "comment"} — derived, not configured
    constraints: Constraints            # media requirements, formats, windows, limits
    def validate(self, verb: str, content: Content) -> list[Violation]: ...
```

`capabilities` is **derived from which protocols the class implements**, not declared in YAML. A configured capability list would drift from the code the first time an adapter changed.

---

## The capability landscape — illustrative, not authoritative

> **⚠ Read this table as a sketch of the problem shape, not as a specification.**
>
> It exists to make the *variation* concrete — to show that "publish to social" is not one operation — and it will be wrong in places. Several cells are unverified because the Meta developer account is currently stuck in a [platform-side verification loop](https://communityforums.atmeta.com/discussions/Questions_Discussions/stuck-in-verification-loop---cannot-create-app-for-whatsapp-api/1368496), so they cannot be tested at all yet.
>
> **Expect to revise this repeatedly** — when each adapter is built, when a platform changes its API, and when new platforms are added. Any implementation plan derived from this design should verify the cells it depends on rather than trusting them. A cell that has not been exercised by working code is a hypothesis.

| | Matrix | Discord | FB Page feed | FB Messenger | Instagram | WhatsApp |
|---|---|---|---|---|---|---|
| `post` | ✅ | ✅ | ✅ | — | ✅ media-only | — |
| `comment` | ✅ thread | ✅ thread | ✅ | — | ✅ | — |
| `message` | ✅ DM | ✅ DM | — | ✅ | — | ✅ |
| `react` | ✅ | ✅ | ⚠️ | ⚠️ | ⚠️ | ⚠️ |
| `edit` | ✅ | ✅ | ⚠️ partial | — | ❓ | ❓ |
| `delete` | ✅ redact | ✅ | ✅ | ⚠️ | ✅ | ❓ |
| Media required | no | no | no | no | **yes** | no |
| Media format | any | any | flexible | flexible | **JPEG only** | several |
| Send window | none | none | none | **24h + tags** | none | **24h / template** |
| Rich text | HTML | markdown | plain-ish | plain-ish | plain | limited |

✅ confident · ⚠️ believed, unverified · ❓ unknown · — not applicable to this surface

Two things fall out even from a sketch this rough. **Instagram's "media required" and "JPEG only" are the only hard blockers in the matrix** — and volundr#12 has now removed the second by adding a `jpg` output kind to `flyer-kit`. And **send windows are the genuinely novel constraint**: nothing else here has a notion of *you may not speak right now*, and it is the one rule that can make a perfectly valid message unsendable for reasons of timing rather than content.

### Platforms deliberately not modelled yet

Not because they do not matter — because adding them before the first two adapters exist would be designing against guesses.

- **X/Twitter.** Whatever becomes of the platform, people are still there, so compatibility is a plausible future want. Its API terms and pricing are volatile enough that any design written now would be stale.
- **Reddit.** Already a *source* in the inbound design, and its anonymous read path just closed (see `operations.md`), which is a warning about assuming stability. As a destination it is a `post` + `comment` surface with strong per-subreddit norms.
- **Bluesky, Mastodon, LinkedIn, Nextdoor, YouTube.** Each appears in the source-identity taxonomy on the inbound side.

The capability model is the mechanism for absorbing these: a new platform is a new adapter declaring which protocols it implements, and a new row in a table that was always going to need more rows.

---

## Axis 2 — Origin: how a publish request is authored and approved

**The gate is a property of the community, not of the system.**

| Origin | Fits | Approval is | Status |
|---|---|---|---|
| `git-merge` | A community centred on a GitHub-hosted site — MTL, campaigns | Merging the PR | **Specified** |
| `cli` | A community with no site — the PTA today | Running the command | **Specified** |
| `matrix` | Any community once chat organizing exists | Reacting to a draft | **Seam only** |

An origin's only job is to produce a validated, authorized `PublishRequest` and assert that a human approved it. Everything downstream is shared.

```python
@dataclass
class PublishRequest:
    request_id: str                  # deterministic; see Idempotency
    scope: str                       # community/mtl
    targets: list[Target]            # publisher id + verb + per-target content
    origin: str                      # git-merge | cli | matrix
    approved_by: str
    approved_at: str

@dataclass
class Target:
    publisher_id: str
    verb: str                        # post | comment | message
    content: Content                 # already resolved for this destination
    parent: ObjectRef | None         # for comment/message
```

**Adding an origin never touches the publishers, and adding a platform never touches the origins.**

---

## Authorization, idempotency, and result identity

These three were absent from the first draft and each is load-bearing.

### Scope authorization happens before credentials are resolved

A `PublishRequest` names target publishers by id. Nothing in the first draft checked that those publishers belonged to the request's *scope* — so a typo or a bug could have published MTL content through a campaign's credentials. That is the worst failure this system could have.

**Every target is authorized before fan-out, and the check is on the resolved instance rather than the requested id:**

1. Resolve `publisher_id` to a `PublisherInstance`; unknown id → reject the whole request.
2. Assert `instance.scope == request.scope`; mismatch → reject the whole request, loudly. This is a bug or an attack, never a routine condition.
3. Assert the instance implements the requested verb; missing → reject.
4. Only then resolve credentials.

Rejection is whole-request rather than per-target. A request naming a destination it has no business touching is not partially trustworthy.

For `git-merge`, scope comes from the repo→scope mapping in Knarr's config, **not** from `social.yml` — a repo may declare what it wants said, but not on whose behalf. That single rule keeps a compromised or careless site repo from reaching another community's Page.

### Idempotency, because fan-out is partial by nature

Publishing N destinations is N independent calls, any of which may fail, and a retry must not repost the ones that succeeded.

- **`request_id` is derived deterministically from immutable source data**, never generated fresh on retry. For `git-merge`: the merge commit SHA plus the campaign path. For `cli`: a hash of scope, targets, and content. The same input always yields the same id.
- **Outcomes are recorded per `(request_id, publisher_id, verb)`**, durably, before the next target is attempted.
- **A retry skips targets already recorded as succeeded.** Only unrecorded or failed targets are attempted.
- **Ambiguous outcomes are reconciled, not retried blindly.** If a call times out with no result, the publisher queries the platform for a matching recent object where the API allows, and otherwise marks the target `uncertain` and surfaces it. Reposting on ambiguity is how a community gets the same announcement twice.

### `PublishResult` identity is composite

A bare platform id is not enough to act on later. Two Pages can produce indistinguishable comment ids, and a reply must go out through the same instance and credentials that made the original post.

```python
@dataclass
class ObjectRef:
    publisher_id: str     # which instance — determines adapter AND credentials
    platform: str
    surface: str
    object_type: str      # post | comment | message | media
    object_id: str        # what the platform calls it
```

This is what makes the engagement loop work: a comment arrives on an MTL Page post, the watcher emits it with an `ObjectRef`, the operator answers in Matrix, and the reply goes back through `mtl-facebook-page` because the ref says so. **Storage must outlive a pod** — Kafka is a log, not a lookup. Postgres via a Mimir `DataService` is the obvious home and would make Knarr the shared cluster's second consumer.

---

## Topics and contracts

`knarr.messages.outbound` already has a meaning from the OG design — *messages approved for publishing*, with Autoboros as an intended consumer. Putting a differently-shaped `PublishRequest` on it would silently change a contract another component was written against.

So:

- **`knarr.publish.requests`** — new topic, carries `PublishRequest`. Owned by this design.
- **`knarr.publish.results`** — new topic, carries per-target outcomes including `ObjectRef`.
- **`knarr.messages.outbound`** — left alone, contract intact.

New topics are cheap; a contract collision found six months later is not. If the two shapes genuinely converge, merging them later is a deliberate migration rather than an accident.

---

## Media handling

The first draft hand-waved this and got two things wrong.

### A replayable record must not carry a perishable URL

`knarr.publish.requests` has seven-day retention and is replayable by design. A short-TTL signed URL written into it is **dead on replay** — the request would look valid and fail at fetch time for reasons no log would explain.

So a request carries a **durable media reference**, and a fetchable URL is minted per delivery attempt:

```python
@dataclass
class MediaRef:
    kind: str            # garage-object | public-url
    locator: str         # object key, or the URL itself
    content_type: str
    checksum: str        # detects the object changing underneath a replay
```

- **`cli` origin** uploads local files to Garage and emits `kind: garage-object`. The publisher mints a signed URL immediately before the platform fetch, with a TTL sized to that attempt.
- **`git-merge` origin** emits `kind: public-url` — gh-pages already serves the asset durably, which is exactly what Instagram needs.

### The relative-path-to-URL contract has to be written down

`social.yml` says `media: exports/mtl-soccer-fall-2026-instagram.jpg`. That is a repo-relative path, and something must turn it into a URL Instagram can fetch. Left implicit, it becomes a guess in code.

**The contract:** Knarr's per-repo config declares a `public_base_url` (for MTL, the custom domain gh-pages serves) and the path mapping from repo root to served root. The origin resolves the relative path against it, and the resolved URL is **validated as fetchable before the request is emitted** — a publish that beat its own asset into existence fails at authoring, not at the platform.

This is also why the `git-merge` origin waits for the deploy workflow of the same merge commit to finish before emitting. Instagram fetches over HTTP; a request emitted at merge time would race a deploy that has not run.

### Preflight is async and separate from validation

`validate()` stays **pure and local** — no network — so it is cheap, deterministic, and safe to call anywhere including in a tight authoring loop.

Fetchability is a separate `async preflight()` with a bounded timeout, run once per request before fan-out, its result reused by every target rather than re-fetched per destination. Keeping them apart means a dry run does real local validation instantly, and real network validation only when asked.

**`--dry-run` runs both**, and reports them separately, so "this would be rejected by Instagram" and "this URL is not reachable" are distinguishable failures.

---

## Replies are the same transport, not the same operation

The source-identity design scheduled reply-from-Matrix as Phase 6, sub-staged by access path. Built separately it would duplicate credential resolution, error surfacing, result recording and retry.

**A reply is a write to the same platform the original lives on, through the same instance, with the same credentials.** So it belongs to the publisher — but as `comment()` or `message()` depending on surface, *not* as a single `reply()`, because those obey different rules.

To be explicit about what this does **not** mean: replying never crosses platforms. A comment on a Facebook post is answered on Facebook. Matrix is where the *operator* reads and types; the write goes out through the publisher that owns that scope and surface.

Phase 6 therefore collapses from a subsystem into two protocol methods plus a routing rule — with the `ObjectRef` recorded at publish time as the hinge that makes it possible at all.

---

## Validation is the load-bearing part

Platforms fail late, vaguely, and after the interesting work is done. A shared validate step turns those into authoring-time errors:

- **Verb unsupported by surface** — "whatsapp cannot post". The most common authoring mistake, and free to catch.
- **Instagram requires media** and **accepts JPEG only**.
- **Send windows** — a free-text WhatsApp message outside a 24-hour window is a validation failure, not a runtime surprise.
- **Per-platform length limits**, checked against rendered text rather than source.
- **Scope mismatch** — see Authorization.

---

## Captions, and where AI sits

Per-destination captions, authored in the manifest or the CLI invocation. The source-identity principle transfers unchanged: **AI adaptation is infrastructure, but opt-out per consumer.**

- `caption` — written by hand, used verbatim. Always the default for anything sensitive.
- `caption_from: <publisher-id>` — reuse another target's text.
- `adapt: true` — derive from a base caption, respecting this destination's limits and conventions.

A political campaign can hand-write every word while MTL lets a soccer announcement be reshaped for Instagram. Per target, not per instance, because it is a judgement about *this* message.

---

## The volundr boundary

volundr states two properties this design must not break: **no custom secrets anywhere**, and **a human merge is always the publish gate**.

- **volundr renders and gates.** Builds the flyer, previews it, visually diffs it, never learns a Meta token exists.
- **The site repo declares intent.** `social.yml` names destinations by id and supplies captions. No credentials, and — per Authorization — no authority over scope.
- **Knarr holds identity and credentials** in-cluster, authorizes, resolves, and writes.

Knarr **polls** for merges rather than receiving a webhook: the cluster is private with no inbound path, and an Action calling Knarr would need a credential volundr is designed not to hold. Latency is minutes, which is right for content that took days to write. A Tailscale-reachable endpoint is the upgrade path if that ever changes.

A **CI-side `social.yml` validator** in volundr is worth adding later — unknown publisher id, missing media file, verb unsupported by the named destination. It needs only publisher ids and capabilities, never secrets, so it stays inside volundr's trust model.

---

## Where the PTA actually fits

**The PTA's stated problem is fragmentation, not publishing.** Communication is scattered across several WhatsApp groups and Facebook with little web presence, and the pain is that nobody can see it all.

That is the *inbound* half of Knarr, with publishing secondary. Treating the PTA as "MTL but on WhatsApp" would build the wrong thing first. The `cli` origin is specified here anyway because it is small and needs no infrastructure — but **the PTA's first real value is aggregation**, and that ordering belongs in whatever plan follows.

---

## Phasing

Ordered so nothing is blocked on Meta developer-account verification, which is outside our control.

**Phase A — the publisher, on destinations we control.** Capability protocols, `PublisherInstance`, authorization, idempotent fan-out, `ObjectRef` persistence. Adapters for **Matrix** and **Discord**, both already running. Origin: `cli`, `--dry-run` first. *Exit: one command publishes to two real destinations, records both `ObjectRef`s, and a re-run reposts nothing.*

**Phase B — the `git-merge` origin.** `social.yml` schema, repo→scope mapping, polling, deploy-completion wait, media URL resolution. *Exit: merging a PR publishes without anyone running a command.*

**Phase C — the Meta destinations.** FB Page feed, FB Messenger, Instagram, WhatsApp, behind the same protocols. Gated on the account unblocking. *Exit: an MTL flyer reaches a Page and an IG account from a merge.*

**Phase D — the engagement loop.** Comment and message watchers correlated by `ObjectRef`, routed into Matrix; `comment()` / `message()` on publishers that already exist.

**Phase E — the `matrix` origin.** When chat organizing exists.

Phases A and B are entirely unblocked today.

---

## Deferred, with the reason stated

- **Multi-person approval and volunteer triage.** One operator; the primitives exist unused.
- **Scheduling.** A property of the request, not the pipeline. Out of v1.
- **Cross-posting inbound to outbound.** Needs the engagement loop first.
- **Deletion and editing of published posts.** Platforms vary wildly and failure modes are worse than for publishing.
- **Analytics.** Nobody has asked what question it would answer.

---

## Risks worth naming now

- **A publish is irreversible in a way a site deploy is not.** A bad merge is fixed by another merge; a bad Facebook post has already reached feeds. Argues for `--dry-run` as the learning default and genuinely strict validation.
- **Political campaign Pages carry extra Meta enforcement** — authorization requirements, disclaimers, stricter review. Never the target of a first run of anything.
- **`ObjectRef` persistence is the hinge of the engagement half.** Lose it and inbound comments cannot be tied to what they are about.
- **Silent success is the failure mode this codebase keeps producing.** The router reported delivery Synapse had rejected; Strimzi ignores a topic naming a cluster that does not exist; a watcher instance can fail every cycle and look healthy. **A publisher that reports a post it did not make is that bug with a worse blast radius.** Every publisher verifies its result and surfaces failure loudly.
- **The capability table is a hypothesis.** Building against an unverified cell is how "Instagram accepts PNG" becomes a runtime discovery.

---

## Open questions

1. **Where exactly does `ObjectRef` state live?** Postgres via a Mimir `DataService` is the presumption; schema and retention are unspecified.
2. **How much does `social.yml` overlap `flyers.conf`?** Both name assets in the same directory. Merging them is tempting and probably wrong — rendering and distribution are different concerns with different owners — but deserves a deliberate answer.
3. **Does the Discord adapter go through the Matrix bridge or the API directly?** Via the bridge is nearly free; directly is more control and another credential. Phase A should try the bridge first and record what it cannot express.
4. **Is `surface` a field or part of `platform`?** Written here as a field so `facebook` stays one credential domain across two surfaces. If more platforms split this way, it may deserve to be part of the identifier.
