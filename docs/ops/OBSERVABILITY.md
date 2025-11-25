# Observability Guide — Dashboards & Alerts

This guide shows how to **measure, visualize, and alert** on the Animica stack (node, RPC, P2P, DA, consensus, AICF, randomness, miner). It standardizes **metrics, logs, traces, and exemplars** so on-call teams have **clear SLOs**, **actionable alerts**, and **linked runbooks**.

> TL;DR  
> - Ship **Prometheus** metrics from every component.  
> - Use **Grafana** dashboards and recording rules below.  
> - Route **alerts** via Alertmanager → PagerDuty/Slack with severity.  
> - **Trace** RPC requests end-to-end with OpenTelemetry and **exemplars**.  
> - Keep **cardinality** & **PII** under control. Tie every alert to a **runbook**.

---

## 1) Signals & taxonomy

### 1.1 Metrics (Prometheus)
All exporters must include common labels:
- `chain_id`, `network` (e.g., `1`, `animica.testnet`)
- `component` (`core`,`rpc`,`p2p`,`mempool`,`consensus`,`da`,`randomness`,`aicf`,`mining`)
- `instance`, `job`, `version`, `git_commit`

Metric naming:
- Counters: `*_total`
- Histograms/Summaries: `_bucket/_sum/_count`
- Gauges: naked noun (e.g., `mempool_txs`)

### 1.2 Logs (JSON)
- Structured JSON (`timestamp`, `level`, `component`, `trace_id`, `span_id`, `msg`, `error`, `fields{}`).
- **No secrets/keys/addresses** beyond prefixes (last 6 hex by default).
- Redact: payloads >1KiB, PQ keys, mnemonics, session tokens.

