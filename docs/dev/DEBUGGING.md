# Debugging — Logs, Metrics, pprof-like Profiling, and Tracing

This guide shows how to observe and debug the Animica stack in production and during development:
- **Structured logs** with consistent fields and redaction
- **Metrics** (Prometheus) for SLOs and capacity
- **Profiling** (CPU/Heap/"pprof-like") for Python & Rust
- **Tracing** (OpenTelemetry) with request/peer IDs and cross-service context
- **Triage playbooks** and common failure signatures

> All examples assume repo root; adjust paths and ports as needed.

---

## 1) Structured Logging

### 1.1 Format & fields
All services log **JSON** (single line per event). Canonical fields:
- `ts` — RFC 3339 nanosecond timestamp (UTC)
- `lvl` — `debug|info|warn|error`
- `mod` — module/logger name (e.g. `rpc.methods.tx`)
- `evt` — event name (e.g. `tx.send`, `p2p.hello`)
- `msg` — short human message (optional if fields suffice)
- `req_id` — request id / correlation id (HTTP or WS)
- `trace_id`, `span_id` — OpenTelemetry IDs (if tracing on)
- `peer`, `ip`, `conn_id` — for P2P/RPC clients
- `height`, `hash`, `txHash`, `peer_id` — domain keys
- `dur_ms`, `bytes`, `gas_used` — numeric measures
- `err`, `exc` — error class & message

