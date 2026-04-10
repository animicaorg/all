# Animica Sync (v1)

This document specifies header, block, transaction-mempool, and useful-work share synchronization flows, including edge cases and DoS safeguards. It corresponds to:

- `p2p/sync/headers.py`, `p2p/sync/blocks.py`, `p2p/sync/mempool.py`, `p2p/sync/shares.py`
- `p2p/protocol/*` message schemas and `p2p/wire/*` framing
- `p2p/peer/*` scoring/ratelimits and `p2p/gossip/*` mesh rules
- `p2p/adapters/*_view.py` for cheap validation during sync

All traffic is AEAD-protected post-handshake (Kyber768 + HKDF-SHA3-256; see **HANDSHAKE.md**).

---

## 0) Glossary & defaults

- **IBD**: initial block download from near-genesis.
- **Fast sync**: header-first sync to the best tip, then backfill blocks.
- **Locator**: an ordered list of header hashes summarizing local knowledge for fork navigation.
- **Tip**: candidate best head according to fork choice (see `consensus/fork_choice.py`).
- **Θ**: acceptance threshold parameter (see consensus). Light checks run during sync.

**Timing defaults (configurable in `p2p/constants.py`):**
- Header request timeout: 3 s (single peer), 8 s (multi-peer hedge).
- Block body request timeout: 5 s per batch.
- Parallel block fetch: up to 3 peers, 16 bodies in-flight/peer.
- INV/GETDATA backoff: 500 ms (adaptive).
- Reorg protection window: 64 blocks (soft), 512 (hard diagnostic).

---

## 1) Header sync

### 1.1 Locator construction
A node computes a locator `L = [h_tip, h_tip-1, h_tip-2, h_tip-4, h_tip-8, ..., h_0]` capped to 32 entries. During IBD when no local state exists, `L = [h_genesis]`.

### 1.2 Request/response

GETHEADERS { locator: [hash], stop: optional_hash, max: N<=2048 }
HEADERS   { headers: [HeaderCompact…] }   # contiguous from the fork point

- Sender must return a contiguous run starting at the nearest common ancestor (NCA) w.r.t. locator.
- `HeaderCompact` contains: parentHash, number, mixSeed/nonce domain hints, Θ, policy roots, timestamps.

### 1.3 Validation (cheap)
For each header (sequential):
1. ChainId matches.
2. Parent link (hash(prev) == parentHash of current).
3. Timestamp within skew `[-60s, +120s]` (configurable).
4. Θ schedule plausibility & monotonicity bounds (no >2x step change per window).
5. Policy-root lengths, alg-policy root version compatible.
6. Optional PoIES *preview* (recompute S bounds from header hints only).

On failure: stop at the last good header; downgrade peer score.

### 1.4 Advancing the tip
- Maintain a DAG of candidate chains keyed by tip hash and height.
- Apply fork choice: **longest/weight-aware** (see `consensus/fork_choice.py`), with deterministic tie-breakers (lowest hash).
- Persist only validated headers to `core/db/block_db.py` with `is_body_present=false`.

### 1.5 Edge cases
- **Stalling peer**: if <16 headers returned and peer has higher advertised height, hedge a parallel GETHEADERS to other peers.
- **Looping responses**: reject sequences that jump backwards or repeat.
- **Deep reorg**: if new branch overtakes by >64 blocks (soft cap), mark as *potential reorg*; require multi-peer corroboration (≥2 peers) before adopting. If >512, require manual override or special flag (diagnostic).

---

## 2) Block body sync

### 2.1 Discovery
Blocks are fetched via either:
- `INV{ type=block, hashes[] }` → `GETDATA{ hashes[] }` → `BLOCKS{ compact/full }`
- Or during header-first fast sync, after reaching a stable tip window, we issue `GETDATA` for missing bodies.

### 2.2 Scheduling & parallelism
- Maintain a **fetch queue** ordered by height then dependency readiness.
- Assign up to **16** outstanding blocks per peer; choose **3** best-scoring announcers for each hash (hedged).
- Cancel slower transfers when the first full, valid body arrives.

### 2.3 Validation pipeline (body)
1. **Decode** compact/full block; verify header hash.
2. **Link**: header must be known and contiguous (parent accepted).
3. **Tx precheck**: canonical CBOR, size/gas caps.
4. **Proof envelopes**: parse & schema-check; compute nullifiers.
5. **PoIES recompute**: run scorer in **preview** mode to ensure plausibility versus header’s S fields (no final acceptance here).
6. **State-free checks**: receipts count match, roots length & format.
7. Persist body bytes and a *verified=false* marker.

> Execution/state transitions are core-layer; sync only ensures structural validity so the node can later execute/import deterministically.

### 2.4 Orphans & gaps
- If parent body missing, keep in **orphan pool** keyed by parentHash with TTL 30 min.
- Periodically retry GETDATA for parents; drop on TTL expiry.

