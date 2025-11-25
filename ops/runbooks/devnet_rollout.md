# Devnet Rollout Runbook
**Scope:** Version bump of Animica devnet components (node, miner, RPC, DA, AICF, randomness, explorer, studio-web, studio-services).  
**Goals:** Safe deploy with canary, measurable health, and one-command rollback.

---

## 0) Preconditions & Ownership
- **Change owner:** Release engineer on-call.
- **Change window:** Any time (devnet), prefer low-traffic.
- **Backout owner:** Same as change owner.

---

## 1) Versioning & Image Tags

### Semver & tags
- Tag source: `vX.Y.Z` (semver) on the mono-repo root.
- Image tag convention: `${SEMVER}-${SHORT_SHA}` (e.g., `v0.5.0-a1b2c3d`).
- Also push `:latest-dev` for convenience (not used by prod overlays).

### Bump version
```bash
# From repo root
./ops/scripts/env_check.sh
# Update versions in these files (search/replace):
# - core/version.py, rpc/version.py, etc. (all module version.py)
# - wallet-extension/package.json, studio-*/package.json, sdk/*/versions
# - ops/helm/animica-devnet/values.yaml (default image tags if pinned)
git commit -am "chore(release): bump to vX.Y.Z"
git tag -a vX.Y.Z -m "release vX.Y.Z"

Build & push multi-arch images

export SEMVER=vX.Y.Z
export SHORT_SHA=$(git rev-parse --short HEAD)
export IMAGE_TAG="${SEMVER}-${SHORT_SHA}"

# Builds all images and pushes with IMAGE_TAG and latest-dev
./ops/scripts/push_images.sh "${IMAGE_TAG}"

Artifacts pushed (by script):
	•	ghcr.io/<org>/animica-node:${IMAGE_TAG}
	•	ghcr.io/<org>/animica-miner:${IMAGE_TAG}
	•	ghcr.io/<org>/animica-explorer:${IMAGE_TAG}
	•	ghcr.io/<org>/animica-studio-services:${IMAGE_TAG}
	•	ghcr.io/<org>/animica-studio-web:${IMAGE_TAG}
	•	ghcr.io/<org>/animica-explorer-web:${IMAGE_TAG}

⸻

2) Pre-flight Checklist
	•	✅ CI green (see tests/GATES.md), baselines within guardrails.
	•	✅ No chain param changes that require re-genesis for devnet. If changed:
	•	Update ops/k8s/configmaps/chain-params.yaml and ops/k8s/configmaps/genesis.json.
	•	Expect node DB reset (PVC wipe) or devnet reset announcement.
	•	✅ DB migrations: for studio-services only; apply with:

kubectl -n animica-devnet exec deploy/animica-services -- /app/scripts/migrate.sh


	•	✅ OpenRPC surface compatible with explorer/wallet (if breaking, bump UIs together).
	•	✅ Seed list fresh:

python ops/scripts/gen_bootstrap_list.py --out ops/seeds/bootstrap_nodes.json



⸻

3) Canary Strategy (Choose 1)

A) Kubernetes — Shadow namespace canary (recommended)
	•	Create animica-canary namespace (copy of devnet values, different Service names).
	•	Point only internal consumers (temporary smoke tools) to canary RPC/WS.
	•	Observe for 10–15 minutes before flipping devnet.

# Create canary namespace
kubectl create ns animica-canary || true

# Helm install canary with the new image tag
helm upgrade --install animica-canary ./ops/helm/animica-devnet \
  -n animica-canary \
  --set global.imageTag="${IMAGE_TAG}" \
  -f ops/helm/animica-devnet/values.yaml

# Health gates
kubectl -n animica-canary rollout status statefulset/animica-node --timeout=5m
kubectl -n animica-canary rollout status deploy/animica-miner --timeout=5m

Validate:
	•	ops/scripts/smoke_devnet.sh (override URL to canary RPC).
	•	Grafana (point datasources or add targets for canary job labels).
	•	Loki queries {namespace="animica-canary"} for errors.

B) Kubernetes — In-place canary with surge
	•	Increase node replicas temporarily to 2 (if stateless mode) — not default since node is stateful.
	•	For stateless services (explorer, services, web), set deployment strategy RollingUpdate with maxUnavailable=0, maxSurge=1. They’re already safe to roll.

C) Docker Compose devnet (local/public demo)

# Ensure new tags in compose env or override at runtime
export IMAGE_TAG="${IMAGE_TAG}"
docker compose -f ops/docker/docker-compose.devnet.yml pull
docker compose -f ops/docker/docker-compose.devnet.yml up -d


⸻

4) Rollout — Kubernetes (devnet)

Helm upgrade

export IMAGE_TAG="${IMAGE_TAG}"

helm upgrade --install animica-devnet ./ops/helm/animica-devnet \
  -n animica-devnet \
  -f ops/helm/animica-devnet/values.yaml \
  --set global.imageTag="${IMAGE_TAG}"

# Watch rollouts
kubectl -n animica-devnet rollout status statefulset/animica-node --timeout=10m
kubectl -n animica-devnet rollout status deploy/animica-miner --timeout=5m
kubectl -n animica-devnet rollout status deploy/animica-explorer --timeout=5m
kubectl -n animica-devnet rollout status deploy/animica-services --timeout=5m
kubectl -n animica-devnet rollout status deploy/animica-studio-web --timeout=5m
kubectl -n animica-devnet rollout status deploy/animica-explorer-web --timeout=5m

Kustomize alternative

# Patch image tags via kustomize edits (example)
kustomize build ops/k8s | kubectl apply -f -


⸻

5) Post-Deploy Gates (10–20 minutes)

Run:

bash ops/scripts/smoke_devnet.sh

Dashboards:
	•	Node: head advances (no stalls), RPC p95 latency stable.
	•	PoIES: non-zero Γ and fair mix, acceptance ~ target Θ.
	•	Mempool: ready queue > 0 after submits; rejection rate normal.
	•	P2P: peers >= minimum, drops/backpressure low.
	•	DA: DAS p_fail below threshold; proof errors baseline.
	•	Randomness: beacon finalizes within window; VDF verifies.
	•	AICF: jobs assigned/completed; SLA breaches within SLO.
	•	Explorer: API 5xx < SLO; WS disconnects baseline.

Logs (Loki quick scan):

{namespace="animica-devnet"} |= "ERROR" | json
{app="node"} |~ "panic|traceback|exception"

If any critical alerts fire (see ops/runbooks/alerts_reference.md), pause & evaluate.

⸻

6) Rollback

Fast rollback — Helm

# Show history
helm -n animica-devnet history animica-devnet
# Roll back to previous revision
helm -n animica-devnet rollback animica-devnet <REVISION>

Pin old tag and re-upgrade

export PREV_TAG="vX.Y.(Z-1)-<sha>"
helm upgrade --install animica-devnet ./ops/helm/animica-devnet \
  -n animica-devnet \
  -f ops/helm/animica-devnet/values.yaml \
  --set global.imageTag="${PREV_TAG}"

Compose rollback

# If you kept previous images locally
docker compose -f ops/docker/docker-compose.devnet.yml down
IMAGE_TAG="${PREV_TAG}" docker compose -f ops/docker/docker-compose.devnet.yml up -d

DB compatibility: If a downgrade breaks DB schema (rare; mainly studio-services), restore from snapshot/backup or re-run migrations to the prior version. For node state, devnet permits wipe if necessary:

# Danger: wipes chain DB PVC (devnet-only reset)
kubectl -n animica-devnet delete pvc -l app=animica-node
kubectl -n animica-devnet rollout restart statefulset/animica-node


⸻

7) Cutover From Canary (if using shadow namespace)
	•	Promote tag in main devnet:

helm upgrade --install animica-devnet ./ops/helm/animica-devnet \
  -n animica-devnet \
  -f ops/helm/animica-devnet/values.yaml \
  --set global.imageTag="${IMAGE_TAG}"


	•	Keep canary running for 30–60 minutes as a fallback or delete:

helm -n animica-canary uninstall animica-canary
kubectl delete ns animica-canary



⸻

8) Verification Checklist (tick all)
	•	smoke_devnet.sh passed.
	•	Head advancing with expected interval for 10m.
	•	Γ non-zero; fairness within envelope; acceptance near target.
	•	Mempool admission/eviction healthy.
	•	DA post/get/proof OK; p_fail below threshold.
	•	Beacon rounds finalize; VDF verify times within bounds.
	•	AICF jobs assigned/completed; no slash spikes.
	•	Explorer/API 5xx low; WS healthy.
	•	No critical alerts for 20m.

⸻

9) Comms & Audit
	•	Update release notes (CHANGELOG) with key changes.
	•	Post summary in #devnet with:
	•	Tag, short SHA, rollout window, any knobs changed (Θ, Γ, fees).
	•	Links to dashboards, and helm history snapshot.
	•	Create follow-up tasks for any TODOs (e.g., raise AICF quota, tune mempool floor).

⸻

10) Appendix

Useful one-liners

# Current images running
kubectl -n animica-devnet get deploy,statefulset -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[*].image

# Quick logs tail
kubectl -n animica-devnet logs statefulset/animica-node --tail=200
kubectl -n animica-devnet logs deploy/animica-miner --tail=200

# Check WS readiness
curl -sSf http://<explorer-host>/healthz

References
	•	Alerts: ops/runbooks/alerts_reference.md
	•	Compose: ops/docker/docker-compose.devnet.yml
	•	Helm chart: ops/helm/animica-devnet/
	•	K8s base: ops/k8s/
	•	Metrics: ops/metrics/
	•	Seeds: ops/seeds/

⸻

