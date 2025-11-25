# Animica Metrics: Names, Labels, and SLOs

This document standardizes Prometheus metrics exposed by Animica components (node, miner, DA, RPC, P2P, consensus, AICF, randomness, studio-services, explorer). It also defines common labels, units, example PromQL, and SLO targets used by alert rules and Grafana dashboards.

> All counters are monotonically increasing. Durations are **seconds**. Sizes are **bytes** unless noted. Histograms follow Prometheus `_bucket/_sum/_count` conventions.

---

## Naming & Units

- Prefix: `animica_` for first-party metrics.
- Subsystem suffix: `_rpc`, `_p2p`, `_mempool`, `_consensus`, `_da`, `_aicf`, `_rand`, `_miner`, `_vm`, `_exec`, `_services`, `_explorer`.
- Units: `*_seconds`, `*_bytes`, `*_gwei`, `*_ratio`, `*_percent` (0–100), `*_count` (dimensionless), `*_total` (counter).

## Common Labels

| Label            | Description                                                     | Example                  |
|------------------|-----------------------------------------------------------------|--------------------------|
| `chain_id`       | Numeric chain id                                                | `1`, `2`, `1337`         |
| `network`        | Human network name                                              | `mainnet`, `testnet`     |
| `node_role`      | `full`, `validator`, `archive`, `miner`                         | `full`                   |
| `instance`       | Prom default (host:port)                                        | `10.0.0.5:9100`          |
| `job`            | Prom scrape job                                                 | `animica-node`           |
| `method`         | RPC/middleware method                                           | `tx.sendRawTransaction`  |
| `topic`          | Gossip topic                                                    | `txs`, `blocks`, `shares`|
| `reason`         | Reject/eviction/drop reason                                     | `FeeTooLow`, `TTL`       |
| `status`         | `success`/`error` (or domain-specific)                          | `success`                |
| `type`           | Entity type (proof kind, blob ns class, tx kind, etc.)         | `HashShare`, `transfer`  |
| `phase`          | Randomness/round phases; handshake stages; pipeline steps       | `commit`, `reveal`       |
| `provider_id`    | AICF provider short id (hashed/shortened)                       | `p_01ab`                 |
| `alg_id`         | PQ algorithm or KEM id                                          | `dilithium3`             |
| `bucket`         | Histogram auto-label                                            | `0.1`, `0.25`, …         |

---

## Global / Node Core

- `animica_head_height{chain_id}` — Gauge: best known height.
- `animica_finality_lag_seconds{chain_id}` — Gauge: current head vs finalized (or 0 if none).
- `animica_block_time_seconds` — Histogram: inter-block time (observed).
- `animica_state_root_mismatch_total` — Counter: critical invariants (should be 0).

**PromQL examples**
- Average block time (5m):  
  `rate(animica_block_time_seconds_sum[5m]) / rate(animica_block_time_seconds_count[5m])`
- Finality lag alert (>120s for 3m): see `node.yaml` rule.

---

## Consensus / PoIES

- `animica_consensus_accept_total{type}` — Counter: blocks accepted, labeled by dominant proof class.
- `animica_consensus_reject_total{reason}` — Counter: rejects (policy, nullifier, Θ schedule).
- `animica_poies_theta_micro` — Gauge: Θ (µ-nats) current.
- `animica_poies_gamma_in_round{type}` — Gauge: Γ shares used per type in current round.
- `animica_retarget_update_seconds` — Histogram: retarget computation latency.
- `animica_nullifier_hits_total{type}` — Counter: duplicate/nullifier reuses blocked.

**SLOs**
- Θ retarget update p95 < **50ms**
- Reject rate (policy) < **0.5%** rolling 10m

---

## Mempool

- `animica_mempool_size` — Gauge: tx count.
- `animica_mempool_bytes` — Gauge: total bytes tracked.
- `animica_tx_admitted_total{type}` — Counter.
- `animica_tx_rejected_total{reason}` — Counter.
- `animica_tx_replaced_total` — Counter (RBF).
- `animica_fee_floor_gwei` — Gauge.
- `animica_mempool_select_seconds` — Histogram: selection latency for block build.

**SLOs**
- Admission p95 < **10ms**; selection p95 < **50ms**
- Eviction reason `memory_pressure` fraction < **5%** (5m)

---

## RPC (HTTP & WS)

- `animica_rpc_requests_total{method,status}` — Counter.
- `animica_rpc_request_duration_seconds{method}` — Histogram.
- `animica_ws_subscribers{topic}` — Gauge: active subs.
- `animica_ws_broadcast_total{topic}` — Counter.
- `animica_openrpc_served_total` — Counter: `/openrpc.json` hits.

**SLOs**
- JSON-RPC p99 latency < **200ms** (HTTP)
- WS broadcast drop rate < **0.1%**

---

## P2P

- `animica_p2p_peers` — Gauge: connected peers.
- `animica_p2p_dials_total{status}` — Counter: success/error.
- `animica_p2p_handshake_seconds` — Histogram (Kyber+HKDF).
- `animica_gossip_msgs_total{topic}` — Counter.
- `animica_gossip_reject_total{topic,reason}` — Counter (validators).
- `animica_p2p_bytes_total{direction}` — Counter (egress/ingress).

