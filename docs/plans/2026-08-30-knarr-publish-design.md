# Knarr Publish Design — outbound fan-out and the origin axis

**Date:** 2026-08-30
**Status:** Draft, ready for review
**Component:** knarr
**Builds on:** [Source-Identity Design](2026-05-30-knarr-source-identity-design.md) (the inbound complement — watcher instances, adapters, scope, credentials) · [the OG 2026-04-02 design](../../../../realms/realm-siliconsaga/docs/plans/2026-04-02-knarr-design.md) (Matrix/Kafka layers, the approve-then-fan-out topics)

---

## Overview

Knarr can read from platforms and route what it finds into Matrix. It cannot write anything back out. This design adds the write half: publishing original content to several platforms at once, and — because they are the same capability — replying to the things the watchers surface.

Two orthogonal axes, and separating them is the whole design:

- **Destination** — *where content goes*, expressed as a `PublisherInstance`, the mirror of `WatcherInstance`.
- **Origin** — *how a publish request gets authored and approved*, which differs per community and must not be hardcoded.

The second axis is the one that is easy to miss. An early version of this design assumed a GitHub pull-request merge was **the** approval gate, because the first real case (Mountain Top League) is a GitHub-hosted site with an existing PR-preview pipeline. That assumption does not survive contact with the second case: the PTA has no site, no repo, and no PR to merge, so a merge-shaped gate is not merely inconvenient there, it does not exist.

---

## Why this exists

### The gap is narrower than it looks, and in an unexpected place

The OG design already specified the outbound *topology*. Four of the six Kafka topics Knarr provisions exist for exactly this flow:

| Topic | Purpose (OG design) |
|---|---|
| `knarr.content.draft` | Content being shaped before publishing |
| `knarr.routing.pending` | Awaiting human approval before fan-out |
| `knarr.routing.decisions` | Approval/rejection from power users |
| `knarr.messages.outbound` | Messages approved for publishing to platforms |

with a worked example: *"Blog post draft approved → adapts per platform → publish via `knarr.messages.outbound`."*

So the pipeline was designed. **What was never built is any publisher at all** — `src/` holds `admin`, `router` and `watchers`, and the router only consumes `knarr.watch.alerts` and posts into one Matrix room. Nothing in the codebase writes to an external platform.

### The user-facing problem

One person supports several communities as "the computer guy" rather than as a member of an organizing team. A new soccer season means the same information — dates, times, pricing, a registration link, a flyer image — has to appear on a website, a Facebook Page, an Instagram account, and increasingly a WhatsApp group, each with its own format, its own limits, and its own way of telling you afterwards that somebody replied. Doing that by hand is not hard so much as it is repetitive, error-prone across copies, and unbounded in how much attention it consumes afterwards.

The value is not automation for its own sake. It is that **one approved thing becomes N correctly-formatted posts, and everything that comes back lands in one place.**

---

## Audience reality, and what it removes from v1

The source-identity design named three user groups. This design adds a constraint that materially shrinks v1: **for the foreseeable future there is exactly one operator.** Not a team of organizers with a volunteer rota — one person, supporting three communities from outside them.

Consequences, taken deliberately:

- **No multi-person approval routing in v1.** `routing.pending` / `routing.decisions` stay provisioned but unused by the publish path. They are the right primitives for a real team and the wrong complexity for a team of one.
- **No assignment, claiming, or "who is handling this".** Inbound engagement lands in a room; the operator reads it.
- **No role model beyond "the operator".** Keycloak groups already exist for when this changes; nothing here depends on them.

This is a v1 scope decision, not an architectural one. Every piece below still works unchanged when a second person appears — the difference is that approval currently has one possible answerer.

---

## Axis 1 — Destination: the `PublisherInstance`

Deliberately the mirror image of `WatcherInstance`, because the two problems are the same problem pointed in opposite directions.

```yaml
publishers:
  - id: mtl-facebook
    platform: facebook
    access_path: admin-app          # admin-app | cloud-api | bridge | api
    scope: community/mtl
    credentials_ref:
      secret_name: knarr-cred-community-mtl-page-token
      secret_key: FB_PAGE_TOKEN
    capabilities: [post, reply]     # what this destination can actually do

  - id: mtl-instagram
    platform: instagram
    access_path: admin-app
    scope: community/mtl
    credentials_ref:
      secret_name: knarr-cred-community-mtl-ig
      secret_key: IG_TOKEN
    capabilities: [post]            # IG comment replies are Phase 2 work
    constraints:
      image_required: true          # IG cannot post text alone
      image_format: jpeg            # and rejects PNG outright

  - id: mtl-matrix
    platform: matrix
    access_path: api
    scope: community/mtl
    capabilities: [post, reply]
```

