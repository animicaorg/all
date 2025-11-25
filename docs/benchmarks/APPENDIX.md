# Benchmarks — Appendix: Raw Numbers & Run Hashes

This appendix defines the **artifact layout**, **raw metrics formats**, and the **hashing procedure** used to make benchmark runs reproducible and tamper-evident. It complements:
- `docs/benchmarks/SCENARIOS.md` (scenario definitions)
- `zk/bench/verify_speed.py` (per-scheme zk verifier micro-bench)

---

## 1) Artifact layout (per run)

Each run writes a directory like:

bench/outputs/YYYY-MM-DD//
HEADER.json              # immutable header & environment
SCENARIO.yaml            # the exact scenario used (copied)
SERIES.jsonl             # time-series samples (one JSON per line)
BLOCKS.jsonl             # per-block aggregates (one JSON per line)
POWER.csv                # optional power samples (timestamp,W_cpu,W_gpu,…)
SUMMARY.json             # computed rollups (tps, latency, errors, energy)

> Filenames are stable and case-sensitive. CI jobs should treat any missing optional file (e.g., POWER.csv) as "not recorded", not a failure.

---

## 2) HEADER.json schema (canonical, sorted keys)

**Example:**
```json
{
  "animica_bench_version": "1",
  "commit": "3c5e4c0",
  "repo_dirty": false,
  "tag": "v0.8.2",
  "run_started_at": "2025-03-18T19:22:04Z",
  "host": {
    "id": "HP3", 
    "uname": "Linux 6.8.5 x86_64",
    "cpu": "AMD Ryzen 9 7950X",
    "cpu_count": 32,
    "ram_gb": 64,
    "gpu": "NVIDIA RTX 4090",
    "gpu_driver": "550.54"
  },
  "node": {
    "theta_mode": "fixed:2.6s",
    "gas_limit": 12000000,
    "da_enabled": true,
    "zk_enabled": true
  },
  "miner": {
    "device": "cpu",
    "threads": 16
  },
  "p2p": { "peers": 1, "net": { "rtt_ms": 1, "jitter_ms": 0, "loss": 0.0 } },
  "scenario": {
    "id": "SYN-03-DA-HEAVY",
    "label": "DA-heavy — 128 KiB avg blobs @ 0.2 mix",
    "duration_s": 600,
    "warmup_s": 120,
    "seed": 42
  },
  "versions": {
    "python": "3.11.9",
    "rustc": "1.79.0",
    "animica_core": "0.8.2",
    "animica_consensus": "0.8.2",
    "animica_da": "0.8.2",
    "animica_zk_native": "0.2.0"
  },
  "locks": {
    "spec/opcodes_vm_py.yaml.sha3_256": "e8a1…",
    "vm_py/gas_table.json.sha3_256": "a41c…",
    "zk/registry/vk_cache.json.sha3_256": "9f22…",
    "docs/rpc/OPENRPC.json.sha3_256": "0be7…"
  }
}

	•	All keys are sorted for canonical serialization (jq -S .), which is required for hashing below.
	•	host.id (HPx) is a short label you maintain (e.g., HP1=“M2 Pro 32GB”, HP2=“EPYC 64c”, …).

⸻

3) Raw series formats

3.1 SERIES.jsonl (time-series; 1 JSON per line)

Each line has a kind field and a ts (RFC3339). All objects share a stable minimal envelope.

Kinds & payloads:
	•	"kind":"mempool"

{"ts":"2025-03-18T19:22:10.012Z","kind":"mempool","admit_per_s":212.3,"reject_per_s":3.4,"evict_per_s":0.0,"min_fee":1200}


	•	"kind":"tx_lat" (closed-loop or sampled E2E latencies)

{"ts":"2025-03-18T19:22:10.512Z","kind":"tx_lat","p50_ms":312,"p90_ms":640,"p99_ms":1182,"max_ms":2301,"inflight":800}


	•	"kind":"da" (blob traffic)

{"ts":"2025-03-18T19:22:11.004Z","kind":"da","posts":36,"bytes":4718592,"proof_fail":0}


	•	"kind":"zk" (verifier activity)

{"ts":"2025-03-18T19:22:11.007Z","kind":"zk","scheme":"groth16","verifies":22,"avg_ms":4.9,"p99_ms":7.8,"fail":0}


	•	"kind":"power" (optional; if recorded externally)

{"ts":"2025-03-18T19:22:11.100Z","kind":"power","cpu_w":142.3,"gpu_w":0.0,"pkg_w":158.7}



3.2 BLOCKS.jsonl (per-block aggregates)

{"height":18421,"hash":"0x52…b6","ts":"2025-03-18T19:22:11.606Z","interval_ms":2579,"txs":612,"gas_used":7384021,"da_bytes":8388608,"zk_count":40}
{"height":18422,"hash":"0x10…44","ts":"2025-03-18T19:22:14.187Z","interval_ms":2581,"txs":603,"gas_used":7299920,"da_bytes":6553600,"zk_count":37}

Invariants: sum of txs over blocks within the measured window should match SUMMARY.json.txs_total (± dropped tail/warmup trimming).

⸻

4) SUMMARY.json (rollups, single object)

Example:

{
  "tps_mean": 232.8,
  "tps_p95_block": 258.4,
  "lat_ms": { "p50": 328, "p90": 662, "p99": 1240 },
  "blocks": 228,
  "block_time_ms_avg": 2591,
  "txs_total": 139521,
  "reject_rate_pct": 1.3,
  "da": { "posts": 1821, "bytes": 241172480 },
  "zk": { "scheme_mix": {"groth16": 0.6, "plonk": 0.4}, "verifies": 9120, "fail": 0 },
  "energy": { "joules_per_block": 820.5, "joules_per_tx": 3.9 },
  "notes": "Warmup=120s excluded; open-loop λ=180/s."
}


⸻

5) Run hashing (tamper-evident)

We compute three hashes:
	1.	input_hash — over HEADER.json and SCENARIO.yaml
	2.	series_hash — over SERIES.jsonl and BLOCKS.jsonl
	3.	run_hash — final binder of (1) and (2)

5.1 Definitions
	•	Canonicalize JSON with sorted keys and LF newlines.
	•	Use SHA3-256 (hex).
	•	Concatenate raw bytes with \n separators (no extra newline at end).

5.2 Bash (jq + sha3sum)

# Requires: jq, sha3sum (or python - <<PY ...)

canon_header=$(jq -S -c . bench/outputs/.../HEADER.json)
canon_scenario=$(awk '{printf "%s\n",$0}' bench/outputs/.../SCENARIO.yaml)

input_hash=$( { printf "%s\n" "$canon_header"; printf "%s" "$canon_scenario"; } | sha3sum -a 256 | awk '{print $1}' )

series_hash=$(
  { awk '{printf "%s\n",$0}' bench/outputs/.../SERIES.jsonl
    awk '{printf "%s\n",$0}' bench/outputs/.../BLOCKS.jsonl; } \
  | sha3sum -a 256 | awk '{print $1}'
)

run_hash=$( printf "animica-bench-v1|%s|%s" "$input_hash" "$series_hash" | sha3sum -a 256 | awk '{print $1}' )

echo "input_hash=$input_hash"
echo "series_hash=$series_hash"
echo "run_hash=$run_hash"

5.3 Python (pure stdlib)

import json, hashlib, pathlib

def sha3(data: bytes) -> str:
    return hashlib.sha3_256(data).hexdigest()

p = pathlib.Path("bench/outputs/YYYY-MM-DD/SCENARIO-ID")

canon_header = json.dumps(json.loads(p.joinpath("HEADER.json").read_text()), sort_keys=True, separators=(",",":")).encode()
canon_scenario = p.joinpath("SCENARIO.yaml").read_bytes()

input_hash = sha3(canon_header + b"\n" + canon_scenario)

series = p.joinpath("SERIES.jsonl").read_bytes()
blocks = p.joinpath("BLOCKS.jsonl").read_bytes()
series_hash = sha3(series + b"\n" + blocks)

run_hash = sha3(b"animica-bench-v1|" + input_hash.encode() + b"|" + series_hash.encode())

print({"input_hash": input_hash, "series_hash": series_hash, "run_hash": run_hash})

Store run_hash in SUMMARY.json.run_hash (and print it in CI). Publishing results without the run hash is discouraged.

⸻

6) Sanity checks (CI gates)
	•	Warmup trimming: exclude the first warmup_s from SERIES/BLOCKS when computing rollups.
	•	Mass balance:
Σ BLOCKS.txs (trimmed window) ≈ SUMMARY.txs_total.
Σ SERIES.da.bytes (window) ≥ Σ BLOCKS.da_bytes (blocks may drop/pack differently).
	•	Bounds: block_time_ms_avg within ±10% of Θ when theta_mode=fixed:*.
	•	Power: if POWER.csv exists and is non-empty, require energy.joules_per_block > 0.

⸻

7) Example raw snippets

SERIES.jsonl (first 5 lines after warmup):

{"ts":"2025-03-18T19:24:04.010Z","kind":"mempool","admit_per_s":214.8,"reject_per_s":2.9,"evict_per_s":0.0,"min_fee":1200}
{"ts":"2025-03-18T19:24:04.512Z","kind":"tx_lat","p50_ms":318,"p90_ms":651,"p99_ms":1190,"max_ms":2102,"inflight":780}
{"ts":"2025-03-18T19:24:05.010Z","kind":"da","posts":34,"bytes":4456448,"proof_fail":0}
{"ts":"2025-03-18T19:24:05.011Z","kind":"zk","scheme":"groth16","verifies":21,"avg_ms":5.1,"p99_ms":7.9,"fail":0}
{"ts":"2025-03-18T19:24:05.112Z","kind":"power","cpu_w":148.2,"gpu_w":0.0,"pkg_w":163.3}

BLOCKS.jsonl (sample):

{"height":18431,"hash":"0xe3…91","ts":"2025-03-18T19:24:11.612Z","interval_ms":2590,"txs":607,"gas_used":7311021,"da_bytes":7340032,"zk_count":39}


⸻

8) Quick queries (jq)
	•	Mean TPS over trimmed window:

jq -sr '
  [ inputs | select(.kind=="mempool") | .admit_per_s ] | add / length
' bench/outputs/.../SERIES.jsonl


	•	p99 latency (approx from time-series p99):

jq -sr '
  [ inputs | select(.kind=="tx_lat") | .p99_ms ] | sort | .[length*99/100|floor]
' bench/outputs/.../SERIES.jsonl


	•	Total DA bytes:

jq -sr '[ inputs | select(.kind=="da") | .bytes ] | add' bench/outputs/.../SERIES.jsonl



⸻

9) Publishing & provenance

When publishing results, include:
	•	SUMMARY.json (with run_hash)
	•	HEADER.json (canonical)
	•	SCENARIO.yaml (exact file used)
	•	series_hash and input_hash (optional but recommended)
	•	Hardware profile (HPx → spec), OS/kernel, and power method if used

A minimal provenance block (YAML) used in release notes:

bench:
  scenario: SYN-03-DA-HEAVY
  commit: 3c5e4c0
  host_id: HP3
  run_hash: "b7a2a6…f3e9"
  input_hash: "1c9d1e…c54a"
  series_hash: "f9a51b…0a22"
  started_at: "2025-03-18T19:22:04Z"
  duration_s: 600
  warmup_s: 120


⸻

10) Notes on power measurement (optional)
	•	Prefer external shunt/loggers when possible; if not, use platform counters consistently:
	•	Linux: intel_rapl / nvidia-smi --query-gpu=power.draw.
	•	macOS (Apple Silicon): powermetrics (requires sudo; sampling at ≥1 Hz).
	•	Export a uniform CSV:

ts,cpu_w,gpu_w,pkg_w
2025-03-18T19:24:05.112Z,148.2,0.0,163.3


	•	CI should tolerate absent power logs but require consistency across comparisons.

⸻

TL;DR
	•	Keep raw SERIES/BLOCKS, compute run_hash as specified, and publish the trio (HEADER, SCENARIO, SUMMARY).
	•	Any consumer can re-hash and verify your numbers end-to-end.

