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

## Kafka cluster name mismatch

**Symptom:** `kafka-topics.yaml` apply fails with a "no such cluster"
error from Strimzi, or topics never reach `Ready=True`.

**Cause:** the Crossplane `xkafkacluster-strimzi` composition generates
a random suffix on the cluster name (e.g. `knarr-kafka-8r9tn`). The
`strimzi.io/cluster` label in `kafka-topics.yaml` has to match the
actual cluster name.

**Fix:** look up the actual name and update the label:

```bash
kubectl get kafka -n kafka   # note the NAME
# then edit k8s/kafka-topics.yaml's strimzi.io/cluster label to match
kubectl apply -f k8s/kafka-topics.yaml
```
