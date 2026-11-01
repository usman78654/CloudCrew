# CloudCrew DevOps demo

This guide demonstrates only features present in this repository. No cloud account is required.

## 1. Docker deployment

```powershell
python scripts/bootstrap.py
docker compose -p cloudcrew-demo --env-file .env.cloud up --build --wait
python scripts/cloud_demo.py
```

Explain that the same immutable image runs as the API, migration job, and workers. PostgreSQL supplies durable queue coordination. Kill a worker during `scripts/worker_failure_demo.py` to show lease recovery.

## 2. Prometheus and Grafana

```powershell
docker compose -p cloudcrew-demo --env-file .env.cloud -f compose.yaml -f compose.observability.yaml up --build --wait
```

Open:

- CloudCrew: http://localhost:8000
- Prometheus targets: http://localhost:9090/targets
- Grafana: http://localhost:3000 (`admin` / `cloudcrew-demo` for local demo only)

Grafana is provisioned with the **CloudCrew Operations** dashboard. Prometheus evaluates alerts for missing workers, stalled queues, and task failures. Production deployments must override the Grafana password and restrict `/metrics` at the network layer.

## 3. Kubernetes

The manifests are cloud-neutral and work with Docker Desktop Kubernetes, kind, minikube, or a managed conforming cluster.

```powershell
docker build -f Dockerfile.cloud -t cloudcrew:local .
./scripts/deploy_kubernetes.ps1
kubectl -n cloudcrew get pods
kubectl -n cloudcrew port-forward service/cloudcrew-api 8000:80
```

For kind, load the image before deployment: `kind load docker-image cloudcrew:local`. The deployment includes rolling updates, readiness/liveness probes, resource requests and limits, HPAs, a PodDisruptionBudget, persistent PostgreSQL storage, NetworkPolicies, and non-root/read-only application containers.

Scale and rollback:

```powershell
kubectl -n cloudcrew scale deployment/cloudcrew-worker --replicas=4
kubectl -n cloudcrew set image deployment/cloudcrew-api api=cloudcrew:bad-demo
./scripts/rollback_kubernetes.ps1 -Deployment cloudcrew-api
```

## 4. Terraform

Terraform is provider-neutral here: it deploys the same application resources to the Kubernetes context selected by `kubeconfig_path` and `kube_context`. It does not pretend to provision AWS, Azure, or GCP infrastructure.

```powershell
cd infrastructure/terraform
Copy-Item terraform.tfvars.example terraform.tfvars
# Replace the example secrets locally; terraform.tfvars is ignored by Git.
terraform init
terraform fmt -check
terraform validate
terraform plan
terraform apply
```

Use an encrypted remote backend before sharing production state because Terraform state can contain secrets.

## 5. CI, release, and security

`cloud-ci.yml` runs linting, tests, PostgreSQL integration coverage, Kubernetes rendering, Trivy configuration scanning, image building, a critical-vulnerability gate, and the end-to-end demo. Tagged releases build immutable multi-architecture images and publish version and commit-SHA tags to GitHub Container Registry.

The release workflow builds artifacts; deployment remains environment-specific. This separation is deliberate because the repository has no cloud account or cluster credentials.

## Interview summary

> CloudCrew separates a stateless FastAPI control plane from independently scalable workers. PostgreSQL provides a durable queue using row locking, leases, retries, fencing tokens, and idempotency. Docker Compose provides a reproducible local stack. Kubernetes adds health-driven orchestration, scaling, rolling updates, rollback, disruption controls, persistent storage, and network isolation. Prometheus scrapes operational metrics, Grafana visualizes them, and Trivy gates infrastructure and container risks in CI. Terraform can reproduce the Kubernetes deployment against any selected cluster without tying the application to one cloud provider.
