# Knarr Source-Identity Design

**Date:** 2026-05-30
**Status:** Draft
**Component:** knarr
**Builds on:** `realms/realm-siliconsaga/docs/plans/2026-04-02-knarr-design.md`
(the OG community-fan-out design — outbound routing model, Keycloak identity).
This doc is the **inbound source-identity** complement: how content gets into
Knarr from many platforms at many identity scopes.

---

## Overview

Knarr today polls a fixed handful of community sources (Reddit, GitHub) on
behalf of one community and posts them to a shared Matrix room. To support
the actual operational use case — a community admin or moderator monitoring
**many** platforms across **personal, group, and community** identity scopes,
with the AI summarization needed to make that volume tractable — the watcher
layer needs to grow a multi-tenant identity model.

This design extends Knarr with:

1. A **source taxonomy** (identity scope × access path) that names every
   ingestion case in two orthogonal axes.
2. A **`WatcherInstance` model** (Approach A from the brainstorm) where
   each instance is a `(platform, access_path, scope, credentials_ref)`
   tuple, identical interface regardless of platform.
3. An **AI summarize + route pipeline** where summarization is non-optional
   infrastructure but per-consumer opt-out, and presentation mode is
   configurable per (scope, room) binding.
4. A **headed-browser sidecar** as shared infrastructure for the
   no-API platforms whose notifications have been enshittified.
5. A **reply-from-Matrix loop** designed in full but built in
   risk-staged sub-phases.

The cross-cutting goal: a community admin opens one Matrix client and
gets a calm, signal-not-noise view of every platform their communities
live on. The platforms can't be fixed; the *consumption surface* can be.

---

## Why this exists

### The user-experience pitch

Mainstream platforms (Facebook, Nextdoor, LinkedIn, news apps) have
degraded their notification surfaces to drive engagement — teasers,
clickbait, "open the app to see what someone said about a topic you
care about." For someone tracking several platforms across personal and
community life, the signal-to-noise ratio is hostile and every signal
requires a context-switch into a tool whose primary product surface is
designed to capture attention, not deliver information. Knarr inverts
this: aggregate the actual content, present it cleanly in one calm
surface (Matrix), use AI summarization where the consumer wants it. The
user opens one app and sees the real thing.

### The community pitch

**Knarr is for community admins and moderators, not for the casual users
who make up a platform's primary consumer base.** PTA leaders use Knarr
to triage and coordinate; regular PTA members can keep scrolling the
Facebook group themselves if they prefer. Terasology maintainers use
Knarr to monitor activity across the project's repos, social channels,
and community forums; users who just like the game can interact however
they want. The de-enshittification angle is from the perspective of
someone whose *job* is monitoring eight platforms, not from someone
idly scrolling one.

A team of community admins sharing monitoring of a Facebook Page
doesn't each need to grind through every post individually. One person
installs the app token; the community sees the Page's posts in a shared
Matrix room; the first available volunteer responds.

### Design implications (these shape the calls that follow)

- **AI summarize/route is non-optional *infrastructure*, but optional
  *per consumer*.** Different rooms/scopes can pick `summarized`
  (default for most), `raw_threaded` (for groups uncomfortable with AI
  in their pipeline — e.g. one PTA in a district may want unfiltered
  posts even if another PTA in the same Knarr instance uses
  summarization), or `both`. Raw delivery uses a thread-per-post
  pattern so the channel stays readable but nothing is withheld.
- **Per-user vs community identity is architectural**, not config
  sugar — `WatcherInstance` carries scope explicitly.
- **Reply-from-Matrix matters** for community moderation — designed
  for, with explicit risk-staged rollout.
- **Scraping for no-API platforms is justified by the WHY**, not a
  workaround. Posture: load like a normal browser (ads, page assets,
  full JS), but render *only the actual post content* to the human. We
  don't fight the ads loading; we just don't burn human eyeball time
  on the algorithmic feed that surrounds them. For anything that
  warrants real engagement, the human can click through to the
  platform manually. We're maintaining communities across platforms,
  not extracting at scale or evading bot-detection beyond what one
  logged-in browser session naturally looks like.

