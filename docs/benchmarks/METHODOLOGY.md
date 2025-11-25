# Benchmarking Methodology
How we measure throughput (TPS), latency, and PoW (hash-share) energy usage for Animica nodes and miners. The goals are **reproducibility**, **comparability across commits/hardware**, and **actionable signals** for engineering.

> TL;DR  
> - **TPS** = committed (on-chain) transactions per second over a steady-state window.  
> - **Latency** = end-to-end (client submit → receipt) percentiles with cold/warm separation.  
> - **PoW energy** = Joules per accepted hash-share and per block, measured from power sensors or external meters.

---

## 1) Scope & Definitions

### Components under test
- **Node (core/ + rpc/ + mempool/ + consensus/ + execution/ + da/)** in single-node and multi-node topologies.
- **Miner (mining/)** CPU-first backend producing `HashShare` proofs; optional GPU backends when enabled.
- Optional subsystems can be toggled **off** to isolate effects (e.g., DA posting, zk hooks, AICF queues).

### Key metrics
- **Throughput (TPS):**  
  \[
  \text{TPS} = \frac{\sum \text{txs committed in window}}{\text{window duration (s)}}
  \]
  Only **on-chain** committed txs (in canonical chain) count.

- **Latency (end-to-end):**  
  For each tx: from **client submit time** (after serialization) to **receipt indexed** (node reports via RPC).  
  We publish **p50/p90/p99** and **max**, with cold/warm breakdown.

- **Mempool admission rate:** accepted vs rejected (fee/DoS) per second.

- **Block KPIs:** block time distribution, gas used per block, orphan/reorg counts.

