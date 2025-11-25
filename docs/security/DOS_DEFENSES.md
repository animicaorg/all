# DoS Defenses
_Limits, scoring, bans, and message caps across Animica services (P2P, RPC/WS, mempool, DA, ZK, AICF, randomness, website)._

This document is the operational companion to the specs. It enumerates guardrails, default policies, and tuning guidelines that keep the network responsive under hostile load without harming honest throughput.

---

## 1) Principles

- **Validate before decode/allocate:** check length prefixes, domains, and envelope headers before parsing or allocating large buffers.
- **Cheapest checks first:** signature/policy prechecks, chainId, sizes, and schema shape; defer expensive crypto until late.
- **Token buckets everywhere:** per-*{peer, topic, IP, API-key, route}* buckets + a global bucket. Burst allowed, sustained excess throttled.
- **Small, composable caps:** per-message, per-connection, per-interval, per-queue. Avoid a single “big switch.”
- **Backpressure not meltdown:** bounded queues, timeouts, and slow-consumer drop.
- **Dedupe:** never process the same content twice in hot paths (hash/Bloom/inventory tables).
- **Fail closed:** oversize/unknown types → immediately drop with telemetry.

---

## 2) P2P (Gossip, Sync, Transports)

### 2.1 Admission / Handshake
- PQ handshake (Kyber) + AEAD; refuse plaintext.  
- Version, chainId, policy-root, head-height sanity gates.
- **Peer slots:** cap inbound/outbound peers; reserve a fraction for “new” peers to avoid lock-in.

### 2.2 Topic-level Rate Limits
Each topic (headers, blocks, txs, shares, blobs/DA) carries:
- **Per-peer token bucket:** e.g., 50 msgs/5s burst, refill 10 msgs/s.
- **Message caps:** max bytes per message (post-parse) and pre-parse length guards.
- **Inflight limits:** concurrent requests per peer (e.g., 4 blocks, 32 headers).
- **Dedupe:** rolling Bloom over recent hashes/inventory IDs.

### 2.3 Peer Scoring & Bans
- **Score inputs:** valid relays, timely responses, RTT, misbehavior (invalid/malformed, spam, duplicates).
- **Decay:** scores slowly return to neutral; misbehavior subtracts with exponential backoff.
- **Bans:**  
  - *Soft ban* (greylist): deprioritize and reduce quotas for N minutes.  
  - *Hard ban:* disconnect + suppress redial for T minutes/hours.  
- **Eclipse resistance:** enforce per-/24 (or ASN) peer caps; random fanout and mesh graft/prune.

### 2.4 Framing & Compression Safety
- Length-prefix sanity, **compression ratio caps** (drop if compressed→decompressed > threshold).
- **Checksum-before-decompress** when supported.
- **Max frame** size (transport-level) independent from topic message limits.

---

## 3) JSON-RPC & WebSockets

### 3.1 HTTP JSON-RPC
- **Per-IP & per-API-key token buckets** (requests/s and bytes/s).
- **Body size caps** (e.g., 1–4 MiB), **batch size caps** (≤ N calls/batch), **method allowlist**.
- **Structured errors**; no stack traces; constant-time-ish error paths for common rejections.
- **Timeouts:** read header (2–5s), full body (10–20s), handler budget per method.
- **CORS allowlist** (origins), **User-Agent** sanity and **rate tiers**.

### 3.2 WebSockets
- **Max subscriptions/client** (e.g., newHeads, pendingTxs ≤ 4 each).
- **Outbound rate cap** per client (events/s and bytes/s).
- **Heartbeat:** ping/pong with deadline (drop slow clients).
- **Backpressure:** bounded send queue; if full → drop oldest or disconnect.

---

## 4) Mempool

- **Stateless checks first:** chainId, tx size, CBOR canonical order, intrinsic gas, fast PQ-sig precheck.
- **Economic floor:** dynamic **min-fee watermark** (EMA of recent blocks) + **surge multiplier** on congestion.
- **Replacement policy (RBF):** same sender+nonce requires ≥ X% higher effective fee, otherwise reject.
- **Per-sender queues:** cap ready/held counts; prevent single-sender dominance.
- **Global memory cap:** by tx count and bytes; **evict** lowest priority under pressure.
- **Ingress throttles:** per-IP/per-peer token buckets (tx/s and bytes/s).
- **TTL & reorg re-inject:** drop stale pending after T; safe re-inject on reorg within window.

---

## 5) Data Availability (DA Retrieval & Proofs)

- **Blob post/get caps:** max blob size, per-IP bytes/s, concurrent GET/PROOF per client.
- **NMT proof verification budget:** per-request CPU/time limit; early reject malformed ranges.
- **Cache hot paths:** LRU for recent commitments & proofs; avoid recomputation storms.
- **Auth tiers (optional):** API keys with per-tenant quotas; anonymous tier more restrictive.

---

## 6) ZK Verifiers

- **Size limits:** cap proof/VK sizes; schema validation (msgspec/JSON-schema).
- **Curve/KZG guards:** subgroup checks, infinity checks, pairing/KZG fast-fail.
- **CPU/time budgets** per scheme; circuit allowlist via registry; VK pinned by hash.
- **Queueing:** bounded worker pool; drop or 429 when saturated (caller retries).

