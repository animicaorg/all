# Animica Ops Runbooks

This directory contains **actionable, copy-pasteable** procedures for running Animica in devnet and production. It ties together our Compose/K8s/Helm tooling, observability, logging, and incident playbooks that cover the node, P2P, mempool, DA, AICF, miner, randomness beacon, explorer API, studio services, and web UIs.

> TL;DR (on-call quick start)
>
> 1) Check alerts → open dashboards (Node, PoIES, Mempool, P2P, DA, AICF, Randomness, Explorer).  
> 2) Triage with health probes: `ops/scripts/wait_for.sh` or `kubectl get pods -n animica-devnet`.  
> 3) Identify scope (single pod vs cluster vs external).  
> 4) Apply the matching runbook below.  
> 5) After recovery, run smoke test: `ops/scripts/smoke_devnet.sh`.  
> 6) File an incident report and attach Loki queries & Prometheus graphs.

---

## 0) References & entrypoints

- **Compose (devnet one-shot):** `ops/docker/docker-compose.devnet.yml`  
  - Bring up: `docker compose -f ops/docker/docker-compose.devnet.yml --profile devnet up -d`  
  - Down: `docker compose -f ops/docker/docker-compose.devnet.yml --profile devnet down -v`

- **Kubernetes base:** `ops/k8s` (kustomize)  
  - Apply base + devnet overlay:  
    ```bash
    kubectl apply -k ops/k8s/overlays/devnet
    ```
  - Delete:  
    ```bash
    kubectl delete -k ops/k8s/overlays/devnet
    ```

- **Helm chart:** `ops/helm/animica-devnet`  
  - Install/upgrade:  
    ```bash
    helm upgrade --install animica-devnet ops/helm/animica-devnet \
      -f ops/helm/animica-devnet/values.yaml \
      -n animica-devnet --create-namespace
    ```
  - Rollback:  
    ```bash
    helm rollback animica-devnet <REV> -n animica-devnet
    ```

- **Smoke checks:** `ops/scripts/smoke_devnet.sh`  
  - Runs: RPC liveness, mines 1 dev block, explorer & metrics health.

- **Wait helper:** `ops/scripts/wait_for.sh http http://localhost:8545/healthz 120`

- **Configs in repo:**
  - Node/miner defaults: `ops/docker/config/*.toml`
  - Prometheus/alerts: `ops/metrics/*` (also mirrored in `ops/docker/config/*` and `ops/k8s/configmaps/*`)
  - Seeds/bootstraps: `ops/seeds/*`
  - TLS (dev only): `ops/tls/dev/make_selfsigned.sh`

---

## 1) Standard operating procedures (SOP)

### 1.1 Roll out a new node build (Kubernetes via Helm)

