# Benchmarks — Current Baselines & Hardware Profiles

This page captures **reference baselines** for Animica components (node, miner, RPC) across a few common hardware profiles. Numbers are **reproducible**, **comparative across commits**, and generated using the procedure in [METHODOLOGY.md](./METHODOLOGY.md).

> **Read me first**
> - These are *baselines*, not theoretical maxima.
> - Results include config and commit metadata; always compare like-for-like.
> - When PoW (hash-share) mining is enabled, we also report **energy per accepted share** and **per block**.

---

## 1) Hardware Profiles

We use stable profile identifiers to compare runs over time.

| ID  | Nickname                     | CPU / RAM                        | Storage            | GPU                 | OS / Kernel            | Notes |
|-----|------------------------------|----------------------------------|--------------------|---------------------|------------------------|-------|
| **HP1** | Dev Desktop (CPU-only)       | AMD Ryzen 9 5950X · 16c/32t · 64 GB | NVMe PCIe 3.0       | —                   | Ubuntu 24.04 LTS (6.x) | Baseline for contributors |
| **HP2** | Workstation (CPU+GPU avail)  | Intel i9-13900K · 24c (8P+16E) · 64 GB | NVMe PCIe 4.0    | NVIDIA RTX 4070     | Ubuntu 22.04 LTS (5.19) | GPU used only in optional miner runs |
| **HP3** | Cloud VM (balanced)          | GCP c2-standard-16 · 16 vCPU · 32 GB | NVMe local SSD     | —                   | Debian 12 (6.x)        | Public cloud reproducibility |

> If you contribute results on other rigs, add a new **HPx** row and keep it stable.

---

## 2) Baseline Results (latest)

**Commit:** `v0.8.3-14-gabc123`  
**Specs snapshot:** `spec/params.yaml@sha256:3a1d…`  
**Date window:** 2025-01-15 → 2025-01-22

### 2.1 Local Devnet (smoke) — 1 node + 1 miner (fixed Θ)

**Profile:** HP1 (CPU-only)  
**Config deltas:** DA off, Θ fixed ≈ 2.5 s, gasLimit 8 M

| Metric                                   | Value            |
|------------------------------------------|------------------|
| **TPS (overall)**                        | **182.7**        |
| Transfers / Calls TPS                    | 146.2 / 36.5     |
| **Latency p50 / p90 / p99 / max (s)**    | 0.60 / 1.10 / 2.80 / 6.1 |
| Block time avg (s)                       | 2.51             |
| Mempool rejected (tx/s)                  | 0.7              |
| **Energy / accepted share (J)**          | **0.030**        |
| Energy / block (J)                       | 950              |

**Artifacts:** `bench/outputs/2025-01-15/local-devnet.jsonl` (plus `power_hp1_local.csv`)

---

### 2.2 Single-Node Saturation — 1 node (Θ fixed), open-loop load

**Profile:** HP1 (CPU-only)  
**Config deltas:** DA off, Θ fixed ≈ 2.6 s, gasLimit 12 M

| Metric                                   | Value            |
|------------------------------------------|------------------|
| **TPS (overall)**                        | **521.4**        |
| Transfers / Calls TPS                    | 409.9 / 111.5    |
| **Latency p50 / p90 / p99 / max (s)**    | 0.85 / 1.50 / 4.10 / 9.3 |
| Block time avg (s)                       | 2.58             |
| Mempool rejected (tx/s)                  | 8.2              |
| **Energy / accepted share (J)**          | **0.031**        |
| Energy / block (J)                       | 1002             |

**Artifacts:** `bench/outputs/2025-01-18/single-node-saturation.jsonl` (plus `power_hp1_sat.csv`)

---

### 2.3 Two-Node P2P — Node A (miner) ↔ Node B (client), retarget on

**Profile:** HP2 (CPU miner; GPU **disabled**)  
**Network:** RTT 25–35 ms; DA **on** (4–32 KiB blobs at 2 Hz)

| Metric                                   | Value            |
|------------------------------------------|------------------|
| **TPS (overall)**                        | **472.8**        |
| Transfers / Calls / Blob TPS             | 355.3 / 97.1 / 20.4 |
| **Latency p50 / p90 / p99 / max (s)**    | 1.10 / 2.00 / 5.20 / 11.6 |
| Block time avg (s)                       | 2.73             |
| Reorgs (per 15 min)                      | 0–1              |
| **Energy / accepted share (J)**          | **0.029**        |
| Energy / block (J)                       | 980              |

**Artifacts:** `bench/outputs/2025-01-22/two-node-p2p.jsonl` (plus `power_hp2_p2p.csv`)

> **Note (GPU miner):** An experimental GPU hash backend on HP2 produced **0.012–0.015 J/share** at similar acceptance rates. Results are labeled separately under `two-node-p2p-gpu.jsonl`.

---

## 3) Regression Budgets

Automated checks fail CI if:
- **TPS** drops by **> 5%** vs the rolling 7-day median on the same **HPx** & profile.
- **Latency p99** increases by **> 10%**.
- **Energy per share** increases by **> 10%** (when power logs are present).

Thresholds are tuned to avoid noise from minor kernel/driver drift. See `.github/bench.yml` (future).

---

## 4) How to Read Artifacts

Each JSON line follows:

```json
{
  "commit": "v0.8.3-14-gabc123",
  "profile": "single-node-saturation",
  "hardware_id": "HP1",
  "specs_sha": "sha256:3a1d…",
  "window": {"warmup_s":120,"measure_s":360},
  "node": {"params":{"theta":"fixed:2.6s","gasLimit":12000000}},
  "tps": {"overall":521.4,"transfers":409.9,"calls":111.5},
  "latency_s": {"p50":0.85,"p90":1.50,"p99":4.10,"max":9.30},
  "blocks": {"avg_time_s":2.58,"reorgs":0},
  "mempool": {"admitted_s":529.6,"rejected_s":8.2},
  "pow_energy": {"joules_total":18500.0,"j_per_share":0.031,"j_per_block":1002.0}
}

Power CSVs are 1 Hz–10 Hz with timestamp,watts (and optional device columns).

⸻

5) Known Variability
	•	Power readings: AC meters vs RAPL/GPU SMI differ in scope; compare only within the same method.
	•	Thermals & boost: sustained boost clocks can skew short runs; prefer ≥6 min steady windows.
	•	DA on/off: enabling DA adds latency variance; report blob sizes and acceptance rate.
	•	Θ retarget: for realism, use retarget runs ≥15 min; for raw throughput, fix Θ.

⸻

6) Roadmap for Bench Coverage
	•	DA heavy profiles (≥ 1 MiB/s app-layer blobs).
	•	zk.verify stress with Groth16/PLONK proof mixes.
	•	VM(Py) contract-heavy mixes (storage writes, events).
	•	Multi-node, adversarial P2P (loss/jitter, gossip backpressure).

Contributions welcome—follow METHODOLOGY.md and submit artifacts under bench/outputs/YYYY-MM-DD/.

⸻

7) Change Log (results doc)
	•	2025-01-22 — Added HP2 P2P with DA-on; included experimental GPU miner note.
	•	2025-01-18 — First single-node saturation baselines on HP1.
	•	2025-01-15 — Initial local-devnet smoke baselines on HP1.

