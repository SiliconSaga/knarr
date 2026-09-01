# Deploying Knarr from Scratch

End-to-end setup against a fresh k3s/k3d cluster. For day-2 ops once
everything is running, see [operations.md](operations.md).

## Prerequisites

- `k3d-nordri-test` cluster running (or equivalent k3s/k8s)
- Crossplane compositions: `xpostgresql-percona`, `xkafkacluster-strimzi`
- Strimzi operator in `kafka` namespace, Percona PG operator in `percona` namespace
- Mimir's **DataService operator** and its **shared PostgreSQL cluster** running in the `mimir` namespace (ArgoCD apps `mimir-dataservice-operator` and `mimir-shared-clusters`). Knarr no longer provisions its own Postgres cluster — it requests one database out of the shared one.
- `/etc/hosts` entries: `192.168.97.2 matrix.knarr.local kafka-ui.knarr.local`
  (adjust IP for your Traefik LB)

## Steps

```bash
# 1. Namespace
kubectl apply -f k8s/namespace.yaml

# 2. PostgreSQL — one database vended out of Mimir's shared cluster
#    (seconds, not minutes: no cluster is provisioned)
kubectl apply -f k8s/dataservice.yaml
kubectl get dataservice synapse -n knarr -w   # wait for PHASE=Ready
# The operator publishes credentials to the Secret named in status.secretName
# (synapse-dataservice). Nothing needs copying out of it — step 5's
# initContainer reads it directly.

# 3. Synapse signing key (one-time)
python3 -c "
import base64, os
key = os.urandom(32)
kid = 'a_' + base64.b64encode(os.urandom(3)).decode().rstrip('=')
print(f'ed25519 {kid} {base64.b64encode(key).decode()}')
" > /tmp/signing.key
kubectl create secret generic synapse-signing-key -n knarr --from-file=signing.key=/tmp/signing.key
rm /tmp/signing.key

# 4. (nothing to do — credentials are no longer copied by hand)
#    The ConfigMap ships homeserver.yaml as a TEMPLATE and the Deployment's
#    initContainer renders it from the synapse-dataservice Secret at boot.
#    This step used to mean pasting a live password into a committed file.

# 5. Synapse
kubectl apply -f k8s/synapse/
# Wait for health: curl http://matrix.knarr.local/health → "OK"

# 6. Create admin user
kubectl exec -n knarr deploy/synapse -- register_new_matrix_user \
  -c /config/homeserver.yaml -u admin -p <password> -a http://localhost:8008

# 7. Kafka (takes ~3 min)
kubectl apply -f k8s/kafka-claim.yaml
kubectl get kafkacluster knarr-kafka -n knarr -w   # wait for READY=True
# No label edit needed since mimir#18 — the Strimzi cluster is named after the
# claim, so kafka-topics.yaml's strimzi.io/cluster: knarr-kafka is already right.
kubectl apply -f k8s/kafka-topics.yaml
kubectl get kafkatopic -n kafka   # all six should be READY=True
# ^ verify this. A KafkaTopic labelled with a cluster that does not exist is
#   silently ignored by Strimzi — success here is topics existing, not a clean
#   apply. See troubleshooting.md.

# 8. Build and load images
docker build -f src/watchers/Dockerfile -t knarr-watchers:dev .
docker build -f src/router/Dockerfile -t knarr-router:dev .
k3d image import knarr-watchers:dev knarr-router:dev -c nordri-test

# 9. Create router bot user
kubectl exec -n knarr deploy/synapse -- register_new_matrix_user \
  -c /config/homeserver.yaml -u knarr-router -p <password> --no-admin http://localhost:8008

# 10. Update k8s/router/deployment.yaml with room ID and credentials, then:
kubectl apply -f k8s/watchers/
kubectl apply -f k8s/router/

# 11. Kafka UI (optional but recommended)
kubectl apply -f k8s/kafka-ui/
# Open http://kafka-ui.knarr.local (needs /etc/hosts entry)
```
