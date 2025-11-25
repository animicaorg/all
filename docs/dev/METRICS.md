# Metrics — Prometheus Counters, Histograms & Dashboards

This guide explains **what we measure**, **how we name it**, and **how to build Grafana dashboards and alerts** for the Animica stack (RPC, mempool, consensus, P2P, DA, ZK, AICF, execution, randomness, wallet services).

- Exporters: every service exposes **`/metrics`** (Prometheus text format).
- Traces/exemplars: histograms optionally attach **trace exemplars** when tracing is enabled; see `docs/dev/DEBUGGING.md`.

---

## 0) TL;DR — Quick Start

Prometheus scrape (local dev):

```yaml
scrape_configs:
  - job_name: "animica"
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:8545","localhost:8600","localhost:8787","localhost:8080"]

Top three SLO panels (per environment):
	•	RPC success rate:
1 - sum(rate(rpc_errors_total[5m])) / sum(rate(rpc_requests_total[5m]))
	•	RPC p95 latency:
histogram_quantile(0.95, sum(rate(rpc_latency_ms_bucket[5m])) by (le))
	•	Chain progress (blocks/min):
rate(blocks_sealed_total[5m]) * 60

Alert if chain stalls 10m:

increase(blocks_sealed_total[10m]) < 1


⸻

1) Metric Conventions

1.1 Names
	•	snake_case, prefixes by domain: rpc_, mempool_, consensus_, p2p_, da_, zk_, aicf_, exec_, rand_.
	•	Suffix by type:
	•	_total for counters
	•	_ms for milliseconds (gauges or histograms)
	•	_bytes for sizes
	•	_bucket for histogram buckets

1.2 Labels (low cardinality)
	•	Common: env, svc, version (optional via relabeling), method, topic, scheme, backend.
	•	Avoid unbounded labels (user ids, tx hashes). If needed, sample or map to categories.

1.3 Units & buckets
	•	Latency histograms use ms with buckets:
1,2,5,10,25,50,100,250,500,1000,2500,5000
	•	Sizes: bytes; Rates computed with rate()/irate().

1.4 Exemplars (optional)

When tracing is on and exemplars are enabled (PROM_EXEMPLARS=1), latency histograms carry exemplar trace ids.

⸻

2) Metrics by Component (canonical set)

2.1 RPC
	•	rpc_requests_total{method} — Counter
	•	rpc_errors_total{method,code} — Counter
	•	rpc_inflight{method} — Gauge
	•	rpc_latency_ms_bucket{method,le} — Histogram
	•	rpc_ws_clients — Gauge
	•	rpc_ws_broadcasts_total{topic} — Counter

Derived
	•	p95 per method:
histogram_quantile(0.95, sum(rate(rpc_latency_ms_bucket[5m])) by (le,method))

2.2 Mempool
	•	mempool_admitted_total — Counter
	•	mempool_rejected_total{reason} — Counter (FeeTooLow|NonceGap|InvalidSig|DoS)
	•	mempool_size — Gauge (# tx)
	•	mempool_ready — Gauge (# ready to include)
	•	mempool_evicted_total{reason} — Counter
	•	mempool_admission_ms_bucket — Histogram

2.3 Consensus
	•	blocks_sealed_total — Counter
	•	interblock_seconds_bucket — Histogram
	•	theta_value — Gauge (current Θ)
	•	accept_rate — Gauge (recent acceptance % window)
	•	fork_choice_reorg_total{depth} — Counter

2.4 P2P
	•	peers_connected — Gauge
	•	p2p_messages_total{msg} — Counter
	•	p2p_gossip_pub_total{topic} — Counter
	•	p2p_gossip_drop_total{reason} — Counter
	•	p2p_rtt_ms_bucket — Histogram

2.5 DA (Data Availability)
	•	da_put_total — Counter
	•	da_put_bytes_total — Counter
	•	da_get_total — Counter
	•	da_proof_verify_ms_bucket{kind} — Histogram (inclusion|range)
	•	da_nmt_build_ms_bucket — Histogram

2.6 ZK
	•	zk_verify_total{scheme,backend} — Counter (groth16|plonk|stark, native|python)
	•	zk_verify_ms_bucket{scheme,backend} — Histogram
	•	zk_verify_fail_total{scheme,reason} — Counter
	•	zk_native_unavailable_total{reason} — Counter (fallback signals)

2.7 AICF (AI Compute Fund)
	•	aicf_jobs_enqueued_total{kind} — Counter (AI|Quantum)
	•	aicf_jobs_assigned_total — Counter
	•	aicf_jobs_completed_total{status} — Counter (ok|sla_fail|timeout)
	•	aicf_jobs_latency_ms_bucket{phase} — Histogram (queue|exec|settlement)
	•	aicf_provider_slash_total{reason} — Counter

2.8 Execution
	•	exec_apply_block_ms_bucket — Histogram
	•	exec_apply_tx_ms_bucket{kind} — Histogram (transfer|deploy|call|blob)
	•	exec_gas_used_total — Counter
	•	exec_events_emitted_total — Counter

2.9 Randomness (Beacon)
	•	rand_commits_total — Counter
	•	rand_reveals_total — Counter
	•	rand_vdf_verify_ms_bucket — Histogram
	•	rand_round_finalize_ms_bucket — Histogram
	•	rand_beacon_finalized_total — Counter

⸻

3) Dashboards (Grafana)

3.1 Node Overview
	•	Row: Health
	•	Head growth: rate(blocks_sealed_total[5m])
	•	Peers: peers_connected
	•	SLO (RPC success): 1 - sum(rate(rpc_errors_total[5m])) / sum(rate(rpc_requests_total[5m]))
	•	Row: RPC
	•	p95/p99 latency (per method)
	•	Top 5 methods by QPS: sum(rate(rpc_requests_total[5m])) by (method)
	•	Error heatmap: sum(rate(rpc_errors_total[5m])) by (method,code)
	•	Row: Mempool
	•	Size vs Ready
	•	Admissions vs Rejects (stacked by reason)
	•	Admission latency p95
	•	Row: Consensus/Blocks
	•	Inter-block histogram + p95
	•	Θ (theta_value) over time
	•	Row: P2P
	•	Messages/sec by msg
	•	Gossip drops by reason
	•	RTT p95
	•	Row: DA & ZK
	•	DA throughput bytes/sec
	•	DA proof verify p95
	•	ZK verify p95 by scheme and backend; native fallback ratio:

sum(rate(zk_verify_total{backend="native"}[5m])) 
/ ignoring(backend) sum(rate(zk_verify_total[5m]))


	•	Row: AICF
	•	Queue depth (if exported)
	•	Jobs completed (ok vs sla_fail)
	•	Exec latency p95 by phase

3.2 Chain Ops
	•	Reorgs by depth (bar)
	•	Acceptance rate vs Θ
	•	Block execution time p95
	•	Gas used per block

3.3 Beacon/Randomness
	•	Rounds finalized per hour
	•	Commit/Reveal counts per round
	•	VDF verify latency trend

⸻

4) Recording Rules (examples)

groups:
- name: animica.rules
  interval: 30s
  rules:
  - record: job:rpc_requests_total:rate5m
    expr: sum(rate(rpc_requests_total[5m])) by (job,method)

  - record: job:rpc_latency_ms:hist5m
    expr: sum(rate(rpc_latency_ms_bucket[5m])) by (job,le)

  - record: job:rpc_latency_p95
    expr: histogram_quantile(0.95, job:rpc_latency_ms:hist5m)

  - record: job:blocks_per_min
    expr: rate(blocks_sealed_total[5m]) * 60


⸻

5) Alerts (Prometheus Alertmanager)

groups:
- name: animica.alerts
  rules:
  - alert: RPCErrorRateHigh
    expr: (sum(rate(rpc_errors_total[5m])) / sum(rate(rpc_requests_total[5m]))) > 0.02
    for: 10m
    labels: {severity: page}
    annotations:
      summary: "RPC error rate >2% (10m)"
      runbook: "docs/dev/DEBUGGING.md#51-rpc-is-slow-or-erroring"

  - alert: RPCHighLatencyP95
    expr: histogram_quantile(0.95, sum(rate(rpc_latency_ms_bucket[5m])) by (le)) > 500
    for: 10m
    labels: {severity: page}
    annotations:
      summary: "RPC p95 > 500ms"
      tips: "Check mempool pressure & DB IO."

  - alert: ChainStalled
    expr: increase(blocks_sealed_total[10m]) < 1
    for: 5m
    labels: {severity: page}
    annotations:
      summary: "Chain stalled (no blocks 10m)"
      tips: "Inspect consensus logs; miner templates; Θ."

  - alert: LowPeerCount
    expr: peers_connected < 3
    for: 15m
    labels: {severity: ticket}
    annotations:
      summary: "Few P2P peers"
      tips: "Bootstrap seeds reachable? NAT?"

  - alert: DANegativeProofRate
    expr: sum(rate(da_proof_verify_fail_total[10m])) > 0
    for: 10m
    labels: {severity: ticket}
    annotations:
      summary: "DA proof verify failures"
      tips: "Check nmt/erasure params; recent deploys."

  - alert: ZKFallbackToPython
    expr: (sum(rate(zk_verify_total{backend="native"}[10m])) 
          / ignoring(backend) sum(rate(zk_verify_total[10m]))) < 0.6
    for: 10m
    labels: {severity: ticket}
    annotations:
      summary: "ZK native backend fallback"
      tips: "Ensure native crate loaded; CPU features."


⸻

6) SLOs (suggested)

Service	Objective	Target
RPC availability	Non-5xx ratio	≥ 99.9% / 28d
RPC latency	p95 across methods	≤ 250ms / 5m
Block cadence	Inter-block p95	≤ 2× target interval
P2P health	peers_connected	≥ 8 (prod)
DA verify	p95 proof-verify	≤ 150ms
ZK verify	native path ratio	≥ 90%
AICF SLA	success ratio	≥ 98%

Track error budget burn: roll-up of RPCErrorRateHigh over the window.

⸻

7) Capacity Planning & RED/USE

RED (for RPC): Rate, Errors, Duration
	•	Rate: sum(rate(rpc_requests_total[5m]))
	•	Errors: sum(rate(rpc_errors_total[5m]))
	•	Duration: p95/p99 from histogram

USE (for resources): Utilization, Saturation, Errors
	•	Add node/system exporters (CPU, disk IO, file descriptors).
	•	Correlate rpc_latency_p95 spikes with CPU steal or disk queue.

⸻

8) Multi-Instance & Labels

When scraping multiple nodes:
	•	Add instance, cluster, and role labels.
	•	Use sum by (cluster,method) for SLO roll-ups.
	•	Avoid mixing dev/test/prod in one dashboard; filter by env.

⸻

9) Grafana Tips
	•	Time shift panels to compare today vs. last week.
	•	Use transformations to compute percentages inline.
	•	Enable exemplars in graph styles to jump to traces (Jaeger/Tempo).
	•	Add links on panels to runbooks (docs/dev/DEBUGGING.md anchors).

⸻

10) Frequently Asked

Q: Histogram vs summary?
A: Use histograms for aggregation across instances; summaries can’t be aggregated.

Q: My method label exploded.
A: Cap to top-N methods via dashboard query or pre-aggregate with recording rules; avoid high-cardinality labels.

Q: p99 looks noisy.
A: Extend range to 30–60m, or use p95 for paging, p99 for ticketing.

⸻

11) Validation Checklist
	•	/metrics responds within < 100ms.
	•	No unbounded labels (inspect with promtool tsdb analyze or cardinality panels).
	•	Alert rules tested with amtool and silences configured for maintenance.
	•	Dashboards pinned to a specific folder and versioned (JSON in repo).

⸻

Appendix A — Synthetic Load Recipes

Warm-up RPC at 100 rps for 3 min:

vegeta attack -duration=3m -rate=100 -targets=rpc.targets | vegeta report

Dump worst 10 traces for slow RPCs (Tempo/Jaeger render link):
	•	Enable exemplars; click outliers on latency graph.

⸻

Keep metrics cheap, meaningful, and tied to actions. If a metric cannot trigger a decision, reconsider instrumenting it.