Each publisher exposes the same interface regardless of platform. A `Publisher` protocol mirrors the inbound `Adapter` protocol:

```python
class Publisher(Protocol):
    async def post(self, content: Content) -> PublishResult: ...
    async def reply(self, in_reply_to: str, content: Content) -> PublishResult: ...
    def validate(self, content: Content) -> list[Violation]: ...
```

`validate` is not decoration. It is what makes a dry run meaningful and what turns "Instagram will reject this" from a runtime failure into a pre-publish error — see *Validation is the load-bearing part* below.

**`PublishResult` carries the platform's own id** (`post_id`, `media_id`, Matrix `event_id`). That id is the correlation key for everything inbound afterwards: a comment on a Page post is only connectable to "the fall soccer announcement" because we recorded what the platform called it. Losing it means the engagement half cannot work.

### Why `capabilities` and `constraints` are per-instance rather than per-platform

Because they genuinely vary by access path and by account, not just by platform. An Instagram account reachable through an admin app can publish; the same platform reached by a personal-login scrape cannot. Encoding these on the platform would force the exceptions into code.

---

## Axis 2 — Origin: how a publish request is authored and approved

**The gate is a property of the community, not of the system.** This is the central claim of this design.

| Origin | Fits | Approval is | Status |
|---|---|---|---|
| `git-merge` | A community centred on a GitHub-hosted site — MTL, local campaigns | Merging the PR | **Specified below** |
| `cli` | A community with no site — the PTA today | Running the command | **Specified below** |
| `matrix` | Any community once chat organizing actually exists | Reacting to a draft in a room | **Seam only** — see below |

An origin's only job is to produce a validated **PublishRequest** and assert that a human approved it. Everything downstream — validation, formatting, fan-out, result recording — is shared.

```python
@dataclass
class PublishRequest:
    request_id: str            # stable, dedupe key
    scope: str                 # community/mtl
    targets: list[str]         # publisher instance ids
    content: Content           # body, media refs, link
    per_target: dict[str, Content]   # optional overrides
    origin: str                # git-merge | cli | matrix
    approved_by: str           # who, and via which surface
    approved_at: str
```

Emitted to `knarr.messages.outbound`. A single consumer fans out. **Adding an origin never touches the publishers, and adding a platform never touches the origins.**

---

## Origin: `git-merge` (specified)

For MTL and campaigns, where a site repo already exists and volundr already renders, previews and visually diffs every change.

### The manifest lives beside the assets it describes

```text
mtl-soccer/flyers/fall-2026/
  flyers.conf          how to render        (volundr)
  social.yml           where it goes        (this design)
  index.html
  instagram.html
  exports/
    mtl-soccer-fall-2026.pdf
    mtl-soccer-fall-2026-instagram.jpg
```

```yaml
# social.yml
scope: community/mtl
targets:
  - publisher: mtl-facebook
    caption: |
      Fall soccer registration is open! Saturdays 9am at O'Connor Field.
      $85 per player, ages 5-12. Register: https://mountaintopleague.com/register
    media: exports/mtl-soccer-fall-2026-instagram.jpg
  - publisher: mtl-instagram
    caption_from: mtl-facebook      # reuse, or write a distinct one
    media: exports/mtl-soccer-fall-2026-instagram.jpg
  - publisher: mtl-matrix
    caption: "Fall registration is live — flyer attached, please share."
    media: exports/mtl-soccer-fall-2026.pdf
```

Placement is deliberate. The caption is *content*, so it belongs with the content, edited by whoever edits the flyer, in the same pull request, reviewed in the same diff. A caption living in Knarr's config would be a change to infrastructure in order to fix a typo.

**The manifest never names a credential.** It references publishers by id; Knarr resolves identity and secrets. This is what lets a public site repo declare where its content goes without holding a token — see *The volundr boundary*.

### The trigger: Knarr polls, and does not receive

Knarr runs on a private cluster with no inbound path, and volundr's stated trust model is **"no custom secrets anywhere — jobs use only the ephemeral `GITHUB_TOKEN`."** A GitHub Action calling into Knarr would need a credential that repo is explicitly designed not to hold, and exposing a webhook endpoint would mean putting an ingress in front of a private cluster.

So a `git-merge` origin instance polls its watched repos for merged pull requests touching a path that contains a `social.yml`, on the cadence its config declares. Latency is minutes, which is correct for content that took days to write.

Both properties are preserved: **GitHub holds no Knarr secret, and the cluster accepts no inbound connection.** If latency ever matters, a Tailscale-reachable webhook endpoint is the upgrade path and changes only the trigger, not the flow.

### Ordering constraint, easy to get wrong

Instagram fetches media **over HTTP from a public URL**. The gh-pages deploy provides exactly that — but only after it runs. So the publish must wait for the site deploy of the same merge to complete, or the fetch 404s.

