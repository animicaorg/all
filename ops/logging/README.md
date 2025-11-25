# Animica Logging Guide – labels, retention, and PII hygiene

This doc defines how we log across services (node, miner, p2p, rpc, da, aicf, randomness, explorer, studio-*).  
Goal: **actionable, low-cardinality, privacy-respecting** logs that are easy to query and cheap to store.

Stack (default): **Promtail → Loki → Grafana** (see `ops/docker/config/promtail/config.yml`, Loki/Grafana in `ops/docker/docker-compose.observability.yml` or k8s under `ops/k8s/observability/*`).

---

## 1) Label strategy (Loki-friendly)

Keep labels **stable & low cardinality**. Put everything else in the JSON log body.

### 1.1 Canonical labels (✅ use these)
- `service`: one of `node|miner|rpc|p2p|da|aicf|randomness|explorer|services|studio-web|explorer-web`
- `component`: coarse area (e.g. `chain`, `mempool`, `consensus`, `gossip`, `ws`, `http`, `db`)
- `env`: `dev|devnet|staging|prod`
- `network`: `animica-mainnet|animica-testnet|animica-devnet|local`
- `chainId`: numeric (e.g. `1`, `2`, `1337`)
- `version`: short git describe (e.g. `v0.3.2-abc123`)
- `region` (optional): cloud region/colo tag (`iad`, `fra`, `sfo`…)
- `job`: Prometheus scrape job or deployment logical name
- `instance`: hostname/pod-name (k8s Downward API)
- `container` / `pod`: k8s names (already added by promtail on k8s)
- `app`: Helm/Kustomize app label (if present)

> These are already set or derived by our Promtail config; add only if missing.

### 1.2 Event-specific labels (use sparingly)
- `topic`: for P2P/WS topics (e.g. `newHeads`, `shares`, `blocks`) — **bounded set only**
- `http_route`: normalized path (`/rpc`, `/ws`, `/da/blob/{id}`) — **templated, not raw**
- `level`: `debug|info|warn|error` (optional as label; always present in JSON body)

### 1.3 **Do NOT** label these (put in JSON body instead)
- Peer IDs, tx hashes, block hashes, commitments
- Error messages/stack traces
- Wallet addresses, IP addresses, user agents
- Dynamic IDs (request_id, trace_id)  
Those explode cardinality and hurt Loki’s index.

### 1.4 JSON log body fields (recommended)
Every service should emit structured JSON with at least:
```json
{
  "ts": "2025-01-01T12:34:56.789Z",
  "level": "info",
  "msg": "submitted tx",
  "service": "rpc",
  "component": "tx",
  "version": "v0.4.0-1a2b3c",
  "env": "devnet",
  "chainId": 1337,
  "request_id": "a0f1…",
  "trace_id": "b23c…",
  "tx_hash": "0xabc…",
  "latency_ms": 42
}

Keep keys flat, use primitive types/short arrays, cap string fields to ~1–2 KiB.

⸻

2) Retention, storage & cost controls

Tune Loki retention per environment & label selectors. Suggested defaults:

Environment	Logs retention	Index retention	Notes
local/dev	3–7 days	2 days	cheapest; disable debug by default
devnet (public)	7–14 days	7 days	drop debug; sample info if needed
staging	14–30 days	7–14 days	error/warn full; info partial
prod	30–90 days	14–30 days	consider cold archive (S3) at 30–90d

Tips:
	•	Prefer zstd chunk compression (default).
	•	Shorten retention for high-volume selectors (e.g. service=p2p, component=gossip).
	•	Use tenant or stream limits to prevent runaway cost.
	•	Optional archival: ship chunks to S3 with lifecycle rules (90–180d) for audits.

⸻

3) PII & secrets policy

We must never log:
	•	Private keys, mnemonics, seeds, PQ secret material
	•	API keys, JWTs, session tokens, cookies, Authorization headers
	•	Raw request bodies for /deploy, /verify, /faucet, /rpc (unless whitelisted & scrubbed)
	•	Emails, names, exact physical addresses
	•	Full IP addresses (treat as personal data); use hashed/truncated if operationally required

Allowed with care (pseudonymous on-chain data):
	•	Addresses and hashes (tx/block/commitment) — keep in JSON body (not labels)
	•	Peer IDs — hashed or truncated when possible

3.1 Redaction & hashing (Promtail pipeline)

Promtail supports regex stages to mask secrets. Example patterns to mask:
	•	Authorization:\s*(Bearer\s+)?([A-Za-z0-9\-\._~\+\/]+=*) → Authorization: ****
	•	mnemonic":\s*"(.*?)" → mnemonic":"[REDACTED]"
	•	("private_key"| "secret"| "api_key")\s*:\s*"(.*?)" → "$1":"[REDACTED]"

Hash IPs before shipping (salt via env var):
	•	Replace remote_ip with ip_hash=sha256(remote_ip || SALT)[:16]
	•	Truncate user_agent to ≤128 chars

If in doubt, drop the field. Functionality beats observability when privacy conflicts.

3.2 Logging levels & sampling
	•	Default: info in prod, debug off.
	•	Enable per-component debug temporarily behind dynamic config and auto-revert timers.
	•	Apply probabilistic sampling on noisy categories (e.g., gossip fanout at p=0.01).

⸻

4) Access control & governance
	•	Restrict Grafana access (org roles; folders per env).
	•	Loki read access via network policy & auth proxy (no direct public access).
	•	Keep an audit log of dashboard/report exports (Grafana server logs).
	•	Run periodic scans for secret-like patterns in stored logs.

Incident playbook (PII exposure suspected):
	1.	Quarantine offending streams (Loki delete API / retention short-circuit).
	2.	Rotate impacted credentials immediately.
	3.	File an incident with timeline & scope; notify stakeholders.
	4.	Add/adjust Promtail redaction rule and a regression test.

This guidance is operational, not legal advice—follow your local compliance rules (GDPR/CCPA/etc.).

⸻

5) Quick checklist
	•	Only canonical labels are used; no high-card dynamic labels
	•	Structured JSON logs with ts, level, msg, service, component, version, env
	•	Secrets/PII scrubbed at source and again in Promtail
	•	Retention and sampling configured per env
	•	Access to Grafana/Loki restricted; audit logs enabled
	•	Run ops/scripts/env_check.sh and ops/docker/healthchecks/http_health.sh after deploy

⸻

6) References in this repo
	•	Promtail config: ops/docker/config/promtail/config.yml
	•	Loki config: ops/docker/config/loki/config.yml
	•	Grafana provisioning: ops/metrics/grafana/provisioning/*
	•	Alerts & dashboards: ops/metrics/rules/*, ops/metrics/grafana/dashboards/*