---

## Audience: three user groups

Knarr serves three distinct user groups with different needs, different
trust postures, and different deployment shapes. The architecture
should make all three first-class.

### Group A — Community admins and moderators

The original target. Run watcher instances on behalf of communities
they manage (Terasology maintainers, PTA leaders, sports league
admins). Hold credentials for community-scoped sources (a Page admin
token, a community-shared PAT, a Discord bot token). Consume in
shared Matrix rooms; first-available volunteer acts. Tolerant of (and
benefits from) the AI summarize layer because volume is highest here.

### Group B — Personal curators

Power users, often *also* in Group A, watching their own feeds in a
curated way (their own GitHub notifications, their YouTube channel,
their personal social presence). Hold credentials for personal-scoped
sources. Consume in personal Matrix spaces. Same trust posture as
Group A but for `user/<id>`-scoped instances. Many real-world users
are simultaneously Group A and Group B and don't experience them as
distinct.

### Group C — Community-receiving users (mundane)

People who just want to **stay in touch with a community** from
whichever platform they already use. They don't run Knarr; they don't
manage credentials beyond their own platform-of-choice account. They
receive content from one or more community spaces, delivered through
the channel they prefer (Matrix client, SMS, WhatsApp, email digest
— per the OG 2026-04-02 design's outbound fan-out side, which this
doc complements). Some communicate back through the same channel; some
just consume. Group A's job is to keep the community healthy *for*
Group C — so anything Group A does to triage and curate ultimately
serves Group C.

This doc focuses on Groups A and B (the inbound source side). Group C
is served by the OG design's outbound fan-out; the two designs share
the Keycloak identity backbone but otherwise ship orthogonally. A
single Knarr deployment serves all three groups simultaneously — the
distinctions are about which features each group exercises, not about
separate instances.

**Implication for design calls that follow:** the per-room
`presentation_mode` (summarized vs raw_threaded) opt-out matters most
to Group A consumers (a moderator might want raw passthrough into a
private review channel before the AI touches anything) and to Group
C (a community room consumed mostly by Group C users might prefer
unfiltered posts to honour their trust preferences). Group B
typically opts into summarization because the value-to-volume ratio
is highest in personal feeds.

---

## Source taxonomy

Two orthogonal axes. Every source Knarr can ingest lives at one cell of
the product.

### Axis 1 — Identity scope

| Scope | Meaning | Routes to | Example |
|---|---|---|---|
| `community/<slug>` | Content owned by a shared community | Community-visible Matrix rooms | r/Terasology, MovingBlocks/Terasology repo |
| `user/<keycloak-user-id>` | Content owned by one user's private context | That user's personal Matrix space (invite-only) | Cervator's personal GitHub notifications, Cervator's PTA Facebook group |
| `group/<keycloak-group-id>` | Content shared by a subgroup smaller than full community | That group's Matrix space | A subset of PTA leaders within a larger district-spanning Knarr instance |

Scope is set at instance creation, immutable for that instance, and
rides every event the instance emits.

### Axis 2 — Access path

Ordered by reliability and effort:

| Access path | Auth shape | Reliability | Maintenance | Examples |
|---|---|---|---|---|
| **Native API** | Token / OAuth | High | Low | Reddit, GitHub, Bluesky, YouTube Data API |
| **Admin-installed app** | OAuth + platform-side install by an admin | High | Low | Facebook Page (admin), Facebook Group (admin) |
| **Personal-login scrape** | Persistent browser session, the user's own login | Medium | Medium — selectors drift on redesign, occasional re-auth | Personal Nextdoor, non-admin FB Group, LinkedIn personal feed |
| **Email-extract + scrape** | Mailbox access + same browser session | Medium-low | Medium-high — email format drifts too | Any platform whose notification emails contain a link but not the content |
| **Paid API** | Money + token | High | Low | Twitter/X if priced reasonably for our small-community volume |

