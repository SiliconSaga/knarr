# Deploying Knarr from Scratch

End-to-end setup against a fresh k3s/k3d cluster. For day-2 ops once
everything is running, see [operations.md](operations.md).

## Prerequisites

- `k3d-nordri-test` cluster running (or equivalent k3s/k8s)
- Crossplane compositions: `xpostgresql-percona`, `xkafkacluster-strimzi`
- Strimzi operator in `kafka` namespace, Percona PG operator in `percona` namespace
- `/etc/hosts` entries: `192.168.97.2 matrix.knarr.local kafka-ui.knarr.local`
  (adjust IP for your Traefik LB)

## Steps

```bash
# 1. Namespace
kubectl apply -f k8s/namespace.yaml

# 2. PostgreSQL (takes ~3 min)
kubectl apply -f k8s/postgres-claim.yaml
kubectl get postgresqlinstance synapse-db -n knarr -w   # wait for READY=True

# 3. Synapse signing key (one-time)
python3 -c "
import base64, os
key = os.urandom(32)
kid = 'a_' + base64.b64encode(os.urandom(3)).decode().rstrip('=')
print(f'ed25519 {kid} {base64.b64encode(key).decode()}')
" > /tmp/signing.key
kubectl create secret generic synapse-signing-key -n knarr --from-file=signing.key=/tmp/signing.key
rm /tmp/signing.key

# 4. Update k8s/synapse/configmap.yaml with DB credentials from:
kubectl get secret synapse-db-user-secret -n knarr -o jsonpath='{.data.host}' | base64 -d
kubectl get secret synapse-db-user-secret -n knarr -o jsonpath='{.data.password}' | base64 -d

# 5. Synapse
kubectl apply -f k8s/synapse/
# Wait for health: curl http://matrix.knarr.local/health → "OK"

# 6. Create admin user
kubectl exec -n knarr deploy/synapse -- register_new_matrix_user \
  -c /config/homeserver.yaml -u admin -p <password> -a http://localhost:8008

# 7. Kafka (takes ~3 min)
kubectl apply -f k8s/kafka-claim.yaml
kubectl get kafkacluster knarr-kafka -n knarr -w   # wait for READY=True
# Update strimzi.io/cluster label in kafka-topics.yaml to match actual cluster name:
kubectl get kafka -n kafka   # note the NAME
kubectl apply -f k8s/kafka-topics.yaml

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
