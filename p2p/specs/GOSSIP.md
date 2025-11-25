# Animica Gossip (v1)

This document specifies gossip topics, validation stages, peer scoring, and DoS controls for the Animica P2P layer. It matches the intent and defaults of:

- `p2p/gossip/topics.py` (canonical topic names/IDs)
- `p2p/gossip/validator.py` (fast stateless checks)
- `p2p/gossip/engine.py` (mesh & fanout)
- `p2p/peer/ratelimit.py` (token buckets)
- `p2p/metrics.py` (counters/histograms)
- `p2p/adapters/*_view.py` (cheap consensus/proof views)

The handshake/AEAD layer is defined in **HANDSHAKE.md**. This document covers **post-handshake** pub/sub only.

---

## 1) Topics

All topics are CBOR-encoded payloads framed by the wire envelope (see PROTOCOL.md). Topic strings are ASCII and stable; IDs are derived internally.

| Topic string                 | Purpose                                   | Payload (wire type)                         | Max size | Priority | Notes |
|-----------------------------|-------------------------------------------|---------------------------------------------|---------:|---------:|------|
| `animica/1/tx`              | Transaction relay                          | `TxInv` or `Tx` (compact first, full on IWANT) | 128 KiB | High     | Dedup by txHash; PQ-sig **precheck** only |
| `animica/1/share`           | Useful-work share relay                    | `ShareAnnounce` or `ShareBody`              | 256 KiB | High     | HashShare and compact AI/Quantum refs |
| `animica/1/header`          | Header announcements                       | `HeaderCompact`                             | 32 KiB  | High     | Light consensus checks (Θ, roots) |
| `animica/1/block`           | Compact block announce + fetch-on-demand   | `BlockAnnounce`                             | 256 KiB | Normal   | Use GETDATA to fetch full block |
| `animica/1/blob`            | DA blobs                                  | `BlobAnnounce` / `BlobChunk`                | 2 MiB   | Low      | Optional; NMT commitments verified lazily |
| `animica/1/ctrl`            | Control (IHAVE/IWANT/GRAFT/PRUNE)          | `GossipCtrl`                                | 32 KiB  | Highest  | Mesh maintenance only |

> Implementations SHOULD advertise only topics they validate. A node MAY subscribe to a subset (e.g., full node skips `blob`).

---

## 2) Validation pipeline (per message)

Validators are **layered**; each stage may **drop** or **downgrade** (score penalty & soft drop) before decoding expensive objects.

1. **Framing & size gate**
   - If `payload_len > topic.max_size` → *hard drop*.
   - If compressed, enforce post-decompress cap.
   - Reject non-canonical CBOR (map key order, duplicate keys).

2. **Envelope sanity**
   - Check `msg_id` matches topic kind.
   - Check schema version (reject future major).
   - Check optional checksum (if present) against payload bytes.

3. **Dedup & replay**
   - Compute stable `content_id`:
     - `tx`: `sha3_256(canonical_tx_bytes)`
     - `share`: nullifier or shareHash (see proofs/nullifiers.py)
     - `header/block`: header hash
     - `blob`: commitment root
   - If `content_id` in seen-set bloom → *soft drop*.
   - Maintain a **10-minute** or **100k-entry** sliding window (whichever hits first).

4. **Cheap stateless checks (topic-specific)**

   **tx**
   - Decode `Tx` envelope; ensure chainId matches local.
   - Gas table bounds (<= configured max); access list length cap.
   - PQ signature **precheck**: alg id allowed by local policy; signature length matches algorithm.
   - From/To address well-formed (bech32m `anim1...`).

   **share**
   - Parse ProofEnvelope head (type id, body length).
   - HashShare: recompute target-ratio cheaply against **announced Θ** (from `consensus_view`); header-binding check.
   - AI/Quantum: parse reference, ensure attached metrics header present and not expired; defer heavy attestations to async queue.
   - Enforce per-type **cap preview** using `consensus_view` (don’t forward if already over local preview cap for current window).

   **header**
   - Decode header; check chainId, policy roots (alg-policy root length), Θ schedule bounds, parent ref format.
   - Reject headers with timestamps outside `[-60s, +120s]` skew.

   **block**
   - Compact announce must include header hash + tx/receipt counts.
   - Require known parent (or queued for fetch) before forwarding.

   **blob**
   - Check NMT leaf sizes and declared rows/cols caps.
   - Validate commitment length; defer full availability to DA subsystem.