### 1.3 Traces (OTel)
- Propagate W3C `traceparent` from **RPC** → **execution** → **DA**/**AICF** adapters.
- Export OTLP to collector; sample 5–10% baseline; 100% for errors/slow (>p99).

### 1.4 Exemplars
- Attach trace IDs to latency histograms (Grafana “Exemplars” → click into trace).

---

## 2) Golden signals per component

| Component   | Latency / Throughput                                   | Errors & Saturation                                  | Health/Freshness                                |
|-------------|---------------------------------------------------------|------------------------------------------------------|-------------------------------------------------|
| **RPC**     | `rpc_request_latency_seconds` (p50/p90/p99)             | `rpc_requests_total{status="error"}/total`           | `/readyz` 200, `rpc_ws_clients`                 |
| **P2P**     | `p2p_rtt_ms` histogram                                  | `p2p_handshake_fail_total`, `p2p_gossip_dropped_total` | `p2p_head_lag_blocks`, `p2p_peers`              |
| **Consensus** | `consensus_block_interval_seconds`                   | `consensus_reorg_depth_total`                        | `consensus_theta_value`, `consensus_lambda_obs` |
| **Mempool** | `mempool_admit_latency_seconds`                         | `mempool_reject_total{reason}`, `mempool_evictions_total` | `mempool_txs`, `mempool_min_fee_gwei`        |
| **Execution** | `exec_apply_tx_seconds`, `exec_gas_used_total`       | `exec_fail_total{reason}`                            | `exec_state_snapshots_open`                     |
| **DA**      | `da_post_latency_seconds`, `da_proof_verify_seconds`    | `da_proof_fail_total`, `da_retrieval_fail_total`     | `da_sampler_success_ratio`                      |
| **Randomness** | `rand_vdf_verify_seconds`, `rand_round_duration_seconds` | `rand_proof_invalid_total`, `rand_commit_reject_total` | `rand_round_status`                             |
| **AICF**    | `aicf_queue_depth`, `aicf_assign_latency_seconds`       | `aicf_sla_fail_total{dim}`                           | `aicf_provider_healthy`                         |
| **Mining**  | `miner_submit_latency_seconds`, `miner_hashrate_shares_per_s` | `miner_share_reject_total{reason}`                | `miner_template_age_seconds`                    |

---

## 3) SLOs (targets)

- **RPC Availability:** 99.9% (5xx ratio < 0.1% 30d)  
- **RPC Latency:** p99 < 800ms (read); p99 < 1500ms (tx submit)  
- **Head Freshness:** lag ≤ 2 blocks for 99.5% of 30d minutes  
- **Mempool Admission Errors:** < 1% excluding DoS throttling  
- **DA Proof Verify Fail:** < 0.01% over 7d  
- **Randomness Beacon:** finalize round ≤ target + 2 min for 99%  
- **AICF SLA Pass Rate:** ≥ 99% (rolling 24h)  
- **P2P Handshake Success:** ≥ 99% (rolling 1h)

---

## 4) Prometheus — scrape & recording rules

### 4.1 Scrape example (snippet)
```yaml
scrape_configs:
- job_name: 'animica-rpc'
  scheme: http
  static_configs: [{ targets: ['rpc-1:9100','rpc-2:9100'] }]
  relabel_configs:
  - source_labels: [__address__]
    target_label: component
    replacement: rpc
- job_name: 'animica-p2p'
  static_configs: [{ targets: ['p2p-1:9100','p2p-2:9100'] }]

4.2 Recording rules (latency & error rate)

groups:
- name: animica-recording
  rules:
  - record: job:http_request_errors:rate5m
    expr: sum(rate(rpc_requests_total{status="error"}[5m])) by (job)
  - record: job:http_request_total:rate5m
    expr: sum(rate(rpc_requests_total[5m])) by (job)
  - record: job:http_error_ratio:5m
    expr: job:http_request_errors:rate5m / ignoring() job:http_request_total:rate5m
  - record: rpc:latency_p99:5m
    expr: histogram_quantile(0.99, sum by (le) (rate(rpc_request_latency_seconds_bucket[5m])))
  - record: p2p:head_lag:max5m
    expr: max_over_time(p2p_head_lag_blocks[5m])
  - record: consensus:block_interval:p95:1h
    expr: quantile_over_time(0.95, consensus_block_interval_seconds[1h])


⸻

5) Alerting — Alertmanager rules

Tie every alert to a runbook URL (see §9).

groups:
- name: animica-alerts
  rules:
  - alert: RPCErrorBudgetBurn
    expr: job:http_error_ratio:5m > 0.02
    for: 10m
    labels: { severity: page, team: rpc }
    annotations:
      summary: "RPC error ratio >2% for 10m"
      runbook: "https://docs.animica.org/ops/runbooks/rpc-errors"
      tips: "Check upstream node health, DB, rate limits"

  - alert: RPCLatencyHighP99
    expr: rpc:latency_p99:5m > 1.5
    for: 15m
    labels: { severity: page, team: rpc }
    annotations:
      summary: "RPC p99 latency >1.5s"
      runbook: "https://docs.animica.org/ops/runbooks/rpc-latency"

  - alert: HeadStalled
    expr: p2p:head_lag:max5m > 5
    for: 10m
    labels: { severity: critical, team: consensus }
    annotations:
      summary: "Head lag >5 blocks"
      runbook: "https://docs.animica.org/ops/runbooks/head-stall"

  - alert: ReorgDepthSpike
    expr: increase(consensus_reorg_depth_total[15m]) > 3
    for: 5m
    labels: { severity: warning, team: consensus }
    annotations:
      summary: "Reorg activity elevated"
      runbook: "https://docs.animica.org/ops/runbooks/reorg"

  - alert: MempoolBackpressure
    expr: mempool_txs > 100000 and mempool_min_fee_gwei > 50
    for: 30m
    labels: { severity: warning, team: mempool }
    annotations:
      summary: "Mempool large & fee floor elevated"
      runbook: "https://docs.animica.org/ops/runbooks/mempool-pressure"

  - alert: DAProofFailures
    expr: rate(da_proof_fail_total[15m]) > 0.001
    for: 15m
    labels: { severity: warning, team: da }
    annotations:
      summary: "DA proof failures above 0.1%/15m"
      runbook: "https://docs.animica.org/ops/runbooks/da-proof-fail"

  - alert: BeaconOverdue
    expr: rand_round_status == 2 and (time() - rand_round_open_timestamp_seconds) > 600
    for: 5m
    labels: { severity: page, team: randomness }
    annotations:
      summary: "Randomness round overdue"
      runbook: "https://docs.animica.org/ops/runbooks/beacon-overdue"

  - alert: AICFSLADegradation
    expr: rate(aicf_sla_fail_total[30m]) > 0.01
    for: 30m
    labels: { severity: warning, team: aicf }
    annotations:
      summary: "AICF SLA failures >1%/30m"
      runbook: "https://docs.animica.org/ops/runbooks/aicf-sla"

Routing (Alertmanager)
	•	severity=page → PagerDuty on-call
	•	severity=critical → PagerDuty & Slack #prod-incidents
	•	severity=warning → Slack #ops-warning, Jira ticket if sustained

⸻

6) Grafana — dashboards

6.1 Layout
	•	Overview (NOC): SLO tiles, head height, TPS, error ratio, top alerts.
	•	RPC: latency histograms with exemplars, error heatmap, throughput by method.
	•	Consensus: block interval, Θ (theta), λ_obs vs λ_target, reorg depth.
	•	P2P: peers, handshake success, RTT, gossip drops.
	•	Mempool: size, min fee, admit/reject reasons, eviction rates.
	•	DA: post/verify latency, fail ratios, sampler success.
	•	Randomness: round timeline, VDF verify, overdue alarms.
	•	AICF: queue depth, assign latency, SLA pass/fail by provider.
	•	Mining: template age, submit latency, accepts vs rejects.

6.2 Panel JSON (tiny example)

{
  "type": "timeseries",
  "title": "RPC p99 latency (s)",
  "targets": [{
    "expr": "rpc:latency_p99:5m",
    "legendFormat": "{{instance}}"
  }],
  "options": { "legend": { "displayMode": "table" }, "exemplars": { "color": "auto" } },
  "fieldConfig": { "defaults": { "unit": "s", "thresholds": { "mode": "absolute", "steps": [
    { "color": "green", "value": null }, { "color": "yellow", "value": 1.0 }, { "color": "red", "value": 1.5 }
  ]}}}
}


⸻

7) Blackbox & synthetic checks
	•	HTTP probes for /healthz, /readyz, /metrics using blackbox_exporter.
	•	WS synthetic: connect to /ws, subscribe to newHeads, expect event ≤ 5s.
	•	P2P probe: HELLO handshake & head freshness sample (internal CLI or eBPF tap).

Example blackbox module:

modules:
  http_2xx:
    prober: http
    http:
      method: GET
      valid_http_versions: ["HTTP/1.1", "HTTP/2"]
      preferred_ip_protocol: "ip4"


⸻

8) OTEL pipelines
	•	Collector receives OTLP (gRPC) from services; exports to Jaeger/Tempo.
	•	Tail-based sampling: keep traces with error=true or latency>1s.
	•	Log to trace correlation: inject trace_id into logs; Grafana Loki derived fields.

⸻

9) Runbooks (link targets used in alerts)

Create one-page runbooks under docs/ops/runbooks/:
	•	rpc-errors.md, rpc-latency.md
	•	head-stall.md, reorg.md
	•	mempool-pressure.md
	•	da-proof-fail.md
	•	beacon-overdue.md
	•	aicf-sla.md

Each includes: Symptom → Triage steps → Diagnostics queries → Mitigations → Rollback.

⸻

10) Cardinality & cost controls
	•	Bound label sets: method (RPC) top 50; peer_id not exported.
	•	Use recording rules to pre-aggregate by component/instance.
	•	Retention (Prom): 15–30d; long-term store in Thanos/VictoriaMetrics if needed.
	•	Sample rates for traces; reduce histogram buckets to fewer quantiles where safe.

⸻

11) Security & privacy
	•	TLS for all scrapes over WAN, or private network only.
	•	mTLS for cross-cluster scrapes.
	•	Do not log payloads; hash or truncate identifiers.
	•	Separate public NOC (read-only) from privileged Grafana (PII-restricted).

⸻

12) Quickstart (local dev)

# Start Prometheus + Grafana + blackbox (docker-compose example)
docker compose -f ops/compose/observability.yml up -d

# Point node & services to:
#  - PROMETHEUS_PUSHGATEWAY (optional)
#  - OTEL_EXPORTER_OTLP_ENDPOINT
#  - LOKI_PUSH_URL (if using Loki)


⸻

13) Example queries (cheat sheet)
	•	p99 RPC latency:

histogram_quantile(0.99, sum by (le) (rate(rpc_request_latency_seconds_bucket[5m])))

	•	Error ratio:

sum(rate(rpc_requests_total{status="error"}[5m])) / sum(rate(rpc_requests_total[5m]))

	•	Head lag max (5m):

max_over_time(p2p_head_lag_blocks[5m])

	•	Block interval p95:

quantile_over_time(0.95, consensus_block_interval_seconds[1h])

	•	AICF SLA fail rate:

rate(aicf_sla_fail_total[30m])


⸻

14) On-call workflow
	1.	Page received → Open Grafana panel from alert link.
	2.	Check exemplars → Jump to trace.
	3.	Run runbook triage queries.
	4.	Mitigate (scale, drain, throttle, rollback).
	5.	Post-incident: fill template (impact, MTTR, action items).

⸻

15) Appendix — metric keys reference
	•	RPC: rpc_requests_total{method,status}, rpc_request_latency_seconds_bucket
	•	P2P: p2p_handshake_success_total, p2p_rtt_ms_bucket, p2p_peers, p2p_head_lag_blocks
	•	Consensus: consensus_block_interval_seconds, consensus_reorg_depth_total, consensus_theta_value, consensus_lambda_obs
	•	Mempool: mempool_txs, mempool_min_fee_gwei, mempool_reject_total{reason}, mempool_evictions_total
	•	Execution: exec_apply_tx_seconds_bucket, exec_fail_total{reason}, exec_gas_used_total
	•	DA: da_post_latency_seconds_bucket, da_proof_verify_seconds_bucket, da_proof_fail_total, da_sampler_success_ratio
	•	Randomness: rand_round_status, rand_vdf_verify_seconds_bucket, rand_proof_invalid_total
	•	AICF: aicf_queue_depth, aicf_assign_latency_seconds_bucket, aicf_sla_fail_total{dim}, aicf_provider_healthy
	•	Mining: miner_submit_latency_seconds_bucket, miner_share_reject_total{reason}, miner_template_age_seconds

⸻

Last updated: 2025-10-10
Owners: @observability, @netops, @platform
