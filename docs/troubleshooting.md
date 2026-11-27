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

**Symptom:** `kubectl apply` reports success and `kubectl get kafkatopic -n kafka` lists the topics — but they never reach `Ready=True`, and no matching topic exists on the broker.

What happens when a producer then writes to that name depends on the broker's `auto.create.topics.enable`. With auto-creation **on**, the broker creates a topic of the *same name* using its own defaults — so the partition count, replication factor and retention from the `KafkaTopic` spec are all silently ignored, and you end up with a working-looking topic that is not the one you declared. With auto-creation **off**, the produce simply fails. The first case is the dangerous one, because nothing looks broken until the settings matter.

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

## New Kafka cluster crashloops on `Invalid cluster.id` after a rename

**Cause:** renaming the cluster makes Crossplane retarget its existing `Object`, so the old `Kafka` and `KafkaNodePool` survive unmanaged. Their PVC (`deleteClaim: false`) outlives them, and a later cluster whose pod claims the same PVC name adopts it and refuses the foreign cluster id.

**Fix:** delete the old `Kafka`, its `KafkaNodePool` and its PVC together. Expect this again on the first GKE rename.

## Router logged a post that never appeared

matrix-nio returns error objects instead of raising, so an unchecked send "succeeds". An invited-but-unjoined bot hit this before knarr#8, which made the router join at startup, check every send result, and log the Matrix event id. Check the result of any new nio call the same way.

## BDD run gets 403 on `/members` right after teardown

`DELETE /_synapse/admin/v2/rooms/{id}` returns at once and purges in the background, so the alias still resolves for a moment and the next run finds a room that is being deleted. The teardown step in `tests/features/steps/reconcile_steps.py` does not wait yet. The fix is to poll `/_synapse/admin/v2/rooms/delete_status/{delete_id}` after each DELETE. Until then, wait a few seconds and re-run.