1. Bump image tag in `ops/helm/animica-devnet/values.yaml` (or pass `--set image.tag=...`).
2. Apply:
   ```bash
   helm upgrade --install animica-devnet ops/helm/animica-devnet -n animica-devnet
   kubectl rollout status statefulset/animica-node -n animica-devnet --timeout=5m

	3.	Post-deploy checks:
	•	/healthz and /readyz on RPC service.
	•	Prometheus: head advancing, peers >= floor, error rates stable.
	•	Loki: error spike check (see log queries below).
	4.	If regression: helm rollback animica-devnet <REV> -n animica-devnet.

1.2 Scale miner replicas
	•	K8s (HPA-ready):

kubectl scale deploy/animica-miner -n animica-devnet --replicas=4


	•	Compose (devnet):

docker compose -f ops/docker/docker-compose.devnet.yml --profile devnet up -d --scale miner=4



1.3 Hotfix config (no image change)
	•	Node TOML (K8s): edit ConfigMap and restart:

kubectl edit configmap node-config -n animica-devnet
kubectl rollout restart statefulset/animica-node -n animica-devnet


	•	Seeds update:
	•	Update ops/k8s/configmaps/seeds.yaml or Helm values, then re-apply.

1.4 Rotate DNS/bootstrap seeds
	1.	Generate report:

python3 ops/scripts/gen_bootstrap_list.py
python3 ops/scripts/rotate_seeds.py --in ops/seeds/bootstrap_nodes.json --out ops/seeds/bootstrap_nodes.json


	2.	Commit & redeploy ConfigMap/Helm values.

1.5 TLS for dev (self-signed)

( cd ops/tls/dev && ./make_selfsigned.sh --cn dev.animica.local --hosts localhost,127.0.0.1,::1,dev.animica.local )

Mount server.fullchain.pem / server.key into reverse proxy (see ops/docker/nginx/default.conf).

⸻

2) Incident response playbooks

2.1 Head not advancing (block production stall)

Symptoms
	•	Grafana: Node → head lag alert firing.
	•	Mempool has pending txs but no new heads.
	•	Miner hashrate drops or reports WorkExpired.

Checklist
	1.	Verify RPC & P2P health:

kubectl get pods -n animica-devnet -o wide
kubectl logs statefulset/animica-node -n animica-devnet --tail=200
kubectl logs deploy/animica-miner -n animica-devnet --tail=200


	2.	Is Θ (target) mis-tuned? Check PoIES dashboard for acceptance ~0% and fairness collapse.
	3.	Randomness beacon blocking finalize? Check Randomness dashboard (beacon stall/VDF verify errors).

Remediation
	•	Temporarily lower difficulty/Θ in chain params (devnet only) and restart node.
	•	Ensure at least one miner replica healthy; scale up miners (1.2).
	•	If beacon VDF verify failing repeatedly, disable VDF requirement in devnet config and restart, then root-cause (see 2.5).

Post-mortem
	•	Capture Prometheus graph for head height, accept rate, miner submissions.
	•	Loki queries (see Logging).

2.2 RPC saturated / high error rate

Symptoms
	•	Explorer UI or studio services time out.
	•	Alerts: RPC failure rate, p99 latency.

Diagnosis

kubectl top pod -n animica-devnet
kubectl logs svc/animica-node-rpc -n animica-devnet --tail=200

Check rate limit middleware counters & 429s.

Remediation
	•	Scale out RPC (if stateless frontends used) or raise rate limits in ops/docker/config/node.toml.
	•	Enable/verify CORS allowlist in rpc/config.py settings.
	•	Consider adding a caching layer for read-heavy endpoints.

2.3 P2P partition / low peers

Symptoms
	•	Peer count < threshold; header sync slow.

Diagnosis
	•	DNS seeds stale? Run seed check:

python3 ops/seeds/check_seeds.py


	•	Inspect node logs for QUIC/TCP handshake errors (Kyber handshake).

Remediation
	•	Rotate seeds (1.4).
	•	Temporarily open additional inbound ports / verify cloud firewall rules.
	•	Ban abusive peers (dev tool):

python -m p2p.cli.peer ban --peer <peer_id> --minutes 60



2.4 Mempool surge → evictions / fee spike

Symptoms
	•	Watermark raising; high eviction rate; users report stuck txs.

Actions
	•	Verify base/tip split & dynamic floor on Mempool dashboard.
	•	Communicate current min gas via status page.
	•	If misconfigured, adjust mempool limits in config map; if sustained, scale miners.

2.5 DA availability failures

Symptoms
	•	DAS p_fail alert firing, light-client verify failing.
	•	Retrieval API 5xx.

Diagnosis

kubectl logs deploy/animica-da -n animica-devnet --tail=200

Check NMT proof verify errors and RS decode metrics.

Remediation
	•	Increase erasure redundancy (k,n) profile (devnet only) in ops/k8s/configmaps/prometheus.yml / DA config map then restart DA pods.
	•	Warm caches: replay recent blobs via da/cli/get_blob.py in a loop.

2.6 AICF queue backlog / SLA breaches

Symptoms
	•	Queue depth rising, job timeout rate ↑, slashing spikes.

Diagnosis
	•	AICF dashboard: provider heartbeats, lease renewals, SLA evaluator outcomes.
	•	Loki: search for SlashEvent / LeaseLost.

Remediation
	•	Raise provider quotas / replicas, validate provider health endpoints.
	•	Temporarily relax SLA thresholds (devnet) via aicf/policy/example.yaml values.

2.7 Randomness beacon stall or VDF verify failures

Symptoms
	•	rand.getRound stuck, no beaconFinalized events; VDF verify timeouts.

Diagnosis
	•	Verify VDF params vs round duration (tests cover this). On live:

python -m randomness.cli.inspect_round
python -m randomness.cli.verify_vdf --round <id> --proof path/to.bin



Remediation
	•	Reduce target seconds / iterations for VDF in devnet config; restart beacon service.
	•	If QRNG mixer enabled, disable temporarily to isolate.

⸻

3) Logging & observability

3.1 Prometheus (alerts and queries)

Dashboards & alert rules live under ops/metrics/* and ops/docker/config/rules/*.

Useful queries (examples):

# Node head lag
max by (instance) (animica_head_height) - min_over_time(animica_head_height[5m])

# RPC error rate
sum(rate(animica_rpc_requests_total{status=~"5.."}[5m]))

# P2P peers
avg(animica_p2p_peer_count)

# DA sampling probability of failure
avg_over_time(animica_da_sampling_p_fail[15m])

# AICF job backlog
sum(animica_aicf_queue_depth)

3.2 Loki (log queries)

Labels are enriched in promtail configs under ops/docker/config/promtail/config.yml and pipelines under ops/logging/pipelines/*.

Common searches:
	•	Node errors:

{app="node"} |= "ERROR" | unwrap ts


	•	Beacon failures:

{app="randomness"} |~ "VDF|beacon finalize|commit|reveal" |= "ERROR"


	•	P2P handshake issues:

{app="p2p"} |~ "handshake|Kyber|AEAD" |= "fail"


	•	AICF slashing:

{app="aicf"} |~ "SlashEvent|SLA fail"



⸻

4) Release & rollback

4.1 Compose (devnet)

docker compose -f ops/docker/docker-compose.devnet.yml --profile devnet pull
docker compose -f ops/docker/docker-compose.devnet.yml --profile devnet up -d
# Rollback by pinning previous image tags in compose file and re-up.

4.2 Helm
	•	Dry-run:

helm upgrade --install animica-devnet ops/helm/animica-devnet -n animica-devnet --dry-run


	•	Rollback:

helm history animica-devnet -n animica-devnet
helm rollback animica-devnet <REV> -n animica-devnet



⸻

5) Disaster recovery (DR) & data
	•	Databases: Node is a statefulset with PVC (see ops/k8s/statefulsets/node.yaml). Ensure regular snapshots (cloud-provider).
	•	Restore:
	1.	Scale down node replicas: 0.
	2.	Restore PVC snapshot.
	3.	Scale up and monitor head catch-up; re-seed if needed.
	•	Artifacts (studio-services): If using S3 backend (studio_services/storage/s3.py), verify bucket versioning enabled.

⸻

6) Security incidents (abuse, replay, floods)
	•	P2P abuse: temporarily ban peers (see 2.3).
	•	RPC floods: tighten rate limits in rpc/middleware/rate_limit.py via config; enable WAF on ingress.
	•	Nullifier abuse / replay: verify consensus/nullifiers.py metrics; increase TTL window if mis-tuned (devnet only).
	•	Rotate API keys (services): run studio-services/studio_services/cli.py to revoke/regenerate.

⸻

7) SLOs & acceptance gates
	•	CI gates: tests/GATES.md (perf guardrails)
	•	Ops SLOs (suggested):
	•	Head lag < 2× target block interval p95 over 15m
	•	RPC p99 latency < 500ms, error rate < 1%
	•	DA p_fail < 1e-6 over 30m windows
	•	AICF queue median wait < 1 block; SLA breach rate < 0.5% daily
	•	Randomness beacon finalizes every round within configured window

⸻

8) Handy commands (grab bag)

# List pods and restarts
kubectl get pods -n animica-devnet -o wide
kubectl get pods -n animica-devnet --sort-by='.status.containerStatuses[0].restartCount'

# Tail logs
kubectl logs deploy/animica-miner -n animica-devnet -f --tail=200

# Exec into node
kubectl exec -it statefulset/animica-node -n animica-devnet -- /bin/sh

# Port-forward RPC locally
kubectl port-forward svc/animica-node-rpc 8545:8545 -n animica-devnet

# Prometheus port-forward
kubectl port-forward svc/prometheus 9090:9090 -n animica-observability

# Loki port-forward (if deployed)
kubectl port-forward svc/loki 3100:3100 -n animica-observability

# Re-run smoke after changes
bash ops/scripts/smoke_devnet.sh


⸻

9) Runbook hygiene
	•	Keep dashboards/alerts in sync with metric name changes (ops/metrics/*).
	•	Update seeds regularly (ops/scripts/gen_bootstrap_list.py + allowlist/blocklist).
	•	Record all incident timelines and link to queries & diffs.
	•	Prefer config flips on devnet; use rollbacks in prod.

⸻

Got an improvement? PRs welcome. Include: background, blast radius, MTTD/MTTR notes, and before/after graphs.