The origin therefore waits on the deploy workflow's status for the merge commit before emitting the request. A publish that beat its own asset into existence would fail in a way that looks like a Meta problem.

---

## Origin: `cli` (specified)

For the PTA today: no repo, no site, no PR. The operator has content and wants it in two places.

```bash
knarr publish --scope group/pta \
  --targets pta-facebook,pta-whatsapp \
  --caption-file ./announcement.md \
  --media ./photo.jpg \
  --dry-run

knarr publish --scope group/pta --targets pta-facebook,pta-whatsapp \
  --caption-file ./announcement.md --media ./photo.jpg
```

`--dry-run` runs the full path — resolve publishers, validate per destination, render the final text — and prints exactly what would be sent where, without sending. It is the default posture while learning a new destination, and it is the same code path as a real publish rather than a simulation of it.

Approval is that the operator ran the command. With one operator that is a complete and honest answer; it is recorded in `approved_by` as such rather than pretending a review happened.

**Media with no public URL is the one real wrinkle.** Instagram and WhatsApp both fetch from a URL, and a local file has none. The CLI origin uploads to Knarr's own media store (Garage, already in the stack) and hands the publisher a signed short-TTL URL. This is exactly the problem the `git-merge` origin gets for free from gh-pages, and it is why the media-hosting seam belongs in the shared layer rather than in either origin.

---

## Origin: `matrix` (seam only, deliberately)

Not specified, because there is no chat-organizing habit to design against yet and inventing that workflow now means inventing its users too. What is specified is the seam it must satisfy, so adding it later is additive:

- It must produce the same `PublishRequest` and emit to the same topic.
- It must set `approved_by` to a Matrix user id and `origin: matrix`.
- It may use `routing.pending` / `routing.decisions`, which exist for precisely this and stay unused until then.
- It must not require publishers, validation or fan-out to change in any way.

The likely shape, recorded so the seam is not designed blind: a draft is posted to a room with its rendered media, reactions approve or reject, an approving reaction emits the request. **When a real team exists, this becomes the primary origin for anything not already living in a repo** — but that is a prediction, not a specification.

---

## Replies are the same capability, not a second path

The source-identity design scheduled reply-from-Matrix as Phase 6, sub-staged by access path. Designed separately, it would build a second write path with its own credentials handling, its own error surfacing and its own per-platform quirks.

**A reply is a post with an `in_reply_to`.** Both are "write to platform X as scope Y with these credentials". So `reply()` is a method on the same `Publisher` protocol, using the same instance, the same secret and the same result recording. Phase 6 collapses from a subsystem into a method plus a routing rule.

This is what makes the engagement half tractable: a comment arrives on a Page post, the watcher emits it with the platform's post id, the operator answers in Matrix, and the reply goes back out through the publisher that owns that scope — because the correlation key was recorded when the post was published.

---

## Validation is the load-bearing part

Platforms fail late, vaguely, and after the interesting work is done. The value of a shared validate step is turning those into pre-publish errors:

- **Instagram accepts JPEG only** and rejects PNG. Caught by comparing declared media against the instance's `image_format` constraint. (volundr#12 adds the `jpg` output kind that makes a compliant asset possible at all.)
- **Instagram cannot post text without media.** Caught by `image_required`.
- **Media must be publicly fetchable** at publish time. Caught by resolving the URL before the container call.
- **WhatsApp template rules** — outside a 24-hour window only pre-approved templates may be sent. A free-text WhatsApp publish outside a window is a validation failure, not a runtime surprise.
- **Per-platform length limits**, checked against the rendered text rather than the source.

A `--dry-run` that runs real validation against real instance config is the difference between a preview and a guess.

---

## Captions, and where AI sits

Per-destination captions, authored in the manifest or the CLI invocation. The source-identity design already settled the principle for the inbound side and it transfers unchanged: **AI adaptation is infrastructure, but opt-out per consumer.**

- `caption` — written by hand, used verbatim. Always available, always the default for anything sensitive.
- `caption_from: <publisher-id>` — reuse another target's text.
- `adapt: true` — derive this destination's text from a base caption, respecting its length limits and conventions.

A political campaign can hand-write every word while MTL lets a soccer announcement be reshaped for Instagram. That choice is per target, not per instance, because it is a judgement about *this* message.

---

## The volundr boundary

volundr states two properties this design must not break: **no custom secrets anywhere**, and **a human merge is always the publish gate**.

The split that preserves both:

- **volundr renders and gates.** It builds the flyer, previews it, visually diffs it, and never learns a Meta token exists.
- **The site repo declares intent.** `social.yml` names destinations by id and supplies captions. It holds no credentials.
- **Knarr holds identity and credentials** in-cluster, resolves publisher ids, and performs the writes.