- **PoW energy:**
  - **Instant power** \(P(t)\) from sensors or meters.
  - **Energy** \(E = \sum P_i \Delta t_i\) (Joules) over a run.
  - **Joules per accepted share** and **Joules per block**:
    \[
    \frac{E}{\#\text{accepted shares}}, \quad \frac{E}{\#\text{blocks}}
    \]
  - Optional **Joules per kHash** if a calibrated hashrate is available.

---

## 2) Test Profiles

We standardize three profiles. Each profile pins **chain params** and **workload mix**:

1. **Local Devnet (smoke):**
   - 1 node + 1 miner on same machine.
   - Block target: ~2–3s (fixed Θ), small gas limit.
   - Tx mix: 80% simple transfers, 20% small contract calls (no DA).
   - Duration: 3 min (30s warm-up, 120s measure, 30s cool-down).

2. **Single-Node Saturation:**
   - 1 node, synthetic clients from the same host.
   - Θ fixed; mempool limit high; DA off by default (optional on).
   - Open-loop generator ramps to overload, then steps down.
   - Duration: 10 min (2m warm-up, 6m measure, 2m cool-down).

3. **Two-Node P2P (latency realism):**
   - Node A (miner colocated) ↔ Node B (client-facing). RTT target: 20–40 ms.
   - Θ retarget on, DA on (small blobs), normal fork-choice.
   - Duration: 15–20 min with steady trickle + bursts.

**Pin the commit**: record `git describe`, `core/version.py` and module versions in each run artifact.

---

## 3) Workloads

### Transaction mix
- **Transfers** (deterministic gas, no logs).
- **Contract calls** (Counter, Escrow) with predictable events.
- **Optional DA blobs**: 4–64 KiB, posted at fixed rate when testing DA.

Use the shared fixtures and builders:
- `execution/fixtures/` (valid transfers)  
- `vm_py/examples/` (Counter contract)  
- `mempool/cli/replay.py` (rate-controlled ingestion)

### Open-loop vs. closed-loop
- **Open-loop**: submit at a target **arrival rate** (Poisson or fixed inter-arrival). Good for saturation curves.
- **Closed-loop**: each client sends a new tx when the previous one completes. Good for latency SLOs under load.

---

## 4) Environment & Controls

- **Hardware**: record CPU model, cores, SMT, RAM, storage, GPU (if any).
- **Power policy**: disable turbo/boost for stability when comparing energy per share (optional second run with turbo on).
- **Thermal**: ensure adequate cooling; note ambient temp if available.
- **OS**: name/version, kernel, governor (performance/balanced), containerization flags.
- **Process pinning** (optional): pin node/miner to CPU sets for stable perf.

**Time source**: use monotonic clocks for latency (Python `time.perf_counter()` or Rust `Instant`). For multi-host tests, NTP-sync hosts.

---

## 5) Instrumentation

### Node metrics
- Enable Prometheus `/metrics` in `rpc/metrics.py`, `mempool/metrics.py`, `mining/metrics.py`, etc.
- Export at 1s resolution (scrape interval: 1s–2s).

### Client timestamps
- Each submitted tx record:
  ```json
  {"ts_submit": 1700000000.123, "txHash":"0x…", "sender":"anim1…", "bytes": 180}

	•	Each observed receipt record:

{"ts_receipt": 1700000003.456, "txHash":"0x…", "status": "SUCCESS", "block": 12345}



Miner hashrate (HashShare)
	•	Use mining/bench/cpu_hashrate.py for calibration runs at several Θ values.
	•	During production runs, capture miner metrics (abstract shares/s, accepted/rejected).

Power / Energy

Choose one primary and optionally cross-check with a secondary:
	1.	External meter (preferred for GPUs/mixed systems): e.g., in-line AC power meter (log at 1 Hz+).
	2.	CPU package sensors:
	•	Linux: Intel/AMD RAPL via perf stat -a -e power/energy-pkg/.
	•	Linux alt: intel_rapl sysfs.
	•	macOS: powermetrics --samplers tasks --show-initial-usage -n 60 -i 1000.
	3.	GPU:
	•	NVIDIA: nvidia-smi --query-gpu=power.draw --format=csv -lms 100.
	•	AMD ROCm: rocm-smi --showpower -l 1.

Synchronize power logs with benchmark start/stop. Store as CSV with timestamps.

⸻

6) Running the Benchmarks

Common setup

# 1) Build/prepare a fresh DB and genesis
python -m core.boot --genesis core/genesis/genesis.json --db sqlite:///bench.db

# 2) Start node (example: uvicorn via RPC server)
python -m rpc.server --config rpc/config.py

Start miner

# CPU miner with fixed Θ (for energy calibration)
python -m mining.cli.miner --device cpu --threads $(nproc) --share-target 1e-6

Load generation (examples)

# Open-loop: replay a prepared CBOR sequence at X tx/s
python -m mempool.cli.replay --file mempool/fixtures/txs_cbor/tx1.cbor --rate 50 --duration 120

# Closed-loop: tiny Python/TS client that waits for receipt before next submit
# (see sdk/python/examples/deploy_counter.py + call loop)

DA on/off toggles
	•	Enable DA endpoints in RPC (da/adapters/rpc_mount.py) and use sdk/*/da/client to post blobs at a fixed cadence.

⸻

7) Data Reduction & Formulas

Throughput
	•	Compute TPS from committed tx count over the steady-state window.
	•	Also compute effective TPS per tx kind (transfer/call/blob).

Latency
	•	Join submit/receipt logs by txHash.
	•	Compute per-tx latency L = ts_receipt - ts_submit.
	•	Report p50, p90, p99, max.
	•	Warm/cold:
	•	Cold = first block after process start + first 30s of traffic.
	•	Warm = steady-state window after caches/jits (if any) stabilize.

PoW energy
	•	Resample power series to uniform Δt (e.g., 1s), integrate:
[
E = \sum_i P_i \Delta t
]
	•	Derive:
	•	J/accepted-share = (E / N_{\text{accepted}})
	•	J/block = (E / N_{\text{blocks}})
	•	Optional J/kHash = (E / (\text{hashrate}\cdot T / 1000)) when hashrate T is separately measured.
	•	If both CPU and GPU are used, sum device energies (or use whole-system AC meter).

Error bars
	•	For TPS/latency: bootstrap 1,000 resamples for 95% CI.
	•	For energy: repeat run ×3; or compute CI from power variability if repeats are costly.

⸻

8) Reporting

Each benchmark run emits a JSON artifact (one line per metric family):

{
  "commit": "v0.8.3-14-gabc123",
  "profile": "single-node-saturation",
  "hardware": {"cpu":"AMD 5950X","ram_gb":64,"gpu":"None"},
  "node": {"params":{"theta":"fixed:2.5s","gasLimit":8000000}},
  "window": {"warmup_s":120,"measure_s":360},
  "tps": {"overall": 512.3, "transfers": 410.2, "calls": 102.1},
  "latency_s": {"p50":0.82,"p90":1.37,"p99":3.45,"max":7.92},
  "blocks": {"avg_time_s":2.51,"reorgs":0},
  "mempool": {"admitted_s":520.0,"rejected_s":8.2},
  "pow_energy": {"joules_total": 18500.0, "j_per_share": 0.028, "j_per_block": 925.0}
}

Store artifacts under bench/outputs/<date>/<profile>.jsonl and attach raw power CSV.

⸻

9) Reproducibility Checklist
	•	Pin commit hash and list Python/Rust toolchains and OS kernel.
	•	Record chain params (Θ, Γ, gas tables) from spec/params.yaml snapshot.
	•	Disable background services that cause jitter (indexers, desktop search).
	•	Fix CPU governor (Linux: performance), disable turbo (optional profile).
	•	Use the same workload seeds where randomness exists (e.g., Poisson arrivals).
	•	For energy, use the same power method (don’t compare AC-metered to RAPL across labs).
	•	Archive exact command lines and environment variables.

⸻

10) Frequently Asked Questions
	•	Q: Why committed TPS, not ingress TPS?
Committed TPS captures what the chain truly processes under consensus, avoiding inflated numbers from rejected or queued txs.
	•	Q: How do retarget dynamics affect results?
For pure throughput studies, fix Θ. For realism, enable retarget and extend the run (≥15 min) to smooth variability.
	•	Q: Do DA blobs skew latency?
Yes; include DA-on runs separately and report blob sizes and acceptance rate.
	•	Q: Where do we measure PoW energy in PoIES?
On the hash-share mining component only. AI/Quantum energy is accounted under AICF economics separately.

⸻

11) Example Make Targets (optional)

You can wire phony targets for repeatability:

bench-local:
\tpython -m rpc.server &
\tsleep 2
\tpython -m mining.cli.miner --device cpu --threads $$(( $$(nproc) / 2 )) --share-target 1e-6 &
\tsleep 2
\tpython -m mempool.cli.replay --file mempool/fixtures/txs_cbor/tx1.cbor --rate 50 --duration 180

power-log-linux:
\tperf stat -a -e power/energy-pkg/ -I 1000 -- sleep 180 | tee bench_power.log


⸻

12) Ethics & Transparency
	•	Avoid “hero numbers.” Always publish config, commit, hardware, and raw artifacts.
	•	Do not extrapolate cross-hardware without a clear model.
	•	Include energy per block/share alongside TPS to surface efficiency trade-offs.