**Example**
```json
{"ts":"2025-10-10T14:21:53.214Z","lvl":"info","mod":"rpc.methods.tx","evt":"tx.send",
 "req_id":"6yP2wG","txHash":"0x2f...ab","bytes":372,"dur_ms":11}

Redaction: token-like fields are hashed/prefix-only: addr, auth, api_key → keep first/last 4 chars.

1.2 Enabling JSON logs

Python (uvicorn/FastAPI)

uvicorn rpc.server:app \
  --host 0.0.0.0 --port 8545 \
  --log-level info \
  --use-colors false

Set:

export LOG_FORMAT=json
export LOG_LEVEL=info
export LOG_SAMPLING="rpc.methods.tx:0.1,p2p.gossip:0.1"   # 10% sample

Rust (tauri/native helpers)

RUST_LOG=info,animica=debug ./bin

Use tracing-subscriber JSON layer when TRACING_JSON=1.

1.3 Request IDs & correlation
	•	HTTP: middleware injects req_id (honors X-Request-ID).
	•	WS: attach conn_id + rolling seq.
	•	P2P: peer_id + msg_seq.
	•	Always log {trace_id, span_id} when tracing is on.

⸻

2) Metrics (Prometheus)

2.1 Endpoints

Each service exposes /metrics:
	•	rpc: :8545/metrics
	•	p2p: :8600/metrics
	•	da: :8787/metrics
	•	studio-services: :8080/metrics

Sample

# HELP rpc_requests_total count of JSON-RPC calls
# TYPE rpc_requests_total counter
rpc_requests_total{method="chain.getHead"} 152397

# HELP tx_verify_duration_ms histogram of tx verify time
# TYPE tx_verify_duration_ms histogram
tx_verify_duration_ms_bucket{le="1"} 120 ...

2.2 Key metrics (cheat sheet)
	•	RPC
	•	rpc_requests_total{method}
	•	rpc_errors_total{method,code}
	•	rpc_inflight{method}
	•	rpc_latency_ms_bucket{method,le}
	•	Mempool
	•	mempool_admitted_total, mempool_rejected_total{reason}
	•	mempool_size, mempool_ready
	•	Consensus
	•	blocks_sealed_total, interblock_seconds_bucket
	•	theta_value, accept_rate
	•	P2P
	•	peers_connected, gossip_pub_total{topic}, gossip_drop_total{reason}
	•	DA
	•	da_put_bytes_total, da_get_total, da_proof_verify_ms_bucket
	•	ZK
	•	zk_verify_total{scheme}, zk_verify_ms_bucket{scheme,backend}
	•	AICF
	•	aicf_jobs_enqueued_total{kind}, aicf_jobs_sla_fail_total{reason}

2.3 Local scrape (docker-compose example)

scrape_configs:
  - job_name: 'animica'
    static_configs:
      - targets: ['localhost:8545','localhost:8600','localhost:8787','localhost:8080']

2.4 Grafana SLO panels
	•	RPC success rate: 1 - sum(rate(rpc_errors_total[5m])) / sum(rate(rpc_requests_total[5m]))
	•	P2P peer health: peers_connected
	•	Head growth: increase(blocks_sealed_total[1h])

⸻

3) Profiling (pprof-like)

3.1 Python (CPU/Heap)

Quick CPU flamegraph with py-spy (no restart):

pip install py-spy
py-spy record -o /tmp/profile.svg --pid $(pgrep -f "uvicorn rpc.server") --duration 30
open /tmp/profile.svg

Statistical profiler scalene:

pip install scalene
scalene -o /tmp/scalene.txt -m rpc.server

Built-in cProfile for a route slice:

ANIMICA_PROFILE=1 uvicorn rpc.server:app
# Server logs path to /tmp/cprofile-*.prof
snakeviz /tmp/cprofile-*.prof

Memory (tracemalloc):

import tracemalloc; tracemalloc.start(25)
# Expose /debug/mem to dump top stats

3.2 Rust native (pairing/KZG)

criterion benches

pushd zk/native && cargo bench && popd

pprof-rs HTTP (dev feature)

RUSTFLAGS="-g" RUST_LOG=info PPROF=1 ./target/debug/animica-node
# Visit http://localhost:6060/pprof/profile?seconds=30

Linux perf

perf record -g -- ./target/release/animica-node
perf script | stackcollapse-perf.pl | flamegraph.pl > flame.svg

Tip: Always repro with fixed inputs and no debug logs when profiling.

⸻

4) Tracing (OpenTelemetry)

4.1 Enable OTLP export

export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"
export OTEL_SERVICE_NAME="animica-rpc"
export OTEL_TRACES_SAMPLER="parentbased_traceidratio"
export OTEL_TRACES_SAMPLER_ARG="0.1"   # 10% sample
export TRACE_JSON=1                    # also include trace ids in logs

Start collectors (e.g., OpenTelemetry Collector + Jaeger/Tempo).

4.2 Context propagation
	•	HTTP: W3C traceparent/tracestate respected.
	•	WS: first message carries headers snapshot; conn_id -> child spans.
	•	P2P: HELLO seeds a trace_id for handshake, then message-local spans.
	•	Persist req_id in logs and trace_id in spans; link them.

4.3 Important spans
	•	rpc.tx.send → mempool.add → consensus.seal → db.commit
	•	da.put → nmt.build → erasure.encode → store.write
	•	zk.verify (attribute {scheme, backend}) with child span for native/Python path

⸻

5) On-call triage playbooks

5.1 RPC is slow or erroring
	1.	Check SLO: rpc_latency_ms_bucket, rpc_errors_total.
	2.	If mempool_rejected_total{reason="FeeTooLow"} spikes → fee floor.
	3.	Profile hot path: py-spy 20–60s during load.
	4.	Inspect DB: sqlite3 animica.db '.tables'; check long-running queries.
	5.	Enable debug just for target module:

LOG_LEVEL=info LOG_DEBUG_MODULES="rpc.methods.tx" uvicorn ...



5.2 P2P peer drops / gossip stalls
	1.	peers_connected dip? Check p2p.peer.rate_limit.drops.
	2.	Validate clock skew: headers rejected due to time windows?
	3.	Increase mesh fanout temporarily: P2P_GOSSIP_FANOUT=32.

5.3 DA proof failures
	1.	Look at da_proof_verify_ms_bucket tails.
	2.	Enable da.utils.merkle:debug logs; dump a single failing proof to file.
	3.	Re-verify offline:

python -m da.cli.inspect_root --root 0x... --ns 24



5.4 ZK verify spikes or false negatives
	1.	Compare zk_verify_total{backend="native"} ratio; fallback path engaged?
	2.	Dump a failing envelope and run:

python -m zk.bench.verify_speed --input zk/bench/data/sample_proofs.json



⸻

6) Local debug endpoints (dev only)
	•	/healthz, /readyz — liveness
	•	/version — git describe + module versions
	•	/metrics — Prometheus
	•	/debug/config — sanitized effective config
	•	/debug/pprof/* — when enabled (dev)

Guard with DEBUG_ENDPOINTS=1 and never enable publicly.

⸻

7) DB & state inspection

7.1 SQLite

sqlite3 animica.db \
  'SELECT key, length(value) FROM kv WHERE prefix="state" LIMIT 10;'

7.2 RocksDB (optional backend)

Use ldb:

ldb --db=./rocks --hex dump --count=50

7.3 CBOR helpers

python - <<'PY'
import sys, cbor2, binascii
raw = binascii.unhexlify(sys.argv[1])
print(cbor2.loads(raw))
PY 0xA1...


⸻

8) Useful env vars

Var	Meaning	Default
LOG_FORMAT	`json	text`
LOG_LEVEL	`debug	info
LOG_SAMPLING	modA:rate,modB:rate	none
TRACE_JSON	if set, add trace ids to logs	off
OTEL_EXPORTER_OTLP_ENDPOINT	OTLP HTTP	none
METRICS_BIND	host:port for /metrics	per-service
DEBUG_ENDPOINTS	expose /debug/*	off


⸻

9) Checklists

Before profiling
	•	Turn off verbose logs (LOG_LEVEL=warn) to reduce perturbation.
	•	Fix inputs (replay the same workload).
	•	Warm-up (JITs/caches) 30–60s.

Before filing perf bug
	•	Attach flamegraph (CPU) and top snapshot.
	•	Include versions (/version) and platform.
	•	Include metric deltas for the time window.

⸻

10) Quick scripts

Tail important logs (pretty):

jq -r '[.ts, .lvl, .mod, .evt, (.msg // ""), (.err // "")] | @tsv' < node.log

Find slow RPCs (>250 ms):

jq 'select(.mod=="rpc.middleware" and .dur_ms != null and .dur_ms>250)' < node.log


⸻

11) FAQ

Q: I see high CPU but low RPC traffic?
A: Likely consensus/miner activity or DA erasure coding. Check blocks_sealed_total and da histograms; profile non-RPC processes.

Q: Traces missing even with OTEL set?
A: Collector not reachable or sampler rate too low. Verify with curl $OTEL_EXPORTER_OTLP_ENDPOINT/v1/traces -I.

Q: JSON logs unreadable locally.
A: Pipe through jq or use LOG_FORMAT=text in dev.

⸻

Stay observant, measure first, and optimize last. 🔍
