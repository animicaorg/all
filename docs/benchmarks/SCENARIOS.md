# Benchmarks — Scenarios (Synthetic vs Realistic Mixes)

This guide defines **repeatable traffic scenarios** to evaluate Animica’s throughput, latency, and power characteristics across components (node, mempool, execution, DA, zk, mining). We split scenarios into two families:

- **Synthetic (SYN-\*)** — tightly controlled, single-variable stress tests for capacity planning and regressions.
- **Realistic (RL-\*)** — production-like mixes (DEX-style activity, blobs/DA, zk.verify, AICF jobs), tuned to reflect expected mainnet usage.

Each scenario produces structured results (JSONL) suitable for time-series tracking and CI regression gates (see `RESULTS.md`).

---

## 1) Scenario Schema (common fields)

A scenario is a small YAML/JSON document with these keys:

```yaml
id: SYN-01-CPU-TPS
label: "CPU-only TPS — fixed Θ, DA off"
duration_s: 360
warmup_s: 120
seed: 42                            # RNG seed for reproducibility

node:
  theta_mode: "fixed:2.5s"          # or "retarget:lambda=0.4/s,window=120s"
  gas_limit: 8000000
  da_enabled: false
  zk_enabled: false

workload:
  arrival:                          # request process = open-loop unless noted
    kind: poisson                   # poisson | uniform | bursty
    lambda_per_s: 220               # average arrivals/s (global)
    jitter: 0.15                    # optional multiplicative jitter (0–1)
  mix:                              # relative weights (sum≈1.0)
    transfer: 0.85                  # simple balance transfers
    call_vm_light: 0.15             # small contract calls (e.g., Counter.get/inc)
    call_vm_heavy: 0.00             # heavier calls (storage writes, events)
    blob_post: 0.00                 # DA posts/sec derived from arrivals * weight
    zk_verify: 0.00                 # fraction of calls that invoke zk.verify
  blobs:
    avg_bytes: 0                    # mean blob size; 0 disables
    size_dist: fixed                # fixed | lognormal | bimodal
  zk:
    scheme: null                    # groth16 | plonk | stark | null
    rate_per_s: 0                   # optionally override mix-derived rate

p2p:
  peers: 1                          # standalone unless >1 (multi-node runs)
  net:
    rtt_ms: 1                       # 1 == loopback; raise for WAN tests
    jitter_ms: 0
    loss: 0.0

measurement:
  capture_power: true               # record joules/share, joules/block if available
  export_dir: "bench/outputs/auto"  # where JSONL and CSV artifacts go

Arrival model: Use open-loop (Poisson arrivals) to measure system capacity; use closed-loop to measure end-to-end user-perceived latency at steady concurrency (not shown above).

⸻

2) Synthetic Scenarios (SYN-*)

Designed for tight confidence intervals and quick bisecting.

ID	Objective	Key Knobs	Suggested Duration
SYN-01-CPU-TPS	Max TPS (CPU-only)	Θ=fixed:2.5s, DA off, transfer:85%, call_vm_light:15%	6–8 min
SYN-02-VM-HEAVY	Execution & storage pressure	Θ fixed, DA off, call_vm_heavy:60%, light:40%, avg writes/call ≈ 6 keys	8–10 min
SYN-03-DA-HEAVY	DA path throughput & NMT/erasure	Θ fixed, DA on, blob_post weight=0.20, avg_bytes=128 KiB, size_dist=lognormal	10–12 min
SYN-04-ZK-VERIFY	zk.verify overhead (Groth16/PLONK/STARK)	zk.scheme ∈ {groth16, plonk, stark}, zk_verify weight=0.25, rotate schemes between runs	8–10 min
SYN-05-MINER-ENERGY	Energy per accepted share/block	Θ fixed, mempool idle (no extra tx), report J/share, J/block, miner-only background	6–8 min

Example (SYN-03-DA-HEAVY):

id: SYN-03-DA-HEAVY
label: "DA-heavy — 128 KiB avg blobs @ 0.2 mix"
duration_s: 600
warmup_s: 120
node: { theta_mode: "fixed:2.6s", gas_limit: 12000000, da_enabled: true }
workload:
  arrival: { kind: poisson, lambda_per_s: 180 }
  mix: { transfer: 0.55, call_vm_light: 0.25, blob_post: 0.20 }
  blobs: { avg_bytes: 131072, size_dist: lognormal }
p2p: { peers: 1, net: { rtt_ms: 1, jitter_ms: 0 } }
measurement: { capture_power: true, export_dir: "bench/outputs/$(date +%F)/syn-da" }


⸻

3) Realistic Scenarios (RL-*)

Incorporate real contract ABIs, event logs, zk and DA traffic, and multi-node effects.

ID	Objective	Mix & Notes	Suggested Duration
RL-01-DEX	DEX-like flow (swaps, LP add/remove, transfers)	call_vm_light:0.35, call_vm_heavy:0.25 (pool updates), transfer:0.30, zk_verify:0.10 (proofed actions)	12–15 min
RL-02-NFT	Mint/reveal/trade cadence	call_vm_heavy:0.45, light:0.15, transfer:0.25, blob_post:0.15 (images/metadata pins)	12–15 min
RL-03-AICF	AI/Quantum job requests + result consumption	call_vm_light:0.35, call_vm_heavy:0.15, transfer:0.20, zk_verify:0.10, blob_post:0.20 (receipts/artifacts)	15–20 min
RL-04-MULTINODE	2–5 nodes; header/tx/share gossip & retarget	p2p.peers≥2, net.rtt_ms=25–60, retarget on; mix from RL-01 baseline	20+ min

Example (RL-04-MULTINODE):

id: RL-04-MULTINODE
label: "2-node P2P, retarget on — DEX baseline mix"
duration_s: 1200
warmup_s: 180
seed: 7
node: { theta_mode: "retarget:lambda=0.40/s,window=180s", gas_limit: 12000000, da_enabled: true }
workload:
  arrival: { kind: poisson, lambda_per_s: 260, jitter: 0.2 }
  mix: { transfer: 0.30, call_vm_light: 0.35, call_vm_heavy: 0.25, zk_verify: 0.10 }
  blobs: { avg_bytes: 65536, size_dist: bimodal }   # e.g., 32 KiB and 256 KiB modes
  zk: { scheme: groth16, rate_per_s: 26 }
p2p: { peers: 2, net: { rtt_ms: 35, jitter_ms: 6, loss: 0.002 } }
measurement: { capture_power: true, export_dir: "bench/outputs/$(date +%F)/rl-multinode" }


⸻

4) Workload Generators

Two patterns:
	•	Open-loop (recommended for capacity): arrivals are independent of system latency.
	•	Use Poisson with rate λ (events/s) for realistic aggregation of many clients.
	•	Optional bursty generator: alternating ON/OFF with heavy-tailed ON durations (Pareto α∈[1.2,1.8]).
	•	Closed-loop (user concurrency): maintain N in-flight operations; a new op is issued when one completes.
	•	Good for latency distributions at fixed concurrency; not for max-TPS exploration.

Tip: Keep seed fixed to compare commits; vary only one knob at a time.

⸻

5) What to Measure (per run)
	•	Throughput: TPS overall + per-kind (transfer/call/blob/zk).
	•	Latency: p50/p90/p99/max end-to-end; block time average.
	•	Errors: admission rejects (by reason), execution failures, DA proof failures.
	•	Mempool: admitted/s, rejected/s, eviction events.
	•	Consensus: Θ (target), observed block interval distribution, reorgs.
	•	Power (optional): joules/share, joules/block, watts time-series (CPU/GPU if available).
	•	P2P: gossip fanout, backpressure (if multi-node).

⸻

6) Running Scenarios with Existing Tools

You can approximate scenarios with shipped CLIs:
	•	Start node & miner (fixed Θ):

python -m core.boot --genesis core/genesis/genesis.json --db sqlite:///bench.db
python -m mining.cli.miner --threads 8 --device cpu


	•	Open-loop tx generator (Poisson):

# Replay CBOR txs at a controlled rate (adjust burstiness via your driver)
python -m mempool.cli.replay --file mempool/fixtures/txs_cbor/tx1.cbor --rate 200


	•	DA blobs:

python -m da.cli.put_blob --ns 24 docs/benchmarks/RESULTS.md


	•	zk.verify micro-bench (per-scheme): see zk/bench/verify_speed.py.

Automating full scenarios is encouraged—store your YAMLs under docs/benchmarks/scenarios/ and teach your runner to honor the schema above.

⸻

7) Reproducibility
	•	Pin commit, specs hashes, scenario YAML, hardware ID (HPx), and seed in each artifact header.
	•	Record Θ mode, gas limit, DA on/off, and zk scheme explicitly.
	•	Keep power-measurement method constant across comparisons.

⸻

8) Naming & Storage

Artifacts should live at:

bench/outputs/YYYY-MM-DD/<scenario-id>/*.jsonl
bench/outputs/YYYY-MM-DD/<scenario-id>/*power*.csv

Filename prefix: <commit>_<HPx>_<short-scenario>_w<warmup>_d<duration>.jsonl.

⸻

9) Scenario Catalog (initial)
	•	SYN-01-CPU-TPS
	•	SYN-02-VM-HEAVY
	•	SYN-03-DA-HEAVY
	•	SYN-04-ZK-VERIFY
	•	SYN-05-MINER-ENERGY
	•	RL-01-DEX
	•	RL-02-NFT
	•	RL-03-AICF
	•	RL-04-MULTINODE

Propose additions via PR: include a scenario YAML, rationale, and a baseline artifact.

