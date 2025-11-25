# Incident Runbooks — Stall, Fork/Reorg, Data Availability

Operational checklists for high-severity incidents. Each section includes **triggers**, **fast triage**, **diagnostics**, **mitigations**, **communications**, and **post-incident** tasks.

> Prereqs  
> - Observability is configured per [OBSERVABILITY.md](./OBSERVABILITY.md).  
> - On-call has access to: Grafana, Prometheus/Alertmanager, RPC nodes, P2P seeds, DA service, and provider dashboards.  
> - Change freeze & rollback points are known (last green deploy / feature flags).

---

## Index

1. [HEAD STALL — no new blocks](#1-head-stall--no-new-blocks)
2. [FORK / REORG — unexpected depth or frequency](#2-fork--reorg--unexpected-depth-or-frequency)
3. [DATA AVAILABILITY (DA) — sampling/proof issues](#3-data-availability-da--samplingproof-issues)
4. [Appendix: Quick Commands & Queries](#appendix-quick-commands--queries)

---

## 1) HEAD STALL — no new blocks

**Trigger**
- Alert `HeadStalled` firing (`p2p:head_lag:max5m > 5` for 10m).
- External monitors show **height frozen**.
- Miners report `template_age_seconds` growing.

**Severity**
- `page` (SEV-1) on mainnet; `critical` on public testnet.

### Fast triage (≤ 5 minutes)

- ✅ Check **RPC Overview** dashboard: _Head Height_, _Head Lag_, _RPC p99_.
- ✅ Confirm multiple nodes stalled (rule out single-instance issue).
- ✅ Verify **P2P peers** count > minimum (e.g., > 8) and **RTT** sane (< 300ms).
- ✅ Check **Consensus** panel: Θ (theta), λ_obs vs λ_target; ensure Θ not absurd.
- ✅ Inspect **Mempool size** and **min fee**—not stuck on extreme fee floor.
- ✅ Ask miners: are shares/blocks being **rejected** (hash target mismatch / policy roots)?

### Diagnostics (deep dive)

**PromQL**
- Head lag:

max_over_time(p2p_head_lag_blocks[5m])

- Blocks produced:

rate(consensus_blocks_sealed_total[5m])

- Submit rejects (mining):

rate(miner_share_reject_total[5m]) by (reason)

- Mempool pressure:

mempool_txs, mempool_min_fee_gwei

**RPC checks**
```bash
curl -s $RPC_URL/rpc -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}'
curl -s $RPC_URL/rpc -d '{"jsonrpc":"2.0","id":1,"method":"chain.getBlockByNumber","params":["latest",false,false]}'

Node health
	•	Logs: errors around difficulty/Θ schedule, policy roots, DB write stalls.
	•	P2P: churn spikes? handshake failures?

Mitigations
	1.	If Θ too high (retarget runaway after drop in work):
	•	Toggle retarget clamp feature flag to safe bounds (as per spec), or roll back last param push.
	•	Restart consensus service with --theta-clamp restore.
	2.	If P2P partition suspected:
	•	Force seeds refresh; temporarily raise outbound dials and disable strict CORS for WS bootstrap if needed.
	•	Unban mistakenly banned subnets (p2p/peerstore admin command).
	3.	If mempool fee floor jam:
	•	Lower dynamic floor via config flag (temporary), announce in #status.
	•	Drain highest-priority txs to seed the next block.
	4.	If DB saturation / I/O latency:
	•	Move hot RW node to bigger IOPS class; enable write-ahead compression.
	•	Throttle expensive endpoints (rate-limit middleware).
	5.	If miner submit rejects due to policy mismatch:
	•	Verify alg-policy root / DA root at head; ensure miners updated.
	•	Broadcast canonical head via WS; restart outdated miners.

Rollback: revert to last green deploy; disable new features via flags before restart.

Communications
	•	Post to #prod-incidents: incident start, impact (height/time), ETA.
	•	Public status page: “Block production degraded; investigating.”
	•	Ping consensus and p2p owners.

Post-incident
	•	Root cause doc: Θ/policy/p2p/db?
	•	Action items: add recording rule, guardrails for Θ, seed diversity tests, mempool surge tests.
	•	Backfill gaps (if any) in analytics.

⸻

2) FORK / REORG — unexpected depth or frequency

Trigger
	•	Alert ReorgDepthSpike (increase > 3 in 15m).
	•	Explorers show frequent reorgs > 1–2 blocks.

Severity
	•	critical for mainnet; warning for testnet unless user impact.

Fast triage (≤ 5 minutes)
	•	✅ Confirm two or more partitions (diverging heads) via peers’ locator paths.
	•	✅ Check miners’ policy roots & chainId; mismatch often causes accidental minority forks.
	•	✅ Inspect hashshare acceptance and Σψ distributions—are AI/Quantum receipts delayed/out-of-sync?

Diagnostics

PromQL
	•	Reorg depth & frequency:

increase(consensus_reorg_depth_total[30m])


	•	Fork-choice weights:

consensus_fork_weight{branch=~".+"}


	•	Peer counts per branch (if exported):

p2p_peers{branch=~".+"}



RPC

# Compare competing heads
curl -s $RPC_A/rpc -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead"}'
curl -s $RPC_B/rpc -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead"}'

Logs
	•	Look for nullifier reuse rejects, policy-root mismatches, Θ schedule jumps.

Mitigations
	1.	Partition healing
	•	Increase parallel header sync and re-request around fork point.
	•	Temporarily raise reorg depth cap if safe to converge on heavier branch.
	2.	Misconfigured miners / policy
	•	Publish the canonical policy roots; reject blocks with stale roots.
	•	Push a mandatory update notice if version skew caused the split.
	3.	Delayed proofs (AICF / DA) influencing Σψ
	•	Increase proof acceptance grace by one block temporarily.
	•	Backfill proof receipts into lagging nodes.
	4.	Adversarial spam
	•	Tighten share/tx relay limits and per-peer token buckets.
	•	Ban misbehaving peers (rate violations / invalid headers).

Always document which branch was chosen and why (fork-choice weight evidence).

Communications
	•	Live thread in #prod-incidents with branch IDs/hashes.
	•	Public status: “Reorgs above normal; chain converging on heavier branch.”

Post-incident
	•	Add fork simulation to pre-prod.
	•	Enforce version gate for policy roots at epoch boundaries.
	•	Improve fork-choice telemetrics (branch weights / evidence).

⸻

3) DATA AVAILABILITY (DA) — sampling/proof issues

Trigger
	•	Alerts: DAProofFailures (>0.1%/15m), sampler success ratio drops.
	•	Light client verification fails; explorers can’t fetch blobs.

Severity
	•	page if impacts block acceptance or light-client safety.

Fast triage (≤ 5 minutes)
	•	✅ Check DA dashboard: post latency, proof verify latency, failure reasons.
	•	✅ Confirm NMT root in headers matches DA store’s commitment.
	•	✅ Test retrieval of recent blob by commitment (API call below).
	•	✅ Inspect sampler logs for timeouts / namespace errors.

Diagnostics

PromQL
	•	Proof failures by reason:

rate(da_proof_fail_total[15m]) by (reason)


	•	Sampler success:

da_sampler_success_ratio


	•	Retrieval failures:

rate(da_retrieval_fail_total[15m])



API checks

# Get blob by commitment
curl -fS "$DA_URL/da/blob/0x<commitment_hex>" -o /dev/null

# Get availability proof
curl -s "$DA_URL/da/proof?commitment=0x<commitment_hex>&samples=32"

Header vs DA
	•	Parse block header da_root; recompute NMT root from local DA index (if available).

Mitigations
	1.	Hot path saturation
	•	Enable LRU proof cache; increase worker pool for proof generation.
	•	Add short-term rate tiers; throttle abusive clients.
	2.	NMT root mismatch
	•	Reject affected blocks; announce invalidity reason.
	•	Trigger rebuild of index; verify merkle namespace rules.
	3.	Erasure-coded shards missing
	•	Increase sampling retries & widen fetch peers.
	•	Reintroduce redundant storage nodes; rebalance.
	4.	Light-client failures
	•	Ship light-proof hotfix if serialization bug; keep canonical format pinned.

If DA is degraded but chain can proceed, switch to degraded mode: accept with tighter sampling and flagged headers (operator override).

Communications
	•	Post impact: which heights / commitments affected.
	•	Public status: “DA proof retrieval degraded; working with operators.”

Post-incident
	•	Expand DAS probability tests in CI (vectors).
	•	Add namespace-range negative-case tests from corpus.
	•	Stress test erasure/RS decoder under corruption rates.

⸻

Appendix: Quick Commands & Queries

JSON-RPC (curl)

# Head & block
curl -s $RPC_URL/rpc -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}'
curl -s $RPC_URL/rpc -d '{"jsonrpc":"2.0","id":1,"method":"chain.getBlockByNumber","params":["latest",false,false]}'

# Mempool
curl -s $RPC_URL/rpc -d '{"jsonrpc":"2.0","id":1,"method":"state.getNonce","params":["anim1..."]}'

DA REST

curl -fS "$DA_URL/da/blob/0x<commitment>" -o /tmp/blob.bin
curl -s "$DA_URL/da/proof?commitment=0x<commitment>&samples=48" | jq .

PromQL snippets

histogram_quantile(0.99, sum by (le) (rate(rpc_request_latency_seconds_bucket[5m])))
sum(rate(rpc_requests_total{status="error"}[5m])) / sum(rate(rpc_requests_total[5m]))
max_over_time(p2p_head_lag_blocks[5m])
increase(consensus_reorg_depth_total[30m])
rate(da_proof_fail_total[15m]) by (reason)


⸻

Change Log
	•	2025-10-10: Initial version aligned with OBSERVABILITY dashboard & alert names.

Owners: @oncall-core, @oncall-consensus, @oncall-da
Escalation: PagerDuty “Animica Prod” service → L2 Platform