**SLOs**
- Peer count ≥ **min(8, seeds_available)** for public nodes.
- Handshake p95 < **100ms** LAN / < **400ms** WAN.

---

## Mining

- `animica_miner_hashrate_shares_per_second` — Gauge: abstract shares/s at dev Θ.
- `animica_miner_submit_total{status}` — Counter: share/block submits.
- `animica_template_refresh_seconds` — Histogram.
- `animica_vdf_prove_seconds` — Histogram (dev prover; not consensus).

**SLOs**
- Template freshness < **2s** p95.
- Submit success ratio > **99%** (network OK).

---

## Execution & VM

- `animica_exec_apply_tx_seconds` — Histogram: apply latency per tx.
- `animica_exec_out_of_gas_total` — Counter.
- `animica_vm_steps_total` — Counter: interpreter steps.
- `animica_vm_gas_used_total` — Counter.
- `animica_events_emitted_total` — Counter.

**SLOs**
- Apply p95 < **30ms** (transfer), p95 < **150ms** (contract small).

---

## Data Availability (DA)

- `animica_da_post_total{status}` — Counter.
- `animica_da_post_bytes_total` — Counter.
- `animica_da_proof_verify_seconds` — Histogram.
- `animica_nmt_build_seconds` — Histogram.
- `animica_das_sample_fail_probability{window}` — Gauge: estimated p_fail.

**SLOs**
- Post p95 < **300ms** for ≤256KiB.
- Light verify success ratio == **100%** for valid proofs.

---

## Randomness Beacon

- `animica_rand_round_id` — Gauge.
- `animica_rand_commits_total{status}` — Counter.
- `animica_rand_reveals_total{status}` — Counter.
- `animica_vdf_verify_seconds` — Histogram (verifier).
- `animica_beacon_finalized_total` — Counter: successful mixes.

**SLOs**
- Round finalize before next round ETA with success ratio **>99.9%**.
- VDF verify p95 < **150ms** (reference params).

---

## AICF (AI Compute Fund)

- `animica_aicf_jobs_enqueued_total{type}` — Counter.
- `animica_aicf_jobs_assigned_total{status}` — Counter.
- `animica_aicf_jobs_completed_total{status}` — Counter.
- `animica_aicf_sla_pass_ratio{provider_id}` — Gauge (0–1).
- `animica_aicf_slash_events_total{reason}` — Counter.
- `animica_aicf_settlement_seconds` — Histogram.
- `animica_aicf_payout_credits_total` — Counter (units → credits).

**SLOs**
- Assignment latency p95 < **2s** in devnet.
- SLA pass ratio ≥ **0.98** per provider (rolling hour).
- Settlement batch p95 < **1s** for ≤1k claims.

---

## Studio-Services

- `animica_services_deploy_total{status}` — Counter.
- `animica_services_verify_total{status}` — Counter.
- `animica_services_simulate_seconds` — Histogram.
- `animica_services_rate_limit_total` — Counter.
- `animica_services_faucet_total{status}` — Counter.

**SLOs**
- Preflight simulate p95 < **400ms**.
- Deploy relay success ratio ≥ **99%** (excluding user errors).

---

## Explorer API

- `animica_explorer_requests_total{route,status}` — Counter.
- `animica_explorer_request_seconds{route}` — Histogram.
- `animica_explorer_cache_hit_ratio` — Gauge (0–1).

**SLOs**
- p99 latency < **250ms** for all public endpoints.

---

## Example PromQL Snippets

**RPC p99 by method (5m)**
```promql
histogram_quantile(0.99,
  sum by (le, method) (rate(animica_rpc_request_duration_seconds_bucket[5m]))
)

Mempool admit ratio (5m)

sum(rate(animica_tx_admitted_total[5m]))
/
(sum(rate(animica_tx_admitted_total[5m])) + sum(rate(animica_tx_rejected_total[5m])))

Beacon on-time finalization

increase(animica_beacon_finalized_total[1h])
/
increase(animica_rand_round_id[1h])  // approximates rounds elapsed

Gossip error rate

sum(rate(animica_gossip_reject_total[5m]))
/
sum(rate(animica_gossip_msgs_total[5m]))

AICF SLA pass ratio per provider

avg_over_time(animica_aicf_sla_pass_ratio[1h]) by (provider_id)


⸻

Dashboards & Alerts
	•	Grafana dashboards under ops/docker/config/grafana/dashboards/*.json map to the metric families above.
	•	Alert rules in ops/docker/config/rules/*.yaml and ops/k8s/observability/* consume the SLOs specified here. When adjusting thresholds, update both this README and the rule files in the same PR.

⸻

Cardinality Guidance
	•	Prefer low-cardinality labels. Truncate/normalize provider_id. Limit method to canonical RPC names. Avoid unbounded reason values—use an enumerated set.

⸻

Backward Compatibility
	•	Metrics may add labels; removing or renaming requires a deprecation window and dashboard/rule migration notes in the PR description and CHANGELOG.

