# Knarr Operations

Day-2 procedures: status checks, browsing Kafka, accessing Matrix, and
rolling new images. For first-time setup see [deploy.md](deploy.md); for
known failure modes see [troubleshooting.md](troubleshooting.md).

## Checking Status

```bash
# All Knarr pods
kubectl get pods -n knarr

# Watcher logs (Reddit polling, GitHub errors if no token)
kubectl logs -n knarr -l app=knarr-watchers --tail=20

# Router logs (Kafka consumption, Matrix posting)
kubectl logs -n knarr -l app=knarr-router --tail=20

# Synapse logs
kubectl logs -n knarr -l app=synapse --tail=20
```

## Kafka UI (web dashboard)

The easiest way to inspect Kafka. Requires `/etc/hosts` entry:
`192.168.97.2 kafka-ui.knarr.local` (same IP as Matrix).

```bash
# Deploy (one-time)
kubectl apply -f k8s/kafka-ui/

# Then open in browser
open http://kafka-ui.knarr.local
```

From the UI you can:
- **Topics** → click `knarr.watch.alerts` → **Messages** tab to browse all events with formatted JSON
- **Topics** → any topic → **Produce Message** to send a test event
- **Consumers** → `knarr-router` group to check offset lag (is the router keeping up?)
- **Dashboard** for cluster overview

## Kafka CLI (kcat)

For scripting or quick terminal checks, `kcat` talks directly to Kafka via port-forward:

```bash
brew install kcat

# The cluster is named after the claim, so this is stable — no lookup needed.
# (Before mimir#18 it carried a random Crossplane suffix; see troubleshooting.md.)
kubectl port-forward -n kafka svc/knarr-kafka-kafka-bootstrap 9092:9092 &

# List topics
kcat -b localhost:9092 -L

# Read all messages from watch.alerts, formatted
kcat -b localhost:9092 -t knarr.watch.alerts -C -e -J | python3 -m json.tool

# Read just the last 3 messages
kcat -b localhost:9092 -t knarr.watch.alerts -C -o -3 -e

# Publish a test alert
echo '{"event_id":"test","timestamp":"2026-04-05T00:00:00Z","source":{"platform":"test","channel":"manual","community":"terasology"},"content":{"type":"test","body":"Manual test alert","url":null}}' | \
  kcat -b localhost:9092 -t knarr.watch.alerts -P

# Kill the port-forward when done
kill %1
```

## Kafka via kubectl (fallback)

```bash
# List all Knarr topics
kubectl get kafkatopics -n kafka | grep knarr

# Check router consumer group lag (look up the pod name first — same
# Crossplane suffix pattern as the service above)
KAFKA_POD=$(kubectl get pods -n kafka -l strimzi.io/kind=Kafka -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n kafka "$KAFKA_POD" -- \
  bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
  --describe --group knarr-router
```

## Matrix client access

**Element Desktop (Mac):**

```bash
brew install --cask element
```

Sign in → custom homeserver `http://matrix.knarr.local` → `admin` / your password.

**Element Mobile (phone):**
Requires Tailscale or other network path to reach the k3d cluster.
Use the Tailscale IP of the Mac instead of `matrix.knarr.local`.
See [tailscale-setup.md](tailscale-setup.md) for the full setup.

## Rebuilding after code changes

```bash
docker build -f src/watchers/Dockerfile -t knarr-watchers:dev .
docker build -f src/router/Dockerfile -t knarr-router:dev .
k3d image import knarr-watchers:dev knarr-router:dev -c nordri-test
kubectl rollout restart deploy/knarr-watchers deploy/knarr-router -n knarr
```

Changing a source, its cadence or its credentials is a **config** change rather than a code change, so it needs the ConfigMap re-applied instead of an image rebuild:

```bash
kubectl apply -f k8s/watchers/reddit-github.yaml
kubectl rollout restart deploy/knarr-watchers -n knarr   # pick up the new mount
kubectl logs -n knarr -l app=knarr-watchers --tail=5     # confirm the instance list
```

The startup line names every instance it built (`watcher started — 2 instance(s): reddit-terasology, github-terasology`). If an instance you just added is missing from it, the pod is running a stale ConfigMap.

## Watcher / router environment variables

| Env Var | Default | Used by | Description |
|---------|---------|---------|-------------|
| `KNARR_CONFIG_PATH` | `/etc/knarr/knarr.yaml` | Watchers | Config carrying the `instances:` list |
| `GITHUB_TOKEN` | *(none)* | Watchers | Named by an instance's `credentials_ref.secret_key`. **Required if referenced** — the runner fails fast rather than degrading to anonymous calls, so a broken `secretKeyRef` surfaces immediately instead of looking like a quiet source |
| `KAFKA_BOOTSTRAP` | *(required)* | Both | Kafka bootstrap servers |
| `MATRIX_HOMESERVER` | *(required)* | Router | Synapse URL |
| `MATRIX_ROOM_ID` | *(required)* | Router | Target room for alerts. The router **joins** it at startup and refuses to start if it cannot — the reconciler only invites, and an invited-but-unjoined bot silently delivers nothing |

**What a source is, and how often it is polled, are no longer env vars.** Since the Phase 1 WatcherInstance refactor they live in the `instances:` list in `knarr.yaml`, mounted into the watcher pod as the `knarr-watcher-config` ConfigMap at `/etc/knarr`. Adding a source is a config edit rather than a new variable plus a code change, so `REDDIT_SUBREDDIT`, `GITHUB_REPOS` and `POLL_INTERVAL_SECONDS` are gone — each instance carries its own `platform_config` and `polling.interval_seconds`.

## ⚠ Reddit's anonymous endpoint is closed (verified 2026-08-29)

`https://www.reddit.com/r/<sub>/new.json` now returns **403 Blocked** for unauthenticated requests. This is not an IP or User-Agent problem: it reproduces from a residential connection, and with a spec-compliant `platform:app-id:version (by /u/user)` agent string. The `reddit-terasology` instance therefore polls and fails every cycle.

This is a platform change, not a regression in this code, and the WatcherInstance model contains it correctly — the failure is isolated to that one instance, logged, and the process keeps polling everything else.

Unblocking it means moving Reddit off `auth: anonymous` onto OAuth: register a Reddit app, obtain client credentials, and give the instance a `credentials_ref` the way the GitHub one already has. That is a real piece of Phase 2+ work, and it is exactly the platform-access decay the source-identity design was written to absorb.
