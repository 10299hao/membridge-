# Minikube deployment

From the repository root, after Docker and Minikube are running:

```powershell
./scripts/configure.ps1
docker build -t membridge:local .
minikube start
minikube image load membridge:local
kubectl apply -f k8s/00-namespace.yaml
kubectl -n membridge create secret generic membridge-secrets --from-env-file=.env
kubectl apply -f k8s/01-postgres.yaml -f k8s/02-services.yaml
kubectl -n membridge rollout status statefulset/postgres
kubectl -n membridge rollout status deployment/gateway
kubectl -n membridge port-forward service/gateway 8000:8000
```

In another terminal, set `MEMBRIDGE_SERVER=http://127.0.0.1:8000` and
`MEMBRIDGE_API_KEY` to the token in `.env`, then run the normal CLI commands.
The secret is for a single trusted user; this is not a multi-tenant service.
Use HTTPS and an authenticated ingress before exposing the gateway outside a local machine.

```powershell
kubectl -n membridge scale deployment/ai --replicas=3
kubectl -n membridge scale deployment/worker --replicas=3
kubectl -n membridge get pods,pvc
```

Postgres is intentionally one persistent instance. The application creates the initial
schema and pgvector extension on startup. Future schema changes require migrations;
this initial version does not claim zero-downtime upgrades.
