# Load & Soak Testing Playbook

This folder documents how to **stress**, **soak**, and **capacity-test** an Animica devnet stack:
- `core/` node (DB + consensus + execution)
- `rpc/` (HTTP JSON-RPC + WebSocket)
- `mempool/`, `mining/`, `p2p/`, `da/`, `randomness/`
- optional: `capabilities/`, `aicf/`, and studio services

> This is a **playbook** with reproducible recipes, targets, and metrics.
> The actual drivers (Locust/k6/Vegeta/wrk, etc.) are external tools you run
> against the running node(s). You can keep per-scenario configs alongside this
> README when you add them.

---

## 0) Goals & Terminology

- **Throughput**: sustained ops/sec (requests/sec, tx/sec, blobs/sec).
- **Latency**: p50/p90/p99 end-to-end (client → receipt, or PUT → proof).
- **Soak**: run for hours at realistic rate to catch leaks and drift.
- **Capacity**: find the knee where error rate/latency spike (saturation point).
- **SLI/SLO examples** (tune per hardware):
  - `rpc.chain.getHead` p99 ≤ 200ms @ 200 rps
  - `tx.sendRawTransaction` ack p99 ≤ 400ms; **receipt** within ≤ 2 blocks median
  - Mempool admission error rate ≤ 0.1% at floor gas price
  - P2P header/tx propagation p95 ≤ 1.0s within a 5-peer cluster
  - DA `POST /da/blob` (256 KiB) p95 ≤ 400ms; `GET + /proof` p95 ≤ 500ms

---

## 1) What to Test (workloads)

1) **RPC Submit Storm**
   - Fire signed **transfer** txs with distinct senders/nonces.
   - Measure: submit latency, mempool admission rate, **time-to-receipt**.

2) **Mempool Saturation / RBF**
   - Many senders with nonce queues; mix replacements (raise effective fee).
   - Observe: watermark raises, evictions, fairness caps (see `mempool/*`).

3) **Miner Pressure**
   - Enable built-in miner at a dev difficulty; verify **blocks/sec** rises
     while **orphans/rejects** remain near zero; confirm receipts emitted.

4) **P2P Propagation**
   - 3–7 nodes; broadcast headers/txs; measure fanout delay & duplicate suppression.

5) **Data Availability**
   - Ramp **POST /da/blob** for various sizes (4 KiB → 1 MiB).
   - Read back (`GET`) and verify **light proofs**; track error/latency.

6) **Capabilities / AICF**
   - Enqueue **AI** and **Quantum** jobs at configurable rates.
   - Use dev stubs (proofs resolved next block) → measure SLA pass rate & queueing delay.

7) **Randomness Beacon**
   - Drive **commit → reveal → VDF verify** across multiple rounds at load.
   - Ensure windows partition correctly and VDF verifies within the round.

> Tip: Run single-workload tests first to identify subsystem ceilings, then run **mixed profiles** to expose interference (e.g., DA + RPC + mining at once).

---

## 2) Environment & Pre-Reqs

- Python 3.11+, `pip install prometheus-client httpx websockets`
- External load tools (pick one or more):
  - **k6** (HTTP + WS), **Vegeta**, **wrk**, **hey**, **Locust**