### 2.5 Integrity & disk
- Persist blocks to `core/db/block_db.py`; set `is_body_present=true` only after decode+basic checks pass.
- Write-ahead batching (SQLite/RocksDB) to avoid partial commit on crash.

---

## 3) Mempool (transactions) sync

### 3.1 INV/GETDATA loop
- Peers gossip `INV{type=tx, ids[]}` on new arrivals (see **GOSSIP.md**).
- We request unknown ids with `GETDATA{ids[]}` capped by per-peer token buckets.

### 3.2 Admission checks (cheap)
- ChainId & nonce domain formats.
- Gas and access list bounds.
- PQ-signature **precheck** against local alg-policy (length/alg id only).
- Size ≤ 128 KiB (config default).

If cheap checks pass → add to *pending pool* (RPC’s `pending_pool.py` mirrors rules). Full PQ verify runs async; evict on failure.

### 3.3 Dedupe & TTL
- Dedupe by `txHash` (canonical CBOR).
- TTL default 30 min or until included in a block at height `h >= seen_height + 1`.

### 3.4 Re-announcement
- On reorg, resurrect valid-but-dropped txs from orphaned blocks and re-announce with backoff jitter.

---

## 4) Useful-work **shares** sync

Shares (HashShare / AI / Quantum / Storage / VDF) are relayed to aid miners and observers.

### 4.1 INV/announce
- `INV{type=share, ids[]}` or `ShareAnnounce{ kind, shortId, metricsHint }`
- Consumers use `GETDATA` for bodies if interesting.

### 4.2 Validation (cheap)
- Parse envelope head; compute **nullifier** and drop on reuse.
- HashShare: check header-binding and micro-target ratio vs current Θ (from `adapters/consensus_view.py`).
- AI/Quantum: ensure metrics header present; not expired; trap sample counts nonzero.
- Enforce **cap preview** against local policy (do not forward if local window is saturated).

### 4.3 Heavy checks (sampled)
- TEE attestations, quantum trap verifications, VDF verification run sampled (e.g., 1/16 baseline) and adapt by peer score.

### 4.4 Storage & indexing
- Keep a rolling 10k share index keyed by `(kind, nullifier)` with ~10 min TTL for dedupe and miner-side selection.

---

## 5) Fast-sync strategy

1. **Headers-first** to a peer-majority tip (≥2 peers concur).
2. **Stabilize window**: wait until last `K=64` headers have multiple announcers and timestamps monotone.
3. **Backfill bodies** newer → older within the last `K`, then continue backward in batches until checkpoint or configured depth.
4. **Execute/import** into core when body is present and structural checks pass (outside of P2P sync scope).

If a competing branch emerges during backfill that overtakes by >Δ (config 8 blocks), **pause backfill**, advance headers to new tip, then resume.

---

## 6) Edge cases & failure modes

- **Timestamp skew**: if a peer’s headers exceed future skew repeatedly → downgrade score, ignore their tip for 10 min.
- **Policy/alg root rotation**: if header’s alg-policy root is unknown, fetch via gossip or RPC `/openrpc.json` pointer, then resume; do not accept txs using disallowed algs until local policy updated.
- **Θ shock**: sudden Θ drop > allowed clamp triggers *suspect* flag; require multi-peer corroboration before accepting subsequent headers.
- **Compact block missing txs**: if short IDs fail to reconstruct, fall back to full block fetch.
- **Blob unavailability**: mark block as *data-availability-pending*; do not forward as fully synced until DA checks pass.
- **Reorg depth bound**: beyond soft (64), require corroboration; beyond hard (512), enter *safe mode*: stop body imports, keep header sync only, alert operator.
- **Eclipse mitigation**: require minimum diversity (ASNs/IP blocks). If unique peers < 3, reduce acceptance of tip extensions (increase corroboration thresholds).
- **Backpressure**: if global buckets saturated > 10 s, reduce parallelism (halve in-flight per peer), prioritize newest headers and control messages.
- **Crash/restart**: on boot, rebuild fetch queues from DB:
  - headers with `is_body_present=false` → enqueue bodies
  - orphan pool rebuild from `parentHash` links
  - resume mempool gossip after replaying recent `seen-set` from disk (optional bloom snapshot).

---

## 7) Interop checklist

1. Implement **locator-based** GETHEADERS/HEADERS with contiguous runs.
2. Enforce cheap header checks and Θ/policy plausibility before advancing the tip.
3. Maintain a block **fetch queue** with hedged multi-peer downloads and cancel on first success.
4. Keep an **orphan pool** with TTL for bodies whose parents are missing.
5. Run **cheap** admission for txs and shares; perform **sampled heavy** verification with adaptive rates.
6. Respect token-bucket limits and per-topic max sizes (see **GOSSIP.md**).
7. Persist progress atomically to the DB to survive crashes.
8. Require **multi-peer corroboration** for deep reorgs and Θ/policy anomalies.

*End of spec.*