Preference order: OSS / free native API → admin-installed app → scrape →
paid-API-if-low-cost → don't-bother. Casual-community scale (~1000
posts/day total across all sources) is well under any reasonable paid
tier's break-even.

### Concrete example instances

```text
(reddit,    api,            community/terasology,    auth=anonymous)
(github,    api,            community/terasology,    auth=community-pat)
(github,    api,            user/cervator,           auth=cervator-pat)
(facebook,  admin-app,      community/terasology,    auth=terasology-page-token)
(facebook,  admin-app,      group/cervator-pta,      auth=pta-page-token)
(facebook,  scrape,         user/cervator,           auth=fb-session-profile)
(nextdoor,  email+scrape,   user/cervator,           auth=email+nd-session)
(bluesky,   api,            community/terasology,    auth=anonymous)
(youtube,   api,            community/terasology,    auth=api-key)
(youtube,   api,            user/cervator,           auth=cervator-api-key)
```

Same shape every row. The differences are entirely in the access-path
adapter behind the scenes.

---

## Identity & credentials model

### Identity primary key — Keycloak user

The OG 2026-04-02 design already establishes Keycloak as Knarr's
identity backbone (per-user attributes for Matrix mxid, WhatsApp #,
etc.). We extend that here rather than introducing a parallel identity
system. A Knarr user is one Keycloak user; their Matrix mxid is one
attribute among many; their personal Knarr space room id is another.

Scope identifiers reference Keycloak:

- `user/<keycloak-user-id>` — direct reference
- `community/<community-slug>` — community config already has stable
  slugs (`terasology`, `pta-school-name`)
- `group/<keycloak-group-id>` — subgroups are Keycloak groups (the OG
  design already uses these for subscription management)

### Credentials storage

K8s secrets, with a structured naming convention:

```text
knarr-cred-<scope-type>-<scope-id>-<purpose>
```

Examples:

```text
knarr-cred-user-cervator-fb-session       (browser session blob)
knarr-cred-community-terasology-gh-pat    (classic PAT)
knarr-cred-group-pta-school-name-fb-token (admin-installed app token)
```

Instance config carries `credentials_ref` as a nested object grouping
the secret reference fields:

```yaml
credentials_ref:
  secret_name: knarr-cred-community-terasology-gh-pat
  secret_key: GITHUB_TOKEN
```

The reconciler never reads or rotates secret contents — just
references them. Credential management is a separate CLI flow so the
reconciler stays single-purpose.

### Credential lifecycle

Three operations, all CLI-driven:

- **Create.** `knarr cred create <name> --from-file=<path>` for static
  tokens. `knarr cred capture <name> --platform=<p>` for login-capture
  flows (launches the headed-browser sidecar; user logs in once; the
  resulting session blob is captured into the secret).
- **Rotate.** Replace the secret value in place; the watcher instance
  picks it up on its next poll cycle (file-watch or short re-read
  interval — implementation detail).
- **Revoke.** Delete the secret; the instance fails gracefully and
  surfaces an alert in the Knarr admin Matrix room.

### Email-cleanup scope (Gmail)

Use the **narrow scope** path:

- Bot applies a `knarr/processed` label to messages it has fully
  extracted via the Gmail `users.messages.modify` API.
- User's own Gmail filter (one-time copy-paste setup in the Gmail web
  UI) auto-archives or trashes anything with that label.
- Bot's OAuth scope is `https://www.googleapis.com/auth/gmail.modify`
  — the least-privilege scope that supports `users.messages.modify`
  per Google's docs. Note: this scope can in principle change other
  messages too; the constraint is enforced by *our code* applying only
  the `knarr/processed` label. We deliberately avoid
  `https://mail.google.com/` (full-mailbox, includes message deletion)
  and we don't need `gmail.readonly` because the email-extract adapter
  also reads message bodies via the Gmail API.

