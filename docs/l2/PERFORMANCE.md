# Animica L2 — Performance Report Template

This is the report template for L2 throughput claims. Rules of engagement:

- **Nothing here is a marketing figure.** Every number must come from the
  harness (`l2/bench.py`, driven by `animica l2 bench`), be labeled with its
  machine and conditions, and be reproducible from a seed.
- **The real TPS of the system is the lowest sustainable stage of the
  pipeline.** Quoting the fastest stage (execution alone) as "TPS" is
  dishonest; a report must show every stage and name the bottleneck.

## Methodology

### Harness

`l2/bench.py` measures the real pipeline — signature verification → admission
→ parallel execution → SMT update → DA encode → proof — on an **ephemeral
in-memory devnet node** (chain id 421337); it can never touch a mainnet
account. Workloads and accounts are deterministic from `--seed`.

Workloads (spec §30):

| Workload | Shape | What it stresses |
|---|---|---|
| `transfers` | unique sender → unique receiver | maximum parallelism |
| `hot` | many senders → few accounts | contention (collapses toward serial — the only order-preserving behavior for a shared account) |
| `payments` | Zipf-distributed merchants | realistic commerce |
| `inference` | tiny INFERENCE_PAYMENTs | AI micropayments |
| `agent` | bidirectional AGENT_PAYMENTs | machine-to-machine |
| `batch` | large BATCH_PAYMENTs | effective transfers/sec ≫ tps |

Signing is the dominant cost of realistic PQ workloads, so the harness runs
both **pre-signed** (isolates verify+execute+state+DA throughput) and
**inline-signed** (true end-to-end) and reports both, making the bottleneck
explicit. `execution_only()` additionally isolates the non-crypto pipeline.

### Ramp (spec §31)

Ramp mode increases offered load step-wise until a pipeline stage saturates
(admission latency p99 grows without bound, or the pending queue hits
`max_pending` and `submit` starts rejecting), then records the highest offered
rate sustained for a full window with a stable queue. That figure — not a
burst — is the sustainable TPS.

### Producing the numbers

```bash
animica l2 bench --workload transfers --count 20000 --workers 8 --seed 1
animica l2 bench --workload inference --count 20000 --workers 8 --seed 1
animica l2 bench --scaling --workload transfers --count 20000 --workers 1,2,4,8
```

(Equivalent library entry points: `l2.bench.run_once`, `l2.bench.scaling_report`,
`l2.bench.execution_only`.) Results are `BenchResult` JSON:
`{workload, count, workers, presigned, seconds, tps, effective_ops_per_sec,
sig_backend, p50_ms, p95_ms, p99_ms, compressed_bytes, raw_bytes}`.

## Theoretical ceilings (compute these for the report's machine)

Each pipeline stage has a ceiling; the system ceiling is the minimum.

| Stage | Formula | Notes |
|---|---|---|
| Wire / bytes-per-tx | `bytes_per_tx ≈ sig(3309) + pubkey(1952) + body(~80–140)` ≈ **5.3–5.4 KB** | ML-DSA-65 dominates; measured: 5340 B (transfer), 5402 B (inference) |
| Signature verification | `verify_ceiling = per_core_verifies_per_sec × cores` | Batched via `l2/crypto.py`; report `sig_backend` (liboqs vs fallback) — they differ by orders of magnitude |
| Execution + state | `exec_ceiling = execution_only(count, workers)` tps | Component-parallel; `transfers` is the upper bound, `hot` the lower |
| DA encode | `da_ceiling = da_bytes_per_sec / bytes_per_tx` | zlib level 6 over dictionary-encoded body |
| Proof (VALIDITY) | ≈ verify+exec ceiling of the verifying machine | re-execution proof: generation is cheap (DA check), verification costs one full replay |
| L1 settlement | `settle_ceiling = (batches_per_L1_block × batch_max_txs) / L1_block_time_s` | With `batch_max_txs = 50_000`, one anchoring tx per L1 block is far above every other stage — DA byte budget on L1, not tx count, is the binding constraint |

**Report the minimum and name it.** On PQ-signature workloads the expected
bottleneck is signature verification.

## Example report — measured on the dev box

> **Label: measured on the dev box** (10 vCPU Linux 6.8, Python 3.12,
> `sig_backend=liboqs` 0.14, seed 1, presigned admission, VALIDITY mode,
> in-memory store, single batch force-closed). Small `count=500` smoke runs —
> **not** ramp-mode sustained figures. Fields marked `TBD` await a real
> measurement from `animica l2 bench`; do not fill them by extrapolation.

| Metric | Value | Source |
|---|---|---|
| bytes/tx (transfer, wire) | 5,340 B | `raw_bytes/count` |
| bytes/tx (inference, wire) | 5,402 B | `raw_bytes/count` |
| DA compression (transfers, 500 tx) | 2,670,000 → 2,646,389 B (×1.009) | signatures are high-entropy; only structure compresses |
| end-to-end tps, `transfers`, workers=1 | 660.7 | `run_once('transfers', 500, 1)` |
| end-to-end tps, `transfers`, workers=4 | 796.3 | `run_once('transfers', 500, 4)` |
| end-to-end tps, `transfers`, workers=8 | 779.5 | plateau → verification-bound, as predicted |
| end-to-end tps, `inference`, workers=4 | 760.1 | micropayments cost the same as transfers |
| execution-only tps (no sig verify), workers=4 | 3,790.8 | `execution_only(2000, 4)` |
| admission latency p50 / p95 / p99 (ms) | 0.23 / 0.29 / 0.56 | per-`submit`, workers=4 |
| **Bottleneck stage** | **signature verification** | exec-only ceiling ≈ 4.8× the end-to-end rate |
| ramp-mode sustained TPS (§31) | TBD — pending `animica l2 bench` ramp run at `count ≥ 100_000` | |
| `hot` / `payments` / `agent` / `batch` workload table | TBD — pending harness run | |
| DA bytes/sec at sustained load | TBD | `compressed_bytes / seconds` at ramp point |
| L1 settlement rate (anchors/hour, devnet) | TBD — requires `ANIMICA_L2_SETTLEMENT_ENABLED=1` against a devnet L1 | |
| proof verify time per batch (independent node) | TBD | `l2_verifyBatch` timing |

Reading of the example: on this box the non-crypto pipeline clears ~3.8k tps,
but end-to-end throughput sits at ~0.66–0.8k tps and stops scaling past 4
workers — ML-DSA-65 verification is the wall, exactly where a PQ rollup should
expect it. Engineering effort that raises real TPS must attack verification
throughput (more cores for the verifier pool, `ANIMICA_L2_SIG_WORKERS`, or a
faster liboqs build), not the executor.

## Checklist for publishing a report

1. State machine, core count, Python version, `sig_backend`, seed, mode.
2. One table per workload; presigned and inline-signed variants.
3. Scaling table across worker counts; call out where it plateaus.
4. Compute the theoretical ceilings above for the machine; report
   `min(stage ceilings)` next to the measured sustained TPS.
5. Include `compressed_bytes`/`raw_bytes` so DA cost claims are checkable.
6. Never delete the conditions label. A number without its conditions is wrong.