5. **Expensive/async validators**
   - PQ sig full verify for txs is **async**; forward speculatively if precheck passed and per-peer error rate low.
   - Proofs (AI/Quantum/Storage/VDF) heavy checks are **sampled** (e.g., 1 in 16) unless peer misbehaves, then raised to 1 in 2.
   - Headers may be queued for full consensus validation.

6. **Gossip admit / forward**
   - If all cheap checks pass and buckets allow → forward to mesh.
   - Otherwise *soft drop* (no forward), record reason.

All validation outcomes (accept, dup, soft drop, hard drop, error) increment labeled Prometheus counters.

---

## 3) Peer scoring

A scalar **peer score** accumulates with exponential decay (half-life **10 min**). The score blends delivery quality, invalid rate, spam, cooperation, and responsiveness.

Let:
- `D_t`: timely first-delivery successes (normalized)
- `I_t`: invalid messages (failed **cheap** validation)
- `F_t`: forwarded volume beyond quotas (spam)
- `R_t`: request/response health (IHAVE/IWANT, missing responses)
- `Q_t`: quality for *sampled* heavy verifications (tx sigs, proofs)
- `B_t`: behavior (pings, mesh participation, no PRUNE abuses)

**Score update (every Δ=30s bucket):**

score ← λ * score + ( wDD_t - wII_t - wFF_t + wRR_t + wQQ_t + wBB_t )
λ = exp(-Δ / τ), τ = 10 min / ln 2      # half-life 10 min

**Default weights (normative defaults, tunable in config):**
- `wD = +1.0`
- `wI = 20.0`       # strong penalty for invalids at cheap stage
- `wF = 0.5`        # spam penalty proportional to excess
- `wR = +0.5`       # rewards answering IWANT, PING, ID
- `wQ = +1.0`       # verified-good samples
- `wB = +0.2`

**Thresholds:**
- `score < -50`  → **Greylist** (reduced fanout; no control messages honored)
- `score < -200` → **Quarantine** (do not forward to this peer; still accept inbound for debug)
- `score < -500` → **Ban 1h** (disconnect & deny dials; exponential backoff on repeats)

Per-topic sub-scores contribute to the global score with small caps to avoid gaming via one clean topic.

---

## 4) DoS & resource limits

### 4.1 Token buckets (per-peer, per-topic, plus global)
Buckets refill every second with burst caps. Exceeding **bytes** or **msg** limits triggers soft drops and `F_t` spam penalties.

**Defaults (normative):**

- **Global (all topics combined):**
  - `burst = 8 MiB`, `rate = 2 MiB/s`, `max_msgs = 800 / 10s`

- **tx**
  - `burst = 512 KiB`, `rate = 128 KiB/s`, `max_msgs = 64 / 5s`, `max_msg_size = 128 KiB`

- **share**
  - `burst = 1.5 MiB`, `rate = 512 KiB/s`, `max_msgs = 128 / 5s`, `max_msg_size = 256 KiB`

- **header**
  - `burst = 256 KiB`, `rate = 64 KiB/s`, `max_msgs = 64 / 10s`, `max_msg_size = 32 KiB`

- **block**
  - `burst = 512 KiB`, `rate = 128 KiB/s`, `max_msgs = 8 / 10s`, `max_msg_size = 256 KiB`

- **blob** (optional)
  - `burst = 8 MiB`, `rate = 2 MiB/s`, `max_msgs = 8 / 10s`, `max_msg_size = 2 MiB`

Also enforce **per-IP** aggregates when multiple peers share the same address.

### 4.2 Dedupe windows
- **Seen-set bloom** (2 hashes): target FP ≤ 1e-4, window of **10 min** or **100k items**, whichever first.
- **INV suppression**: if we recently forwarded/asked for an item, suppress duplicates for **30 s**.

