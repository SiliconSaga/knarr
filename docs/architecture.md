# Knarr Architecture

## The Big Picture

Knarr has two independent data planes: **Matrix** (real-time chat bridging) and
**Kafka** (event bus for automation, routing, and integration). They connect
through the router bot, but can operate independently.

```mermaid
flowchart TB
    subgraph "External Platforms"
        discord["Discord"]
        whatsapp["WhatsApp"]
        sms["SMS"]
        reddit["Reddit"]
        github["GitHub"]
        email["Email"]
    end

    subgraph "Matrix Plane (real-time chat)"
        synapse["Synapse Homeserver"]
        bridge_discord["mautrix-discord"]
        bridge_whatsapp["mautrix-whatsapp"]
        bridge_sms["mautrix-gmessages"]
        element["Element clients"]
    end

    subgraph "Kafka Plane (event bus)"
        kafka["Kafka"]
        kafka_ui["Kafka UI"]
    end

    subgraph "Knarr Services"
        router["Router Bot"]
        watchers["Watchers"]
        direction TB
    end

    subgraph "Identity & Cache"
        keycloak["Keycloak"]
        valkey["Valkey"]
    end

    discord <--> bridge_discord <--> synapse
    whatsapp <--> bridge_whatsapp <--> synapse
    sms <--> bridge_sms <--> synapse
    element <--> synapse

    reddit --> watchers
    github --> watchers
    watchers --> kafka

    router <--> synapse
    router <--> kafka
    router --> valkey
    keycloak -.->|identity sync| valkey

    kafka_ui --> kafka
```

## Two Planes, Loosely Coupled

### Matrix Plane

Synapse + mautrix bridges handle **real-time bidirectional chat**. A message sent
in Discord appears in a Matrix room and vice versa. This works without Kafka
running — bridges talk directly to Synapse via the appservice API.

```mermaid
flowchart LR
    discord["Discord"] <-->|"mautrix-discord"| synapse["Synapse"]
    whatsapp["WhatsApp"] <-->|"mautrix-whatsapp"| synapse
    sms["SMS"] <-->|"mautrix-gmessages"| synapse
    element["Element"] <--> synapse
```

**What lives here:** All human conversation. If two parents are chatting in a
WhatsApp group bridged to Matrix, that's entirely on the Matrix plane.

### Kafka Plane

Kafka is the **event bus for automation**. Watchers publish alerts, the router
consumes them, and future services (chatbots, archivers, content adapters)
connect here.

```mermaid
flowchart LR
    watchers["Watchers"] -->|"publish"| kafka["Kafka"]
    kafka -->|"consume"| router["Router Bot"]
    kafka -->|"consume"| autoboros["Autoboros (future)"]
    kafka -->|"consume"| scribe["Scribe archiver (future)"]
    kafka_ui["Kafka UI"] -->|"browse"| kafka
    test["Test harness"] -->|"publish/consume"| kafka
```

**What lives here:** Platform alerts, routing decisions, content drafts, identity
change events. Anything that benefits from durability, replay, or multiple
consumers.

### The Router Bridges Both Planes

The router bot is the link between Matrix and Kafka. It has two roles:

**Kafka → Matrix (active today):**
Consumes watcher alerts from Kafka and posts them to Matrix rooms.

**Matrix → Kafka (planned):**
Listens to Matrix room events and publishes them to Kafka topics. This is how
bridged chat (e.g. a WhatsApp message that arrives in a Matrix room) enters the
Kafka plane for archiving, routing decisions, or chatbot processing.

```mermaid
flowchart LR
    subgraph "Kafka → Matrix (active)"
        watchers["Watchers"] --> kafka1["knarr.watch.alerts"]
        kafka1 --> router1["Router"]
        router1 --> matrix1["#social-watch room"]
    end
```

```mermaid
flowchart LR
    subgraph "Matrix → Kafka (planned)"
        whatsapp["WhatsApp msg"] --> synapse["Synapse"]
        synapse --> router2["Router"]
        router2 --> kafka2["knarr.messages.inbound"]
        kafka2 --> scribe["Archiver / Chatbot / etc"]
    end
```

**Without the Matrix → Kafka direction**, bridged messages stay on the Matrix
plane only. Power users see them in Element, but they aren't available for
archiving, chatbot processing, or the subscription fan-out system. This is the
next key piece to build.

## Testing Without the Full Stack

Because Matrix and Kafka are loosely coupled, you can test different components
in isolation:

### Test chatbots without Discord or Matrix

Publish a message directly to Kafka and watch the bot's response:

```bash
# Simulate a chat message
echo '{"event_id":"test-1","timestamp":"2026-04-06T00:00:00Z","source":{"platform":"discord","channel":"general","community":"terasology"},"content":{"type":"text","body":"!help","url":null},"author":{"platform_id":"discord:12345","display_name":"tester"}}' | \
  kcat -b localhost:9092 -t knarr.messages.inbound -P

# Watch the bot's response
kcat -b localhost:9092 -t knarr.messages.outbound -C -o -1 -e
```

No Discord connection, no Matrix homeserver — just Kafka. This is the pattern
that replaces Autoboros's NATS-based test harness (stem component).

### Test watchers without Kafka

Adapters are pure Python with no Kafka dependency. Since the Phase 1
WatcherInstance refactor the entry point is `fetch(since_cursor)` on
`RedditApiAdapter` / `GitHubApiAdapter` — the old `parse_post()` and
`parse_notification()` functions are gone along with the watcher modules
that held them.