This is doing double duty: the OAuth scope is the platform-level
ceiling, and the label itself becomes a useful human-review surface.
The user can dip into the `knarr/processed` label any time, glance
through what's there, and confirm it looks right before letting the
filter clean it up. The label is the convenient operational boundary
*and* an audit trail.

### Per-user space provisioning

When a Knarr user is provisioned, the reconciler creates
`#personal-<keycloak-user-id>:knarr.local` — a Matrix space the user
solely inhabits, marked managed, with a Keycloak-user reference
attribute. The alias derives from the immutable Keycloak user id (a
UUID), not the human-readable username, so renames and collisions
don't break the routing. The user's display-name + avatar live
separately on the Matrix room's `m.room.name` / `m.room.avatar` state
events and can be edited freely without re-aliasing.

Any future `user/<keycloak-user-id>`-scoped instances route content
into this space (with optional sub-room organisation the user adjusts
later). "Where does my personal Knarr stuff land" becomes a one-time
setup, not a per-source decision.

### Explicitly out of scope here

- The `knarr cred capture` headed-browser-login interaction flow
  (implementation detail; build during Phase 4).
- Cross-scope credential sharing (uncommon edge case; YAGNI).
- Multi-user-same-platform conflicts — naturally handled by the
  scope_id discriminator.

---

## Pipeline architecture

### Steady-state flow (no replies yet)

```text
[Source platform]
  ↓  (poll / stream, dedupe, extract)
[WatcherInstance]              ← per (platform, access_path, scope, credentials)
  ↓  (publish)
[Kafka: knarr.watch.alerts]
  ↓  (optional AI pass per consumer's preference)
[AI summarize stage]
  ↓
[Kafka: knarr.watch.alerts.enriched]
  ↓  (apply room/scope/presentation-mode rules)
[Router]
  ↓
[Matrix room]                  ← summarized OR raw-threaded OR both
```

Two-topic shape (raw → enriched) means: instances stay simple (emit
raw events), the AI stage runs once and its result is shared across
all consumers that want summarisation, and consumers that want raw can
subscribe directly to `knarr.watch.alerts` without paying for the
unused AI work. Avoids redundant LLM calls per room.

### Component shape

**`WatcherInstance`.** Identical interface regardless of access path;
the difference is which adapter it loads (`ApiAdapter`,
`AdminAppAdapter`, `ScrapeAdapter`, `EmailExtractAdapter`). Adapters
are stateless except for a dedup cursor (last-seen timestamp or event
id), which lives in a small per-instance KV store (Valkey — already
provisioned per the OG design). On poll: pull new events since cursor
→ dedupe → emit to `knarr.watch.alerts` with full metadata (instance
id, scope, access path, source event id, raw content, link back to
original).