---

## 7) Randomness (Commit–Reveal → VDF)

- **Commit caps:** per-address per-round commit limit; payload length bounds.
- **Reveal window enforcement:** early/late rejects; no side-channel timing differences.
- **Aggregation anti-bias:** fixed combiner; **rate-limit reveals** per IP/key.
- **VDF verify pool:** bounded concurrency; proof size caps.

---

## 8) AICF (Compute Queue)

- **Job request caps:** input size, model/circuit ID allowlist, per-caller concurrent limit.
- **Queue quotas:** per-provider concurrent leases; fair-share matching; retries with backoff.
- **Result ingestion caps:** bytes/s and proofs-per-interval; schema guards.
- **Abuse response:** lease revocation, provider cooldown, stake penalties (per policy).

---

## 9) Website/Studio Services/Explorer

- **Edge filters:** WAF/CDN limits on requests/s and body size; IP reputation optional.
- **Strict CORS** & **no server-side signing**; signed artifacts required for deploy/verify flows.
- **CSP/HSTS/COOP/COEP** headers; MDX sanitization; SSRF-safe fetchers with allowlists.

---

## 10) Default Caps (Illustrative)

> Tune per deployment; values below are safe starting points for dev/testnets.

| Layer           | Cap / Limit                                   | Example Default |
|-----------------|-----------------------------------------------|-----------------|
| P2P per-peer    | headers msgs / 5s (burst/refill)              | 50 burst / 10 s⁻¹ |
| P2P inflight    | concurrent block requests                      | 4               |
| RPC body        | max JSON body size                             | 2 MiB           |
| RPC batch       | max calls/batch                                | 50              |
| WS client       | max subscriptions                              | 8               |
| Mempool tx      | max tx size                                    | 128 KiB         |
| Mempool per-sender | max queued (ready+held)                     | 64              |
| DA get/proof    | concurrent per IP                              | 4               |
| ZK verify       | worker threads                                 | min(cores, 4)   |
| Randomness      | commits per addr/round                         | 2               |
| AICF            | jobs per caller (concurrent)                   | 4               |

---

## 11) Token Bucket Pattern (Pseudo)

```python
class Bucket:
    def __init__(self, rate_per_s: float, burst: int):
        self.rate = rate_per_s
        self.burst = burst
        self.tokens = burst
        self.t_last = now()

    def allow(self, cost: int = 1) -> bool:
        t = now()
        self.tokens = min(self.burst, self.tokens + (t - self.t_last) * self.rate)
        self.t_last = t
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False

Attach buckets to: (peer, topic), (IP, route), (API-key, route), plus a global bucket.

⸻

12) Greylist & Ban Lifecycle
	1.	Warn: log + score penalty; reduce fanout.
	2.	Greylist: halve quotas for 5–15 min; keep connection alive if not abusive.
	3.	Hard ban: disconnect; suppress redial for 30–120 min.
	4.	Escalation: repeated offenses extend ban with exponential backoff.
	5.	Rehabilitation: positive behavior decays penalties over hours.

All actions are telemetrized with reason codes: OVERSIZE, MALFORMED, DUP, RATE, POLICY, SIGFAIL.

⸻

13) Backpressure & Bounded Queues
	•	Bounded channel per topic/route; if full → drop oldest low-priority or refuse new work (429/flow-control).
	•	Prioritize control traffic (HELLO/PING/headers) ahead of bulk (blocks/blobs).

⸻

14) Observability
	•	Counters: admits, rejects (by reason), bans, queue drops, bytes in/out.
	•	Histograms: handler latency, decode time, verify time, WS queue depth.
	•	High-cardinality labels kept minimal (method/topic/reason only).
	•	Alerts: sustained 429/ban rate, queue saturation, WS drops, head stall.

⸻

15) Config & Tuning
	•	All caps are configurable (env/flags).
	•	Ship safe defaults for dev/testnet; mainnet ops maintain tuned profiles.
	•	Feature flags to temporarily reduce surfaces (disable costly routes, lower fanout).
	•	Rollouts via staged canaries; record impact on latency/head growth.

⸻

16) Abuse Playbooks
	•	RPC flood: tighten per-IP buckets, enable CDN/WAF rate limit, raise min-fee watermark if tx storm.
	•	Gossip storm: lower gossip fanout, raise dedupe strictness, greylist high-dup peers.
	•	ZK verify surge: clamp verifier workers, return 429 with Retry-After.
	•	DA retrieval spikes: throttle proofs, prefer cached commitments, require API keys for large blobs.
	•	WS event flood: enforce client caps, drop slow consumers early.

⸻

17) Residual Risks
	•	Sophisticated Sybil/eclipse can still degrade local views; multi-homing and seed diversity help.
	•	DoS vs liveness trade-offs: overly strict caps can reduce propagation; monitor and tune.
	•	CDN/WAF false positives: whitelist critical infra IPs and health checks.

⸻

Keep this document versioned with config changes. When a DoS incident occurs, record deltas and the effective mitigations used.
