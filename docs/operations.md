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

## Watcher / router environment variables

| Env Var | Default | Used by | Description |
|---------|---------|---------|-------------|
| `POLL_INTERVAL_SECONDS` | 21600 (6h) | Watchers | How often to poll Reddit/GitHub |
| `REDDIT_SUBREDDIT` | Terasology | Watchers | Subreddit to watch |
| `GITHUB_REPOS` | MovingBlocks/Terasology | Watchers | Comma-separated repo list |
| `GITHUB_TOKEN` | *(none)* | Watchers | PAT for GitHub notifications API |
| `KAFKA_BOOTSTRAP` | *(required)* | Both | Kafka bootstrap servers |
| `MATRIX_HOMESERVER` | *(required)* | Router | Synapse URL |
| `MATRIX_ROOM_ID` | *(required)* | Router | Target room for alerts |