**Logical instance vs deployment topology** — distinct concerns.
Phase 1 hosts all logical instances in **one watcher pod** (each
instance gets its own asyncio poll loop within the process), which is
the right shape while we have two instances on a single k3d cluster.
Splitting to one-pod-per-instance becomes useful when (a) per-instance
restart/scale isolation matters, (b) credential blast-radius needs
hardening, or (c) instances grow into different resource shapes
(scrape instances will want a headed-browser sidecar; API instances
won't). The `WatcherInstance` class is host-agnostic — it doesn't know
whether it shares a process with siblings or has its own pod — so
splitting later is a deployment-manifest change, not a code change.

**`HeadedBrowserSidecar`.** One pool per node (typically a homelab
desktop running an X session). Persistent profiles per scope on disk
at `~/.knarr/browser-profiles/<scope>/` so each scope keeps its own
login state. Accepts queued requests over a small internal API:
`fetch(url, profile, return_format)` → returns extracted content.
`ScrapeAdapter` and `EmailExtractAdapter` are the only producers of
this queue. The sidecar isolates fragile parts (browser-pool
lifecycle, anti-detection, login-capture flows) so they don't get
duplicated per instance.

**AI summarize stage.** Consumes `knarr.watch.alerts`, emits
`knarr.watch.alerts.enriched`. Calls Claude API (or equivalent) with
the raw content; attaches `{summary, priority, tags, model,
tokens_used}` to the original event. Per-instance rate limit caps to
control cost. Projected cost at small-community scale (~1k posts/day
× ~500 tokens each on a Haiku-class model) is single-digit dollars
per month.

**`Router`.** Reads both topics depending on subscriber preferences.
Lookup table: `(scope, room) → presentation_mode`. Routes:

- `summarized` → take from enriched, post summary as top-level message
  with link + priority tag.
- `raw_threaded` → take from raw, post brief thread-starter
  (`New post from <source>: <title>`), then post the full body as the
  first thread reply (body doesn't eat channel real-estate, but
  nothing is withheld).
- `both` → summarised at top level, full body in a thread under it.

Routing rules live in the reconciler config (extends current
`config/test.yaml`-style schema). Each `(scope, room)` binding opts
into AI summarisation independently.

### Kafka schemas (clean — no live consumers, so no compat shim)

`knarr.watch.alerts`:

```json
{
  "event_id": "...",                     // platform-stable, used for dedupe
  "timestamp": "...",                    // when the event was created upstream
  "extracted_at": "...",                 // when Knarr got it
  "instance_id": "...",                  // which WatcherInstance produced this
  "scope": "user/cervator",              // identity scope
  "access_path": "scrape",               // how it was sourced
  "platform": "facebook",
  "raw_post_ref": "https://...",         // canonical link back to the original
  "content": {
    "type": "post|comment|reaction|release|...",
    "title": "...",                      // platform's title, may be empty
    "body": "...",                       // full text
    "author": "...",                     // platform-handle
    "attachments": [...]                 // urls / mime hints, no inline blobs
  }
}
```

`knarr.watch.alerts.enriched`:

```json
{
  ... all fields from knarr.watch.alerts ...,
  "ai": {
    "summary": "...",
    "priority": "low|normal|high|urgent",
    "tags": ["..."],
    "model": "claude-haiku-4-5",
    "tokens_used": 234
  }
}
```

`knarr.write.requests` (Phase 6, see below):

```json
{
  "request_id": "...",
  "originating_event_id": "...",         // back-reference to the source event
  "scope": "user/cervator",
  "platform": "facebook",
  "access_path": "scrape",
  "action": "reply|react|delete|...",
  "payload": { ... }                     // action-specific
}
```

`knarr.write.outcomes` (Phase 6):

```json
{
  "request_id": "...",
  "status": "ok|error",
  "platform_response": { ... },          // raw platform reply where useful
  "error": "..."                         // when status=error
}
```

---

## Reply-from-Matrix loop

```text
[Matrix user reaction / reply in thread]
  ↓
[Matrix bot picks up]
  ↓  (looks up original event via stored back-reference)
[Kafka: knarr.write.requests]
  ↓
[WriteWorker]                  ← typically the same WatcherInstance pod
  ↓  (perform write via access-path adapter)
[Source platform]
  ↓  (outcome)
[Kafka: knarr.write.outcomes]
  ↓
[Matrix bot posts outcome as thread reply]
```

Write actions are higher detection-risk than reads, so the rollout is
sub-staged. Each sub-phase ships and is evaluated independently.

- **6a — Native API writes.** Reddit, GitHub, Bluesky. Lowest risk;
  APIs are designed for write actions. Auth identity is whatever the
  WatcherInstance already uses.
- **6b — Admin-installed app writes.** FB Page comments, FB Group
  posts (admin-installed). Medium risk; write quotas may differ from
  reads.
- **6c — Email-reply writes.** Reply to the notification email itself
  if the platform threads them (Discourse, Slack, GitHub all do).
  Medium-low risk; standard SMTP, no bot detection.
- **6d — Personal-login-scrape writes.** Headed-browser navigates back
  to the post, fills the reply box, submits. **High risk.** Burner
  account only; never the user's real identity. Not committing to
  building this — capture the design, defer the build pending real
  demand.

---

## Boundaries with adjacent systems

Captured so future contributors don't accidentally rebuild what's
already there.

- **Knarr's own infra observability is Heimdall's job.** Synapse
  uptime, watcher pod health, Kafka consumer lag → Heimdall scrapes
  Knarr's metrics endpoints. Knarr does not monitor itself.
- **Alert-escalation seam** (the dormant one from Loki/heimdall #5):
  AlertManager → webhook to a small Knarr endpoint → post to a
  community-ops Matrix room (e.g. "watchers down for 10m, who's
  around?"). Not built yet; this design preserves the seam by ensuring
  the router accepts inbound webhook-shaped events (one more producer
  for `knarr.watch.alerts`).
- **AI-translation shared primitive (future).** Both Heimdall (the
  AlertManager → ntfy bridge) and Knarr (post summarisation) want a
  "render this event for human consumption" service. Build separately
  for now; if the convergence holds across a year of operation,
  extract a shared service. Not gating either system on the other.

---

## Forward-looking: deployment topologies and satellites

Phase 1 hosts every `WatcherInstance` in a single watcher pod inside
the central Knarr deployment. The architecture should not preclude
two future-state shapes; this section captures them so today's design
calls stay compatible without committing to building either yet.

### Satellite hosts for sensitive-credential scopes

Power users (Group A and B) may want to run sensitive parts of their
own Knarr footprint on hardware they control — a homelab box, a
personal VPS — rather than handing credentials to the central
deployment. Examples:

- A user's personal Facebook session cookie should ideally never
  leave their machine. Today's design assumes it lives in a K8s
  secret on the central cluster; a satellite shape lets it stay on
  the user's host.
- The Gmail-API token for the email-extract path is similarly
  personal. A satellite can hold it, do the IMAP+scrape locally, and
  publish the extracted events upstream.
- Headed-browser scrape sessions ("act as me on Nextdoor") look more
  legitimate when they come from the user's actual home IP rather
  than a cloud egress.

The shape: a satellite is a small process — much lighter than a full
Bluesky PDS, but loosely the same federation spirit — that hosts
`WatcherInstance`s scoped to that user (or a subgroup they
administer). The satellite holds its own credentials, runs its own
adapters (including the headed-browser sidecar where needed), and
publishes events into the central Knarr's Kafka over an
authenticated channel (mTLS or token). Routing, AI summarize, and
Matrix delivery still happen centrally.

For community-managed sources the central deployment can advertise
"this community needs an update poll, who's available?" and one of
the online satellites takes the cycle — distributing the "looks like
a human" burden across volunteers, rotating naturally with who's
online.

**What today's design needs to do (and not do) to stay compatible:**

- The `WatcherInstance` interface is already host-agnostic — it
  doesn't know whether it shares a process with siblings or owns its
  own pod or runs on a satellite. ✓
- The `scope` axis already cleanly separates "who owns this content"
  from "where it physically runs." ✓
- The Kafka publish path is already a writer abstraction — a
  satellite using authenticated Kafka credentials is a config
  change, not an architectural change. ✓
- A future `host_hint` field on `InstanceConfig` (`central` /
  `satellite/<id>`) could carry placement preference, but adding it
  now is YAGNI — Phase 1 only has a central host, and adding it
  later is a non-breaking schema extension.
- The `knarr cred capture` flow (Phase 4 / Phase 0 spillover) ought
  to be runnable from either central or satellite; the CLI
  abstraction means the same binary works in both places.

**Cost worth being honest about.** Satellites add real operational
complexity — secure Kafka ingress, satellite lifecycle (start/stop,
version drift), credential bootstrap on a new satellite, observation
across hosts. Probably worth it for the trust posture and
legitimacy-of-action benefits, but not before there's enough demand
to justify the operations work. Not on the immediate roadmap; the
design just leaves the door open.

### Per-instance pods within the central deployment

A simpler precursor to satellites: split the single watcher pod into
one pod per instance, all on the central cluster. Useful when (a)
per-instance restart isolation matters, (b) credential blast-radius
needs hardening, or (c) instances grow into different resource shapes
(scrape instances will want a headed-browser sidecar; API instances
won't). This is a deployment-manifest change, not a code change —
the `WatcherInstance` class doesn't care whether siblings share a
process. Likely the first split after Phase 4 introduces the
headed-browser sidecar, since that pod really doesn't want to be
co-located with stateless API-poll instances.

### Federation parallels worth knowing about

- **Matrix federation** already does the heavy lifting for the
  outbound side (a Knarr-on-satellite-A user's account can federate
  with a Knarr-on-satellite-B user via standard Matrix; nothing
  Knarr-specific needed).
- **AT Protocol PDS** is the closest conceptual fit for the
  satellite model on the inbound side — each user (or small group)
  could host their own data, with the central Knarr doing the
  fan-in/out.
- We're not committing to building either pattern now; the existing
  design just stays compatible so a future ATProto-style federation
  layer (or a custom one) could land without re-architecting.

---

## Phasing

Six phases, each independently shippable. Phase 1 is the chosen MVP
driver (refactor first, no new source).

### Phase 0 — Identity scaffolding

- Extend Keycloak user model with `personal_space_room_id` attribute.
- Reconciler provisions `#<user>-personal:knarr.local` for any user
  without one.
- K8s secret naming convention documented and enforced.
- New CLI: `knarr cred create | capture | rotate | revoke`.
- Define `WatcherInstance` config schema.

Boundary with Phase 1 is fuzzy — Phase 1 needs all of this to land,
likely the same PR.

### Phase 1 — WatcherInstance refactor (the MVP)

Strictly a refactor — no user-visible behavior change. Single
responsibility.

- Today's `reddit_watcher.py` and `github_watcher.py` become
  `ApiAdapter`-backed `WatcherInstance` deployments.
- Test config rewrites into two instance rows:
  ```
  (reddit, api, community/terasology, auth=anonymous)
  (github, api, community/terasology, auth=community-pat)
  ```
- Kafka schema lifts to the new shape in one go (instance_id, scope,
  access_path, etc. land — no back-compat shim since nothing live).
- Router stays a thin pass-through for now: consumes
  `knarr.watch.alerts`, posts one Matrix message per event to the
  community room. No presentation_mode logic yet; Phase 2 introduces
  it alongside the AI stage.
- BDD lifecycle test adjusted to the new shape; k3d apply re-validated
  end-to-end.

**Exit criterion:** today's Reddit→Matrix pipeline still works, but
via the new architecture. Regression test: same alerts land in same
room with the same message content.

### Phase 2 — AI summarize stage + presentation modes

- New `ai_summarize` stage consuming `knarr.watch.alerts`, emitting
  `knarr.watch.alerts.enriched`.
- Router learns the `(scope, room) → presentation_mode` lookup. Three
  modes ship together: `summarized` (read enriched, top-level post),
  `raw_threaded` (read raw, thread-starter + body-in-thread), and
  `both` (summarised at top, body in thread).
- Community rooms default to `summarized`; test rooms get a mix of
  `raw_threaded` and `both` configured to keep all paths exercised.
- LLM-call cost monitoring + per-instance rate-limit caps.

**Exit criterion:** community room receives summarised posts with
priority labels; admin can flip a single room between any of the three
modes by changing config and re-applying.

### Phase 3 — First new source on the new model

- **Bluesky** as the first new platform. AT Protocol is open, free,
  low-quota-risk; cleanest validation that adding a platform is a
  contained workflow.
- New `ApiAdapter` for Bluesky, one new instance row, one new
  community room or sub-room.
- No new architectural primitives — this phase is a test that Phase
  1's abstractions hold up to a non-trivial new platform.

**Exit criterion:** adding a platform is a documented, repeatable
process (`docs/operations.md` extended).

### Phase 4 — First scrape source (HeadedBrowserSidecar)

- Build the `HeadedBrowserSidecar` (Playwright pool, per-scope
  persistent profile, queued fetch API).
- Build `ScrapeAdapter` against the sidecar.
- First target: a real Facebook Page Cervator administers (admin-app
  access path first — Graph API, sidecar not yet exercised), then a
  personal-login scrape case (Cervator's personal FB feed or a
  non-admin FB Group) to exercise the sidecar end-to-end.
- The `knarr cred capture --platform=facebook` flow ships here.

**Exit criterion:** content from one personal-login-only platform
reaches a `user/cervator`-scoped Matrix space without manual
intervention beyond the one-time login.

### Phase 5 — Email-extract path

- `EmailExtractAdapter` — Gmail API for both read and label apply
  (Gmail-first because the narrow-scope cleanup story depends on
  labels). Adapter is provider-aware: if/when we add non-Gmail
  mailboxes later, that's a separate adapter variant (IMAP read + a
  different cleanup story like move-to-folder).
- `gmail.labels`-only OAuth scope; documented one-time Gmail filter
  setup on the user side (filter auto-archives or trashes anything
  labeled `knarr/processed`).
- First target: Nextdoor (highest-pain de-enshittification case) or a
  non-admin FB Group whose email digests link to posts.
- Sidecar reused for the scrape half (follow the link in the email,
  scrape the actual post content).

**Exit criterion:** Nextdoor (or chosen first target) emails get
processed, content posted to Matrix, source emails labeled
`knarr/processed`. The user's Gmail filter takes it from there.

### Phase 6 — Reply-from-Matrix (sub-staged)

Each sub-phase gated independently.

- **6a** Native API replies (Reddit, GitHub, Bluesky).
- **6b** Admin-installed app replies (FB Page, FB Group).
- **6c** Email-reply writes.
- **6d** Personal-login-scrape writes — designed for, deferred unless
  real demand surfaces.

---

## Migration story

Nothing is in production yet, so "migration" is an in-place rewrite of
the test setup:

- Phase 1 PR replaces today's two watcher deployments with the new
  `WatcherInstance` deployments + the Kafka schema flip + the router
  rewire, all in one PR.
- BDD lifecycle test adjusted.
- k3d-nordri-test gets a fresh apply.
- No live consumers to worry about.

If Knarr later runs against a live community before all phases are
done, the existing `(reddit, github) → community/terasology` pipeline
already covers what's in production; later phases add capability
without breaking what's there.

---

## What this design explicitly defers

- The OG 2026-04-02 design's **outbound multi-channel fan-out** (SMS,
  email, WhatsApp) — orthogonal to this work, ships in parallel or
  after, shares the same Keycloak identity.
- The reply-loop's Phase 6d (scrape-writes) — designed for,
  deliberately not committed to building.
- Cross-scope credential sharing (uncommon enough to leave as YAGNI).
- A shared AI-rendering primitive across Knarr and Heimdall — wait
  for convergence to prove out across a year of operation.
- The `knarr cred capture` headed-browser-login interaction flow
  (implementation detail; build during Phase 4).
- An admin web UI for managing instances / scopes / credentials —
  CLI-only for now per current Knarr operational style.

---

## Future public-docs candidate

The three-user-groups framing (Audience section above) deserves a
top-line position in eventual public docs so expectations are set
early. It changes the answer to "should I use Knarr?" from a confusing
maybe to a clear yes/no based on the reader's role: Group A (community
admins/moderators) and Group B (personal curators) operate Knarr;
Group C (community members) is *served* by it — usually without
realising Knarr is between them and the community they care about.

A public landing page that opens with "Knarr is the tool community
admins use to keep their members in touch — without forcing anyone
through ten different platforms — and a personal feed-curation tool
for power users along the way" captures all three groups concisely.