### 4.3 Backpressure & mesh hygiene
- If bucket pressure persists > 10 s, **PRUNE** low-scoring peers from mesh and keep as fanout-only.
- **GRAFT** limited to peers with score ≥ 0 and recent useful deliveries.

### 4.4 Validation CPU budget
- Cap **expensive** checks to ≤ 30% of one core per peer (moving average).
- Raise sampling rate for heavy checks on misbehaving peers; lower on good peers.

---

## 5) Per-topic validation details

### 5.1 Transactions (`animica/1/tx`)
- **Admit** if PQ-sig **precheck** passes, gas/size within limits, and account fields valid.
- **Forward speculatively** while full signature verify runs in background.
- **Reject** if chainId mismatch, malformed bech32, or exceeds gas table caps.
- **Dedupe** by `txHash` (canonical CBOR bytes).

### 5.2 Shares (`animica/1/share`)
- **HashShare**: recompute header-binding and target ratio using `consensus_view.Θ`. If ratio < micro-threshold → reject.
- **AI/Quantum**: ensure envelope structure valid; nullifier computed; policy roots not expired; sample heavy attestation verification via `proofs_view`.
- Respect **cap preview**: if forwarding would exceed local per-type caps for the current scheduling window, *soft drop*.

### 5.3 Headers (`animica/1/header`)
- Stateless checks: `chainId`, Θ schedule plausibility, policy-root lengths, timestamp skew.
- Parent existence **hint**: if unknown parent and peer is low-score → delay forward until GETHEADERS yields linkage.

### 5.4 Blocks (`animica/1/block`)
- Announce contains header hash + counts + short txids. If parent unknown → **IWANT** headers first.
- Fetch policy: parallel **GETDATA** from 3 highest-score announcers; cancel slow responders.

### 5.5 Blobs (`animica/1/blob`)
- Verify NMT commitment length and bounds; postpone DA sampling to blob subsystem.
- Trickle-forward to avoid bandwidth spikes.

---

## 6) Control plane & mesh (GossipSub-like)

- Nodes maintain a mesh per topic with target degree `D = 6` (min 4, max 12).
- **IHAVE/IWANT**: announce hashes then request selectively (prefer high-score peers).
- **GRAFT/PRUNE**: join/leave meshes; include backoff timers to prevent oscillation.
- **Adaptive fanout**: for peers outside mesh, maintain **fanout sets** refreshed every 30 s.

Mesh parameters:
- Heartbeat: 1 s
- Opportunistic grafting: every 10 s if median peer score in mesh < 0
- Backoff after PRUNE: 60 s (per-peer, per-topic)

---

## 7) Reconfiguration surface

Most constants are configurable via `p2p/config.py`:
- Topic enable/disable
- Bucket sizes/rates
- Score weights/thresholds
- Validation sampling rates
- Mesh degree and heartbeats

Nodes SHOULD persist chosen parameters for reproducibility in tests.

---

## 8) Telemetry

Required metrics (labels: topic, peer, outcome):
- `gossip_messages_total{outcome}`: accept/dup/soft_drop/hard_drop/error
- `gossip_bytes_total{dir}`: in/out bytes
- `gossip_validation_seconds{stage}`: histogram per stage
- `peer_score_gauge{peer}`: instantaneous score
- `bucket_tokens{topic,peer}`: remaining tokens
- `mesh_degree{topic}`: current degree

---

## 9) Security notes

- Gossip runs **after** AEAD handshake; frames are encrypted/authenticated.
- PQ identity signatures (Dilithium3/SPHINCS+) on the handshake transcript prevent MITM controlling gossip.
- Dedup windows and token buckets are **mandatory** for safety.
- Prefer **pull** (IHAVE/IWANT) over unconditional **push** for large items.

---

## 10) Interop checklist

1. Use canonical topic strings from `p2p/gossip/topics.py`.
2. Enforce **max_msg_size** per topic before decode.
3. Maintain seen-set bloom and INV suppression.
4. Implement **cheap stateless** validators exactly as above.
5. Sample heavy validators and adjust by peer score.
6. Apply token-bucket limits and penalties.
7. Respect mesh degrees and control messages.

*End of spec.*
