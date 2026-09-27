# Knarr Community Reach Design — the calendar as source, platform-native membership, and scheduled digests

**Date:** 2026-09-27
**Status:** Draft, for discussion (no implementation planned yet)
**Component:** knarr
**Builds on:** [the OG 2026-04-02 design](https://github.com/SiliconSaga/realm-siliconsaga/blob/main/docs/plans/2026-04-02-knarr-design.md) (subscription model, display modes, "meet people where they are") · [Source-Identity Design](2026-05-30-knarr-source-identity-design.md) (WatcherInstance, scope axis, Group C) · [Publish Design](2026-08-30-knarr-publish-design.md) (PublisherInstance, capabilities, origins)

---

## Overview

The three earlier designs describe how Knarr reads platforms, how it writes to them, and who it serves. This note fills the gap between them for the audience the source-identity design calls **Group C**: parents and community members who never run Knarr and just want to hear about things through whatever they already use. It comes out of setting up wopta.org and its district-wide PTA calendar, and from the observation that no single channel reaches everyone.

Three additions:

- **The calendar is the source of truth for events.** A Google Calendar becomes a first-class `WatcherInstance`, and every other surface (web page, email, WhatsApp, SMS, social posts) is a projection of it. Nobody types an event twice.
- **Where a platform has native membership, the platform is the subscription store.** A Google Group, a WhatsApp group, or a Facebook group already knows who is in it and already handles join and leave. Knarr posts to the group and never holds the member list. Keycloak keeps subscriptions only for channels with no native group of their own (SMS) and for operator identity.
- **Digests are a scheduled origin.** "This week at Mount Pleasant PTA" is a `PublishRequest` assembled from calendar events and announcements on a schedule, approved by policy rather than by a person, rendered once per channel.

None of this changes the pipeline as designed. The calendar watcher is one more adapter on the inbound side that runs today. The digest and the Google Group publisher sit on the publish design's Phase A (the `knarr.publish.requests` topic, scope authorization, idempotent fan-out), which is specified but not built, so nothing outbound in this note runs on the currently deployed pipeline.

---

## Why now

Mount Pleasant PTA is standing up wopta.org as its own domain, with a Google Workspace for Nonprofits activation in progress and a district calendar page listing all eleven West Orange PTAs plus the school district's own feed. The Council of PTAs is about to be shown that page. The publish design already noted that **the PTA's first real value is aggregation, not publishing**; this note is the concrete version of that. The pieces below are ordered so that the first two phases need no Knarr at all, which is what makes the Council pitch honest.

The thalamus note on volunteering states the constraint plainly: *not another app to babysit; the front door stays where people already are.* Every channel below is one parents already have open.

---

## Principle: one canonical event, many projections

An event exists exactly once, in the PTA's Google Calendar. Everything else derives from it:

| Surface | How it derives | Latency | Who maintains it |
|---|---|---|---|
| wopta.org calendar page | Google embed, live | seconds | nobody |
| A parent's own calendar app | subscribed by link (Google) or ICS (Apple, Outlook) | minutes to a day | nobody |
| Weekly email digest | Knarr digest consumer reads the calendar watcher | scheduled | policy |
| WhatsApp / Matrix "this week" post | same digest, plain-text render | scheduled | policy |
| Facebook / Instagram event flyer | publish pipeline, human-approved, media from flyer-kit | when someone decides | operator |
| SMS reminder | digest variant, one line, opted-in numbers only | scheduled | policy |

Announcements that are not events (a fundraiser link, a call for volunteers) originate in Matrix, the CLI, or a site merge as the publish design already covers. The digest carries both kinds; only the event kind has the calendar as its source.

A corollary worth stating: the Givebacks site's calendar should embed the Google calendar rather than being a second place to enter events. Two calendars is the failure mode this principle exists to prevent.

---

## Group C channel matrix, for a PTA

Read across: what a parent does, who holds the membership, what Knarr does, and when it becomes possible.

| Channel | Parent action | Membership lives in | Knarr's job | Phase |
|---|---|---|---|---|
| Own calendar app | click "Add to Google Calendar" or the ICS link on wopta.org | the parent's device | none | 0 |
| Google Group (email) | join the group, or be added by the PTA | Google | post digests and announcements to the group address | 2 |
| Google's own calendar notifications | join the group, add the shared calendar from the invitation, pick notification settings | Google | none; the admin shares the calendar with the group once, each member does the rest | 0 |
| WhatsApp group | join the group | WhatsApp | post the digest through the bridge; surface replies in Matrix | 3 |
| Facebook group / Page | follow or join | Meta | post approved items; surface comments (gated on Meta developer access) | 4 |
| Instagram | follow | Meta | post approved flyers | 4 |
| SMS | text JOIN MPE to the Knarr number | Keycloak, via the OG opt-in flow | one-line reminders, digest headline | 4 |
| wopta.org "this week" | visit the page | none | none; a daily site rebuild renders the next seven days from the ICS | 0 |
| Matrix / Element | join the space | Synapse | everything, for operators and power users | 1 |

The phase numbers are this note's phasing, below. Phase 0 is entirely outside Knarr.

---

## Calendar as a `WatcherInstance`

A calendar is a source like Reddit or GitHub: it has items, items change, and the interesting output is "something new or different since last time." It fits the instance model with no new primitives.

```yaml
instances:
  - id: calendar-pta-mpe
    platform: calendar
    access_path: api                 # anonymous: the calendar's public ICS URL, no credentials
    scope: group/pta-mpe
    polling:
      interval_seconds: 900
    platform_config:
      ics_url: https://calendar.google.com/calendar/ical/<id>/public/basic.ics
      upcoming_lead_times: [P7D, P1D]  # emit an event.upcoming alert this far ahead
    target_room: pta-events
    # no credentials_ref — public calendars need none
```

**Alert kinds** on `knarr.watch.alerts`, using the existing envelope with `content.type` set to one of:

- `event.created`, `event.updated`, `event.cancelled` — the diff between two ICS snapshots, keyed by `UID` plus `RECURRENCE-ID` (an overridden or cancelled single occurrence of a recurring event carries the series' `UID` and its own `RECURRENCE-ID`, so `UID` alone would make it collide with the series), with `SEQUENCE` and `LAST-MODIFIED` deciding what counts as a change. Updates carry the changed fields so a digest can say "moved to 5pm" rather than reprinting the event.
- `event.upcoming` — emitted once per (event, lead time) when the calendar's clock crosses the threshold. This is the reminder primitive. It is what lets a "night before" SMS and a "this week" email be the same consumer with different lead times.

**Cursor.** ICS has no change feed, so the cursor is the previous snapshot: a map of `(UID, RECURRENCE-ID)` to a content hash, plus the set of `(UID, RECURRENCE-ID, lead_time)` triples already emitted. Small enough for Valkey, and it makes the adapter honest in the way the source-identity review demanded: the cursor advances only after Kafka confirms delivery, so a failed publish re-diffs from the same snapshot.

**Why anonymous ICS first.** The Google Calendar API with push notifications is better (seconds instead of fifteen minutes, no diffing) and belongs to the `admin-app` access path with a Workspace service account. It is also another credential and another OAuth consent flow. The public ICS gets a working calendar source with zero secrets, which is the right first step for a component whose history is full of silent-failure lessons. Upgrade the access path later without touching consumers.

**A trap to record now.** A Google calendar created *by importing an ICS URL* (the district feed, for example) refreshes on Google's schedule, often most of a day. Watch the original ICS, not Google's re-export of it. The `ics_url` field takes whichever is closest to the source.

---

## Google Group as a destination, and as the membership store

### The publisher

A Google Group is an email list with an archive. As a `PublisherInstance` it is a broadcast surface with threads:

```yaml
publishers:
  - id: mpe-families-list
    platform: google-group
    surface: list
    access_path: smtp                # send as the WOPTA Workspace user to the group address
    scope: group/pta-mpe
    platform_config:
      address: mpe-families@<domain>
      reply_to: <the PTA's public mailbox>
    credentials_ref:
      secret_name: knarr-cred-group-pta-mpe-smtp
      secret_key: SMTP_APP_PASSWORD
```

Capabilities, in the publish design's terms: `post` (a new thread) and `comment` (a reply in an existing thread, by `In-Reply-To`). No `message`, no `react`, no `edit`; email has none of those. The `ObjectRef` is the `Message-ID`, which is enough to thread a follow-up.

SMTP is deliberately the first access path. The Groups API adds membership counts and settings management, but nothing the digest needs, and it needs domain-wide delegation. Start with the thing that is one app password away.

### The membership rule

The OG design stores every subscription in Keycloak with a Valkey cache in front. That is right for SMS, where nothing else can hold the list. It is wrong for email once a Google Group exists, because it duplicates a list Google already maintains, and it puts parents' addresses inside Knarr for no benefit.

So the rule, generalising what the OG design already says about WhatsApp ("users who join the bridged group are implicitly subscribed"):

> **Where the platform has native membership, the platform is the subscription store.** Knarr posts to the group. Join, leave, bounce handling, and the archive are the platform's problem. Keycloak holds subscriptions only for channels with no native group (SMS) and holds identity for operators and power users.

Consequences:

- Knarr never sees a parent's email address. The digest goes to one address. This is the single biggest privacy improvement available and it costs nothing.
- The calendar can be shared with the same group. That grants *access*, not notifications: each member still adds the calendar from the sharing invitation (whether it appears automatically depends on the group's settings) and then chooses their own notification settings, which is where event reminders, new/changed/cancelled event emails, and the daily agenda live. A parent who wants "the household calendar experience" can get it, but it is per-person setup. The Knarr digest is the one email that reaches every member with no setup on their side, and the design treats it as the guaranteed path.
- `!knarr subscribe <email> ...` from the OG design becomes "add them to the group," a thing the PTA can do in the Groups UI without an operator.
- If the group allows members to post, it is also a **source**: a discussion list. That makes it a `WatcherInstance` too (Groups API, or the Phase 5 email-extract path against the WOPTA mailbox). Whether members may post is a per-PTA policy decision, not a Knarr one; both shapes are supported.

---

## Digests as a scheduled origin

The OG design has digests in two places: a `mode: digest` flag on a subscription, and the router's threaded and flat digest display modes for alerts landing in Matrix. Both are about presentation. What Group C needs is a digest as **content**: assembled on a schedule, rendered per channel, sent through the publish pipeline like anything else.

### The `schedule` origin

The publish design's origin table gains a row:

| Origin | Fits | Approval is | Status |
|---|---|---|---|
| `schedule` | Any community with a calendar and a routine to keep | A policy set once, optionally with a Matrix reaction gate for the first weeks | Proposed here |

An origin's contract is that it produces a validated, authorized `PublishRequest` and asserts a human approved it. `schedule` is a **stated exception** to the per-request half of that: the human approved the *policy* (the template, the targets, the cadence) when the `DigestSpec` was merged into config, so `approved_by` names the config author and `approved_at` the merge commit, and downstream fan-out must accept that shape only when `origin == "schedule"`. When this is implemented, `schedule` joins `git-merge | cli | matrix` in the `PublishRequest` schema and the validator, and the publish design's origin table gets this row. A `review: matrix` option restores a per-request human gate: it posts the rendered digest into an operator room and waits for a reaction before sending, which is how the first few weeks should run.

### `DigestSpec`

```yaml
digests:
  - id: mpe-weekly
    scope: group/pta-mpe
    schedule: "0 18 * * 0"           # Sunday 6pm
    timezone: America/New_York       # required; the cron is evaluated in this zone, DST included
    window:
      events: P7D                     # calendar events starting in the next 7 days
      announcements: since_last_run   # items from the announcements room since the previous digest
      changes: since_last_run         # event.updated / event.cancelled since the previous digest
    sources:
      calendars: [calendar-pta-mpe, calendar-district]
      rooms: [pta-announcements]
    targets:
      - publisher: mpe-families-list
        verb: post
        render: email
      - publisher: mpe-families-room   # a MATRIX publisher whose room is bridged to the WhatsApp group;
        verb: post                     # post is valid on Matrix. A native WhatsApp publisher is
        render: plain                  # Conversational only and would need `message` plus a thread
    quiet: skip_if_empty
    review: matrix                    # or: policy

  - id: mpe-tomorrow
    scope: group/pta-mpe
    schedule: "0 19 * * *"
    timezone: America/New_York
    window:
      events: P1D
    sources:
      calendars: [calendar-pta-mpe]
    targets:
      - publisher: mpe-sms
        verb: message
        render: line
    quiet: skip_if_empty
    review: policy
```

### How it runs

A **digest consumer** reads two topics: `knarr.watch.alerts` for calendar events, and `knarr.messages.inbound` for announcements from the configured rooms, since Matrix room traffic enters Kafka on that topic and not on the alerts topic. The Matrix-to-Kafka direction of the router is still marked planned in `docs/architecture.md`, so the announcements half of a digest depends on it; the calendar half does not. The consumer keeps the window per digest and on schedule assembles one `PublishRequest` per digest with one `Target` per configured publisher. It emits to `knarr.publish.requests` and the publish design's fan-out does the rest: scope authorization, per-target validation, idempotent delivery, `ObjectRef` recording.

- **`request_id`** is the SHA-256 of `scope`, `digest_id`, and `period_start` (RFC 3339, UTC), joined by newlines, hex-encoded. A canonical encoding and a fixed algorithm, never a language `hash()` (Python's is per-process randomized), so a scheduler that restarts and re-runs the same period produces the same id and reposts nothing.
- **Render styles** are a small fixed set: `email` (HTML with a plain-text part, an unsubscribe line pointing at the group), `plain` (WhatsApp and Matrix, short lines, no markup), `line` (SMS, one event per message, under 160 characters), and later `card` (an image for Instagram via flyer-kit). Rendering is a template per style, not an AI step. The publish design's `adapt: true` remains available per target for the day someone wants it.
- **`quiet: skip_if_empty`** is the default and should stay the default. A digest with nothing in it teaches people to ignore digests.
- **Changes get their own section.** "Moved: Movie Night now starts at 6:30" is more useful than the whole week reprinted. Cancellations of events inside the next 48 hours bypass the schedule and go out as an immediate `plain` and `line` post; that is the one case where a calendar edit should interrupt someone.
- **Replies come back.** `Reply-To` on the email points at the PTA mailbox; the Phase 5 email-extract watcher surfaces those replies in Matrix. WhatsApp replies arrive through the bridge already. This closes the loop the publish design describes: everything that comes back lands in one place.

---

## Scope model for a district

The source-identity design's three scopes map cleanly onto how the district is organised, and it is worth choosing now so calendar keys, subdomains, groups, and rooms all use the same names:

| Thing | Scope |
|---|---|
| West Orange Council of PTAs, the district calendar, anything all PTAs share | `community/wopta` |
| One school's PTA: its calendar, its Google Group, its WhatsApp group, its rooms | `group/pta-<key>` where `<key>` matches `_data/calendars.yml` on wopta.org (`mpe`, `gregory`, `redwood`, …) and the intended subdomain (`mpe.wopta.org`) |
| An operator's own feeds | `user/<id>` |

Public calendars are public by definition, so a `community/wopta` digest may include every PTA's public events. Anything a PTA does not put on its public calendar never leaves its `group/` scope. The scope authorization rule in the publish design then does real work: a request scoped `group/pta-mpe` cannot reach `group/pta-redwood`'s group address even by typo.

**This revises the source-identity design's scope table**, which names group scopes `group/<keycloak-group-id>`. Here `group/` scopes are slugs, exactly as `community/` scopes already are (`community/terasology` is a slug in the live config). A slug is what appears in config, calendar keys, subdomains, and room names, and it survives a Keycloak rebuild; a Keycloak group id, where a Keycloak group exists, becomes an attribute *of* the group rather than its name. Authorization compares slugs, and the source-identity design's table should be updated to say so when this note is accepted. Whether a Keycloak group is needed per PTA at all stays open: under the membership rule above the members are in Google and WhatsApp, and the Keycloak group would hold only that PTA's operators.

---

## Phase 0, which needs no Knarr

Everything in this section is possible with the wopta.org repo and a Workspace admin console. It is the honest version of the Council pitch, and it front-loads the parts that give parents value on day one.

1. One public Google Calendar per PTA, owned by the WOPTA account, each PTA's contact given edit rights. Already designed into wopta.org; IDs are pasted into `_data/calendars.yml` as they exist.
2. One Google Group per PTA (`<key>-families` at the WOPTA domain) once Workspace is active. Anyone can join; posting rights are the PTA's choice. The calendar is shared with the group, which gives members Google's own event notifications.
3. A subscribe page on wopta.org: for each PTA, the calendar links, the group join link, the WhatsApp invite, the Facebook group, each with a QR code. One page to put on a flyer.
4. A daily site rebuild (a scheduled GitHub Action) so the calendar page can carry a static "next seven days" list rendered from the ICS. That makes the site itself a channel for people who will not add a calendar, and it costs one workflow file.

When Knarr arrives, it slots in behind these without changing any of them.

---

## Phasing within Knarr

Ordered so each step is useful on its own and none waits on Meta.

**Phase 1 — calendar watcher.** The `calendar` platform with the anonymous ICS access path, one instance per PTA calendar plus the district feed, emitting `event.*` alerts. This is a pure `ApiAdapter` in the source-identity design's terms and arguably an easier first new platform than Bluesky: no auth, a stable format, and a diff-based cursor that exercises the delivery-confirmation rule. One honest limit: the router today posts every alert to the single configured room (`MATRIX_ROOM_ID`); per-instance `target_room` routing is a source-identity Phase 2 item, so a dedicated `#pta/events` room waits on that or on this phase carrying the routing change. *Exit: an event added on a phone appears in the configured watch room within fifteen minutes; editing it produces an `event.updated` with the changed fields.*

**Phase 2 — Google Group publisher and the weekly digest.** The `google-group` publisher over SMTP, the `schedule` origin, the digest consumer with the `email` render, `review: matrix` on. Needs the publish design's Phase A primitives (authorization, idempotent fan-out, `ObjectRef` persistence) and gives them a second real destination. *Exit: Sunday evening, a digest lands in the group's archive, and re-running the scheduler sends nothing.*

**Phase 3 — the same digest into chat.** `plain` render to a Matrix room bridged to the PTA's WhatsApp group (the Layer 1 bridge from the OG design) and to the PTA's Matrix rooms. Replies arrive through the bridge. *Exit: one `DigestSpec`, two targets, both delivered, both recorded.*

**Phase 4 — thin and gated channels.** The `line` render to SMS through the OG opt-in flow, and Facebook and Instagram targets when the Meta account unblocks. Nothing here is new design; it is the existing plans meeting a digest that already exists.

---

## Policy, decided up front

- **Knarr holds no parent contact details for channels that have native membership.** The Google Group holds emails; WhatsApp holds numbers. Only SMS opt-ins live in Keycloak, and only because nothing else can hold them.
- **Public calendars carry no children's names.** "Kindergarten field trip" not "Ms. Lopez's class with …". This is a PTA policy to write into the calendar guide, not something Knarr can enforce, but the digest templates should never add a name field that would invite it.
- **Every email digest has an unsubscribe line** pointing at the group, and the group's archive is members-only by default.
- **One operator, still.** Digest review is a Matrix reaction for the first weeks and a policy thereafter. Nothing here introduces a second approver.

---

## Risks worth naming

- **Digest fatigue.** Weekly is the ceiling for email. The nightly variant exists for SMS only, where a one-liner is the norm. `skip_if_empty` is not optional.
- **Change storms.** A volunteer reshuffling a calendar generates a burst of `event.updated`. The digest's "changes" section absorbs them; only imminent cancellations interrupt. The watcher should coalesce edits to the same `UID` within one poll.
- **Two calendars.** If the Givebacks calendar keeps being edited independently, the principle fails. The remedy is organisational: embed, do not duplicate.
- **Deliverability.** Email from `@wopta.org` needs SPF and DKIM set, which Workspace does on activation. A digest that lands in spam is worse than none; Phase 2's exit criterion should include a check against a Gmail and an Outlook inbox.
- **The silent-success family of bugs.** A digest that is "sent" but bounced from the group is the publish design's headline risk pointed at parents. The SMTP publisher must treat a rejected recipient as a failure, and the Group's own bounce handling should be watched from the operator room.

---

## Open questions

1. **Posting rights on the groups.** Announce-only lists are simpler and safer; discussion lists make the group a source too and change the moderation load. Probably per-PTA, defaulting to announce-only.
2. **SMTP as one Workspace user versus domain-wide delegation.** One user with an app password is enough for Phase 2; delegation becomes worth it when several PTAs post as themselves.
3. **Where the digest consumer's window state lives.** Valkey is enough for "since last run"; the `ObjectRef` store in Postgres is the durable record of what was actually sent.
4. **Templates versus Autoboros.** Start with plain templates. Hand rendering to Autoboros only when a target genuinely needs adaptation, per the publish design's opt-in.
5. **Onboarding the other ten PTAs.** A group, a calendar, and a `DigestSpec` per PTA is a template stamped eleven times. Whether that is a CLI command (`knarr pta add redwood`) or a config convention is a Phase 2 detail, but it should be decided before the second PTA rather than the eleventh.