- A running devnet node:
  ```bash
  # Example: boot node DB + start RPC (adjust paths/ports to your config)
  python -m core.boot --genesis core/genesis/genesis.json --db sqlite:///devnet.db
  python -m rpc.server --host 0.0.0.0 --port 8545

	•	Optional aux:
	•	mining/cli/miner.py for local hashing
	•	da/retrieval/api.py mounted via da/adapters/rpc_mount.py
	•	capabilities/rpc/mount.py, aicf/rpc/mount.py for job endpoints

⸻

3) Target Endpoints & Streams
	•	HTTP JSON-RPC: POST /rpc (batch allowed). See spec/openrpc.json.
	•	WebSocket: GET /ws with topics newHeads, pendingTxs.
	•	DA REST: POST /da/blob, GET /da/blob/{commitment}, GET /da/proof?...
	•	Capabilities/AICF (optional): cap.getJob, aicf.listJobs, etc.

⸻

4) Metrics & Telemetry

Every service mounts Prometheus /metrics if enabled (see *_ /metrics.py):
	•	RPC: request counters, histograms per method; WS subscriber stats
	•	Mempool: admits/rejects, evictions, watermark, queue sizes
	•	Mining: hashrate (abstract shares/s), submit latencies, rejects
	•	DA: post/get/proof counters, bytes/s, sampler stats
	•	Randomness: commits/reveals counts, VDF verify seconds histogram
	•	Capabilities/AICF: enqueue/resolve rates, SLA pass/fail

Example Prometheus scrape (inline sample):

scrape_configs:
  - job_name: "animica-devnet"
    scrape_interval: 2s
    static_configs:
      - targets: ["127.0.0.1:8545"]   # rpc /metrics
        labels: {role: "rpc"}
      - targets: ["127.0.0.1:8666"]   # da /metrics if separate
        labels: {role: "da"}


⸻

5) Reproducibility
	•	Seeds: set PYTHONHASHSEED=1337 and tool-specific seeds.
	•	CPU pinning: taskset -c 0-3 …; optionally disable turbo for stable runs.
	•	Warmup: ignore first 30–60s to avoid cold caches/JIT.
	•	Duration: for soak, run ≥ 2h; for spike, run 1–2 min ramps.

⸻

6) Example Command Lines

RPC submit storm (k6)

rpc.json body for batch tx.sendRawTransaction requests is up to you.

k6 run --vus 200 --duration 2m \
  --env RPC_URL=http://127.0.0.1:8545/rpc \
  scripts/k6_rpc_submit.js

DA PUT/GET/PROOF (hey)

# PUT (256 KiB)
dd if=/dev/urandom bs=256K count=1 of=/tmp/blob.bin
hey -z 60s -c 50 -m POST -T application/octet-stream \
  -D /tmp/blob.bin http://127.0.0.1:8666/da/blob

WS newHeads fanout (k6)

k6 run --vus 200 --duration 60s scripts/k6_ws_newheads.js

Keep your scripts under tests/load/ when you add them (e.g., k6_*.js,
locustfile.py, vegeta.txt). Commit with clear headers about method mix,
payload size, and expected SLOs.

⸻

7) Suggested Profiles

Profile	Rate/Users	Mix	Success Criteria (example)
RPC-light	200 rps	70% chain.getHead, 30% state.getBalance	p99 < 200ms, err < 0.1%
Submit-only	50–300 tx/s	100% tx.sendRawTransaction	admit err < 0.5%, median receipt < 2 blocks
Mixed	150 rps + 50 tx/s	60% reads, 40% writes	p95 RPC steady; blocks/sec stable
DA-heavy	30 PUT/s (64–512 KiB)	60% PUT, 30% GET, 10% PROOF	p95 PUT < 400ms, PROOF < 500ms
P2P-propagate	5 nodes, 20 tx/s/node	tx + headers gossip	p95 propagation < 1s, dup ratio < 1.2x
Capabilities/AICF	10–50 jobs/s	70% AI, 30% Quantum	SLA pass ≥ 95%, resolver E2E delay p95 < 2 blocks
Randomness-round	sustained	commits/reveals + VDF proofs	finalize each round on time; verify p95 < target seconds


⸻

8) Interpreting Results
	•	Latency histograms: p50/90/99; watch for long tails (GC, DB fsync).
	•	Throughput: plateau vs offered load → capacity. If error spikes first, you’re saturating earlier (rate-limit or DoS guards).
	•	Back-pressure: mempool watermark rising? DA queue length? WS backlogs?
	•	Fairness: per-sender caps and RBF thresholds should prevent starvation.

⸻

9) Artifacts

Store raw metrics and reports under artifacts/ with timestamped dirs:

artifacts/
  2025-10-02T18-00-00Z/
    k6-summary.json
    vegeta-hdrplot.html
    prom-snapshot.tgz
    notes.md

Include:
	•	exact git commit (git describe --tags --dirty --always)
	•	hardware profile (CPU, memory, disk)
	•	command lines & env vars
	•	charts for before/after tuning

⸻

10) Troubleshooting
	•	High submit errors → check mempool/validate logs; chainId/gas limits; rate-limit middleware.
	•	Receipts slow → miner not keeping up; difficulty too high; block gas too low.
	•	DA proofs failing → NMT params mismatch; namespace out of range; erasure k,n mis-configured.
	•	WS drops → CORS/origin policy; per-IP token bucket limits; reverse proxy timeouts.
	•	P2P stalls → NAT/firewall; QUIC disabled; flow control windows exhausted.

⸻

11) Next Steps

When you add concrete drivers:
	•	Put them in tests/load/ (e.g., k6_rpc_submit.js, locustfile.py).
	•	Document knobs at the top of each file.
	•	Wire results into tests/bench/runner.py style JSON to compare runs across PRs.

Happy breaking (the right way)!