Under the `git-merge` origin the human merge remains the gate — Knarr reacts to it rather than replacing it. Under `cli` there is no volundr involvement to preserve.

**A CI-side validator is worth adding to volundr later**: a `social.yml` that references an unknown publisher or a missing media file should fail in the PR, not at publish time. That needs only the publisher *ids*, never their secrets, so it stays inside volundr's trust model. Recorded as a follow-up, not specified here.

---

## Where the PTA actually fits

Worth stating plainly so it is not misfiled: **the PTA's stated problem is fragmentation, not publishing.** Communication is scattered across several WhatsApp groups and Facebook with little web presence, and the pain is that nobody can see it all.

That is the *inbound* half of Knarr — the aggregation the source-identity design was written for — with publishing as a secondary need. Treating the PTA as "MTL but on WhatsApp" would build the wrong thing first.

The `cli` origin is specified here anyway because it is small, it unblocks the publishing the PTA does need, and it is the origin that requires no infrastructure at all. But **the PTA's first real value is aggregation**, and that ordering belongs in whatever plan follows this design.

---

## Phasing

Ordered so that nothing is blocked on Meta developer-account verification, which is currently stuck in a platform-side loop and outside our control.

**Phase A — the publisher, proven on destinations we control.** `Publisher` protocol, `PublisherInstance` config, the fan-out consumer, `PublishResult` recording. Adapters for **Matrix** and **Discord**, both of which are already running. Origin: `cli` with `--dry-run` first. *Exit: one command publishes to two real destinations and records both ids.*

**Phase B — the `git-merge` origin.** `social.yml` schema, repo polling, deploy-completion wait, correlation of a merge to a request. Still fanning out to Matrix/Discord. *Exit: merging a PR that touches `social.yml` publishes without anyone running a command.*

**Phase C — the Meta destinations.** Facebook Page and Instagram adapters behind the same protocol, gated on the developer account unblocking. WhatsApp Cloud API alongside, since its test tier needs no business verification once an app exists. *Exit: an MTL flyer reaches a Page and an IG account from a merge.*

**Phase D — the engagement loop.** Comment and message watchers correlated by the ids Phase A recorded, routed into Matrix; `reply()` on the publishers that already exist. Facebook Messenger belongs here — it is the Page's DM surface, not a separate platform, and ignoring it would miss the channel people actually use to ask whether registration is still open.

**Phase E — the `matrix` origin.** When chat organizing exists.

Phases A and B are entirely unblocked today.

---

## Deferred, with the reason stated

- **Multi-person approval and volunteer triage.** One operator; the primitives exist unused.
- **Scheduling.** "Publish at 9am Saturday" is genuinely useful and genuinely orthogonal — it is a property of the request, not the pipeline. Deliberately out of v1.
- **Cross-posting inbound to outbound** (spot a local item, amplify it to a Page). The read side feeds the write side eventually; it needs the engagement loop first.
- **Deletion and editing of published posts.** Platforms vary wildly and the failure modes are worse than for publishing. Not until there is a reason.
- **Analytics.** Insights APIs exist for all of these; nobody has asked what question they would answer.

---

## Risks worth naming now

- **A publish is irreversible in a way a site deploy is not.** A bad merge to gh-pages is fixed by another merge; a bad Facebook post has already reached people's feeds. This asymmetry argues for `--dry-run` as the learning default and for validation that is genuinely strict.
- **Political campaign Pages carry extra Meta enforcement.** Authorization requirements, disclaimers, and stricter review. They should never be the target of a first run of anything.
- **Correlation ids are the hinge of the engagement half.** If `PublishResult` ids are not durably recorded, inbound comments cannot be tied back to what they are about, and the loop silently degrades into an undifferentiated feed. This deserves storage that outlives a pod.
- **Silent success is the failure mode this codebase keeps producing.** The router reported delivery Synapse had rejected; Strimzi ignores a topic naming a cluster that does not exist; a watcher instance can fail every cycle and look healthy. A publisher that reports a post it did not make is the same bug with a worse blast radius. **Every publisher must verify its result and surface failure loudly.**

---

## Open questions

1. **Where do `PublishResult` ids live?** Kafka is a log, not a lookup. Postgres via a DataService is the obvious answer and would be Knarr's second consumer of the shared cluster.
2. **How much does `social.yml` overlap `flyers.conf`?** Both name assets in the same directory. Merging them is tempting and probably wrong — rendering and distribution are different concerns with different owners — but it deserves a deliberate answer rather than drift.
3. **Does the Discord adapter go through the bridge or the API directly?** Via Matrix is nearly free given the bridge exists; directly is more control and another credential. Phase A should try the bridge first and record what it cannot express.