Tests patch `_http_get`, which exists on each adapter purely as that
isolation seam, and assert on the returned `(alerts, cursor)` pair:

```bash
python3 -m pytest tests/ -v
```

Cursor behaviour is worth testing explicitly rather than incidentally: it is
what decides whether an alert is emitted once, twice, or never. `WatcherInstance`
only advances its cursor after Kafka confirms delivery, so the failure tests
inject a producer whose `flush()` reports undelivered messages and assert the
next poll re-fetches from the same point.

### Test the router without external platforms

Publish a test alert to Kafka and verify it appears in the Matrix room:

```bash
echo '{"event_id":"manual-001","instance_id":"reddit-terasology","scope":"community/terasology","access_path":"api","platform":"reddit","raw_post_ref":"https://example.invalid/post","content":{"type":"post","title":"Manual test","body":"Testing the pipeline","author":"tester","attachments":[]},"timestamp":"2026-08-29T12:00:00+00:00","extracted_at":"2026-08-29T12:00:00+00:00"}' | \
  kcat -b localhost:9092 -t knarr.watch.alerts -P
```

Then check `#social-watch` in Element or the router logs. This is the **post-Phase 1 envelope**: the old nested `source: {platform, channel, community}` block was replaced by flat `instance_id` / `scope` / `access_path` / `platform` fields plus `raw_post_ref`. `from_kafka_dict` is deliberately strict and raises on a missing field rather than defaulting, so a message in the old shape is skipped by the consumer instead of arriving half-populated — if nothing appears, check the router logs for a decode error before suspecting Matrix.

The router logs the Matrix event id it received back (`Posted alert from reddit/reddit-terasology (event $...)`). A line without one means the send was rejected, not delivered.

### Inspect everything via Kafka UI

Open `http://kafka-ui.knarr.local` to browse topics, view messages with
formatted JSON, check consumer group lag, and even produce test messages
from the browser.

## Data Flow by Scenario

### "New Reddit post about Terasology"

```mermaid
sequenceDiagram
    participant R as Reddit API
    participant W as Watcher
    participant K as Kafka (watch.alerts)
    participant Bot as Router Bot
    participant S as Synapse
    participant E as Element

    W->>R: GET /r/Terasology/new.json
    R-->>W: posts JSON
    W->>K: publish WatchAlert
    K->>Bot: consume alert
    Bot->>S: post to #social-watch
    S->>E: notification
```

### "Parent texts JOIN PANTHERS" (future)

```mermaid
sequenceDiagram
    participant P as Parent Phone
    participant SP as Spare Phone
    participant B as mautrix-gmessages
    participant S as Synapse
    participant Bot as Router Bot
    participant KC as Keycloak
    participant V as Valkey

    P->>SP: SMS "JOIN PANTHERS"
    SP->>B: message received
    B->>S: Matrix event in SMS room
    S->>Bot: room event
    Bot->>KC: create/update user, add to Panthers group
    Bot->>V: update subscription cache
    Bot->>S: reply "You're subscribed"
    S->>B: Matrix reply
    B->>SP: SMS reply
    SP->>P: "You're subscribed to Panthers via SMS"
```

### "Coach posts announcement" (future, with subscriptions)

```mermaid
sequenceDiagram
    participant C as Coach (Element)
    participant S as Synapse
    participant Bot as Router Bot
    participant V as Valkey
    participant K as Kafka
    participant WA as WhatsApp Group
    participant SMS as SMS subscribers
    participant EM as Email subscribers

    C->>S: posts in #panthers/announcements
    S->>Bot: room event
    Bot->>K: publish to messages.inbound
    Bot->>V: lookup subscribers for room
    V-->>Bot: [{sms, +1555...}, {email, pat@...}]
    Bot->>S: forward to WhatsApp bridge room
    S->>WA: bridge delivers to WhatsApp group
    Bot->>SMS: send via gmessages bridge
    Bot->>EM: send via email service
```

## Kafka Topics

| Topic | Direction | Purpose |
|-------|-----------|---------|
| `knarr.watch.alerts` | Watchers → Router | Platform monitoring alerts |
| `knarr.messages.inbound` | Matrix → Consumers | All chat messages entering the event bus |
| `knarr.messages.outbound` | Producers → Matrix | Messages approved for publishing to platforms |
| `knarr.routing.pending` | Router ↔ Poweruser | Messages awaiting human approval |
| `knarr.routing.decisions` | Poweruser → Router | Approval/rejection events |
| `knarr.content.draft` | Autoboros ↔ Router | Content being shaped before publishing |
| `knarr.identity.changes` | Keycloak → Cache | User/group changes for Valkey invalidation |

Note: `knarr.identity.changes` is not yet created — it will be added when the
subscription model is implemented.

## Infrastructure Dependencies

```mermaid
flowchart BT
    subgraph "Tier 1 — Nordri"
        k3s["k3s / GKE"]
        traefik["Traefik"]
        storage["Storage"]
    end

    subgraph "Tier 2 — Mimir"
        pg["PostgreSQL"]
        kafka["Kafka (Strimzi)"]
        valkey["Valkey"]
    end

    subgraph "Tier 2 — Nidavellir"
        keycloak["Keycloak"]
    end

    subgraph "Tier 3 — Knarr"
        synapse["Synapse"]
        bridges["Bridges"]
        router["Router"]
        watchers["Watchers"]
        kafka_ui["Kafka UI"]
    end

    synapse --> pg
    synapse --> traefik
    router --> kafka
    router --> valkey
    router --> keycloak
    watchers --> kafka
    kafka_ui --> kafka
    bridges --> synapse
