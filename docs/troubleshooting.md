# Troubleshooting

Known failure modes and the workarounds that have stuck. New patterns
should land here as they're discovered.

## Synapse locale mismatch

**Symptom:** Synapse fails to start with a locale error pointing at the
Postgres database.

**Cause:** Percona creates databases with `en_US.utf-8` collation;
Synapse wants `C`.

**Workaround:** set `allow_unsafe_locale: true` under `database` in
`homeserver.yaml`.

## GitHub watcher 401 on every poll

**Symptom:** `knarr-watchers` logs a 401 from the GitHub API on every
poll cycle. Polling continues but no GitHub events ever land in Kafka.

**Cause:** missing `GITHUB_TOKEN` — the GitHub notifications API
requires a PAT with the `notifications:read` scope. The watcher is
otherwise unaffected and still produces Reddit alerts.

**Fix:** create a fine-grained PAT, add it to `knarr.env` as
`GITHUB_TOKEN`, restart the watcher Deployment.

## Kafka topics exist as objects but never become Ready

**Symptom:** `kubectl apply` reports success and `kubectl get kafkatopic -n kafka` lists the topics — but they never reach `Ready=True`, and nothing exists on the broker. Producing to one creates a *different*, auto-created topic, or fails, depending on broker config.

**The trap:** Strimzi **silently ignores** a `KafkaTopic` whose `strimzi.io/cluster` label names a cluster that does not exist. The Kubernetes object is accepted and stored — so it looks present — but no operator claims it, so it gets no status and no broker-side topic. There is no event and no error anywhere. **The object existing is exactly what makes this hard to spot; "it's in `kubectl get`" is not evidence it works.** Check the READY column, not the presence of a row.

**Historical cause (fixed 2026-08-28, mimir#18):** the `xkafkacluster-strimzi` composition used to name the cluster after the Crossplane *composite*, which carries a random suffix (`knarr-kafka-8r9tn`, then `knarr-kafka-2wjjq` after a rebuild). Every committed reference went stale on each teardown. Knarr's six topics were inert for two months for exactly this reason.

The composition now names Strimzi resources after the **claim**, so the cluster is deterministically `knarr-kafka` and the committed labels stay correct across rebuilds.

**If you still see a mismatch,** compare the label against the live cluster:

```bash
kubectl get kafka -n kafka
kubectl get kafkatopic -n kafka
```

The authoritative bootstrap address is published on the claim — read it rather than assembling one:

```bash
kubectl get kafkacluster knarr-kafka -n knarr -o jsonpath='{.status.bootstrapServers}'
```
