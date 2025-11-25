# Loki Log Queries — Common Investigations

This cheat-sheet assumes Promtail attaches labels like:
- `namespace` (e.g., `animica-devnet`)
- `app` ∈ {`node`,`miner`,`rpc`,`p2p`,`da`,`aicf`,`randomness`,`explorer`,`services`}
- `pod`, `container`, `instance`, plus JSON fields `level`, `request_id`, `trace_id`, etc.

> Tip: Start broad, then narrow by `namespace`, `app`, and time range. Use **Log browser → Live** for tailing.

---

## 0) Discoverability

**All apps in namespace**
```logql
{namespace="animica-devnet"}

Top talkers (approx)

topk(10, count_over_time({namespace="animica-devnet"}[5m]))

Known label values

label_values({namespace="animica-devnet"}, app)


⸻

1) Errors & Exceptions (Any App)

All ERROR lines

{namespace="animica-devnet"} |= "ERROR"

Structured JSON errors (filter on level)

{namespace="animica-devnet"} | json | level="ERROR"

Stack traces / panics

{namespace="animica-devnet"} |~ "(panic|traceback|exception)"

Error rate by app

sum by (app) (count_over_time({namespace="animica-devnet"} |= "ERROR" [5m]))


⸻

2) Correlation by IDs

Find a specific request/trace across services

{namespace="animica-devnet"} | json | request_id="REQ-abc123"

{namespace="animica-devnet"} | json | trace_id="00000000000000000000000000000000"

Show only message + app (prettify)

{namespace="animica-devnet"} | json | request_id="REQ-abc123" | line_format "{{.app}} :: {{.msg}}"


⸻

3) Node / Consensus

Head stall indicators

{app="node",namespace="animica-devnet"} |~ "(head stall|no new blocks|lagging head)"

PoIES anomalies

{app="node",namespace="animica-devnet"} | json | level!="INFO" |~ "(Theta|Γ|fairness|acceptance|retarget)"

Reorgs (depth & reasons)

{app="node",namespace="animica-devnet"} |~ "reorg" | json | line_format "depth={{.depth}} from={{.from}} to={{.to}}"


⸻

4) RPC / JSON-RPC

5xx or explicit failure logs

{app="rpc",namespace="animica-devnet"} |~ "(500|Internal Server Error|Unhandled)"

Method hot list

sum by (method) (count_over_time({app="rpc",namespace="animica-devnet"} | json | method!="" [5m]))

p95 RPC latency (assuming lat_ms in JSON)

quantile_over_time(
  0.95,
  {app="rpc",namespace="animica-devnet"} | json | method!="healthz" | unwrap lat_ms [5m]
)

Slow requests (>1s) with method & id

{app="rpc",namespace="animica-devnet"} | json | lat_ms > 1000 | line_format "{{.method}} id={{.id}} {{.lat_ms}}ms"

CORS / Rate limit denials

{app="rpc",namespace="animica-devnet"} | json | component="rate_limit"


⸻

5) Mempool

Admission errors (nonce gaps, low fee)

{app="node",namespace="animica-devnet"} |~ "(AdmissionError|NonceGap|FeeTooLow)"

Evictions / watermark raises

{app="node",namespace="animica-devnet"} |~ "(evict|watermark|surge)"

Surge bursts (5m window)

count_over_time({app="node",namespace="animica-devnet"} |~ "surge" [5m])


⸻

6) P2P / Gossip

Handshake failures (Kyber / AEAD)

{app="p2p",namespace="animica-devnet"} |~ "(HandshakeError|AEAD|Kyber|KEM)"

Backpressure / dropped frames

{app="p2p",namespace="animica-devnet"} |~ "(backpressure|dropped|over quota)"

Peer churn (joins/leaves)

sum by (msg) (count_over_time({app="p2p",namespace="animica-devnet"} |~ "(newPeer|peerLost)" [10m]))


⸻

7) Data Availability (DA)

Commit/Proof failures

{app="da",namespace="animica-devnet"} |~ "(InvalidProof|NamespaceRangeError|erasure decode fail)"

DAS p_fail anomalies

{app="da",namespace="animica-devnet"} | json | p_fail > 0.05


⸻

8) AICF (AI/Quantum)

Job timeouts / SLA / slashing

{app="aicf",namespace="animica-devnet"} |~ "(timeout|SLA|SlashEvent)"

Assignment/lease activity

sum by (event) (count_over_time({app="aicf",namespace="animica-devnet"} |~ "(Assigned|LeaseRenew|Completed)" [5m]))


⸻

9) Randomness / Beacon

Commit/Reveal boundary violations

{app="randomness",namespace="animica-devnet"} |~ "(CommitTooLate|RevealTooEarly|BadReveal)"

VDF verify problems

{app="randomness",namespace="animica-devnet"} |~ "(VDFInvalid|verify failed|disc bits)"

Round finalize summary

{app="randomness",namespace="animica-devnet"} | json | event="beacon_finalized" | line_format "round={{.round}} commits={{.commits}} reveals={{.reveals}} vdf={{.vdf_ms}}ms"


⸻

10) Explorer / WS Streams

WebSocket disconnect spikes

sum by (code) (count_over_time({app="explorer",namespace="animica-devnet"} |~ "WS close code" [5m]))

API saturation hints

{app="explorer",namespace="animica-devnet"} |~ "(timeout|queue full|over capacity)"


⸻

11) Miner

Hash loop errors / device backends

{app="miner",namespace="animica-devnet"} |~ "(DeviceUnavailable|kernel|backend|nonce loop)"

Submit rejects

{app="miner",namespace="animica-devnet"} |~ "(SubmitRejected|WorkExpired|stale share)"


⸻

12) Rate-Limited IPs (RPC or DA)

{namespace="animica-devnet"} | json | component="rate_limit" | line_format "{{.remote_ip}} bucket={{.bucket}} reason={{.reason}}"


⸻

13) Deduplication & Noise Control

Show unique error lines (per app)

{namespace="animica-devnet"} |= "ERROR" | dedup

Exclude health checks

{app="rpc",namespace="animica-devnet"} != "healthz"


⸻

14) From Logs → Metrics (ad-hoc)

p99 RPC latency per method

quantile_over_time(
  0.99,
  {app="rpc",namespace="animica-devnet"} | json | unwrap lat_ms [10m]
)

Error ratio (errors / all logs)

sum(count_over_time({namespace="animica-devnet"} |= "ERROR" [5m]))
/
sum(count_over_time({namespace="animica-devnet"}[5m]))


⸻

15) Useful Patterns
	•	Regex: |~ "(foo|bar)" includes; !~ excludes.
	•	JSON: | json to parse, then compare: field > 500, field="X".
	•	Formatting: | line_format "{{.field}} {{.other}}" for quick readable output.
	•	Windowing: Prefer [5m] [10m] for stability; shorten to [1m] when tailing.

⸻

16) Triage Flows

A) “Heads stopped”
	1.	Check node errors:

{app="node",namespace="animica-devnet"} |= "ERROR" or |~ "stall"


	2.	See miner rejects surge:

{app="miner",namespace="animica-devnet"} |~ "SubmitRejected"


	3.	P2P drops/backpressure:

{app="p2p",namespace="animica-devnet"} |~ "(dropped|backpressure)"



B) “High 5xx on RPC”
	1.	Errors:

{app="rpc",namespace="animica-devnet"} |~ "500"


	2.	Latency:

quantile_over_time(0.95, {app="rpc",namespace="animica-devnet"} | json | unwrap lat_ms [5m])


	3.	Rate limits:

{app="rpc",namespace="animica-devnet"} | json | component="rate_limit"



⸻

See also:
	•	ops/runbooks/alerts_reference.md — link errors → queries
	•	ops/runbooks/incident_checklist.md — step-by-step during incidents

