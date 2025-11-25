# Animica Mempool

A high-signal, DoS-resistant transaction pool that:
- admits only statically valid transactions (chainId, sizes, gas, PQ-sig precheck),
- sequences transactions per sender to avoid nonce gaps,
- prioritizes by **effective fee**, **age**, and **size pressure**,
- supports safe **replace-by-fee (RBF)**,
- exports an efficient **drain iterator** for block building,
- adapts a **dynamic min-fee floor** with a surge multiplier under load,
- integrates with `rpc/` for submission & queries and with `mining/` for candidate packing.

This document explains the model, invariants, fee/priority math, and operational knobs.

---

## 1) Architecture (TL;DR)

- **Indexes**
  - `tx_lookup`: hash → `PoolTx`
  - `by_sender`: address → `SequenceQueue` (ready/held & smallest-next-nonce)
  - `heaps`: priority queues for *ready* transactions:
    - `PQ_main`: global ordering by PriorityScore
    - `PQ_sender`: small side-queues to enforce per-sender fairness caps

- **Hot path**
  1. `validate.fast`: size limits, CBOR decode sanity, chainId, PQ-signature precheck
  2. `accounting.peek`: read balance/nonce snapshot (from `execution/adapters/state_db.py`)
  3. `sequence.enqueue`: place in per-sender queue; mark ready iff `nonce == expected`
  4. compute `EffectiveFee` & `PriorityScore`; push to `PQ_main` if ready
  5. apply **admission throttle** & **floor**; maybe reject/park

- **Drain path**
  - `drain.pop_ready(gas_budget, byte_budget) → [PoolTx]` in deterministic priority order
  - yields txs while respecting gas/bytes budgets, sender fairness caps, and replacement constraints

- **Reorg path**
  - on reorg, re-inject reverted receipts, update sender nonces, re-ready as needed.

---

## 2) Invariants

1. **Deterministic ordering within equal inputs**  
   Given equal pool state and inputs, drain order is deterministic.

2. **No nonce gaps per sender**  
   A sender’s `nonce = n+1` is *held* until `nonce = n` is accepted or dropped.

3. **Replacement safety**  
   A tx `(sender, nonce)` can be replaced only by a strictly better effective fee:

EffectiveFee(new) ≥ EffectiveFee(old) × (1 + RBF_PCT)

Default `RBF_PCT = 0.125` (12.5%).

4. **Floor respected**  
No tx with `gas_price < MinFeeFloor` is admitted to ready sets (parked or rejected).

5. **Resource ceilings**  
Soft/hard caps on total tx count and total bytes; eviction never violates fairness.

---

## 3) DoS Philosophy

- **Push checks to the edge**: cheap stateless validation & PQ-sig precheck before any heavy path.
- **Token buckets**: per-IP/per-peer ingress (tx/s and bytes/s), plus a global ingress bucket.
- **Watermark + eviction**: rolling min-fee watermark; under pressure, evict the lowest-priority tail.
- **Per-sender fairness**: caps on how many ready txs per sender can occupy the front of the PQ.
- **Cheap decoding**: deterministic CBOR checks, fixed upper bounds for fields (access list, data).
- **Amortized accounting**: cache recent balance/nonce reads; batch updates from recent blocks.

---

## 4) Fee Market & Priority

### 4.1 Effective Fee

`gas_price` (user) splits into **base** + **tip** conceptually for miner economics; the pool uses an **effective** view:

EffectiveFee = max(gas_price, MinFeeFloor)            # used for replacement & ordering
TipComponent = max(0, gas_price - EMA_PaidBase)       # for miner-side UI & selection

`EMA_PaidBase` is an exponential moving average of the median paid price of *recently included* txs (see below).  
Some chains may adopt explicit base-fee; here we keep a minimal, empirically stable estimator.

### 4.2 Dynamic Min-Fee Floor

The mempool maintains a rolling floor `M_t` from recent inclusion prices:

Take median of included gas_price in last W blocks, then EMA:

Median_t   = median( { gas_price(tx) | tx ∈ blocks[t-W+1..t] } )
M_t        = EMA(Median_t; α_floor)                    # α_floor ∈ (0,1], default 0.2
MinFeeFloor = clamp(M_t, floor_min, floor_max)

**Surge multiplier** when pool occupancy is high:

occupancy = pool_bytes / POOL_BYTES_SOFT
surge     = 1 + β * max(0, occupancy - θ)             # θ=0.7, β=2.0 by default
MinFeeFloor’ = MinFeeFloor * surge

During spikes, the temporary floor rises smoothly to keep the pool healthy.

### 4.3 Priority Score

Transactions are ordered by a weighted, unit-safe score:

Units:

- fee terms in gwei (or smallest unit)

- size in kilobytes

- age in seconds (capped)

SizeKB   = max(0.001, tx_size_bytes / 1024.0)
AgeCap   = min(age_secs, AGE_SOFT_CAP)                 # e.g. 1 hour
PriFee   = ln(1 + EffectiveFee / F0)                   # F0 is scaling, e.g. 1 gwei
PriSize  = 1 / SizeKB
PriAge   = sqrt(AgeCap)

PriorityScore = w_fee * PriFee + w_size * PriSize + w_age * PriAge

Defaults: `w_fee=1.0, w_size=0.15, w_age=0.10, F0=1`.  
Rationale:
- logarithm tames extreme fees but preserves rank,
- inverse-size rewards compact txs under bandwidth pressure,
- square-root age prevents starvation without dominating.

**Fairness guard:** a sender can contribute at most `K` contiguous txs to the top of the queue (`K=2` default). Overflow is placed into a per-sender side-queue that is interleaved.

---

## 5) Admission, Validation, Sequencing

### 5.1 Stateless validation (fast path)
- max size checks (bytes, access list entries),
- chainId match,
- gas limits (intrinsic ≤ gas_limit ≤ block gas cap),
- **PQ signature precheck** (`pq.verify` fast path) to reject malformed signatures.

### 5.2 Accounting checks
- fetch `(balance, nonce)` snapshot,
- ensure `sender_nonce ≤ tx_nonce ≤ sender_nonce + NONCE_FUTURE_LIMIT`,
- estimate max-spend = `gas_limit * gas_price + value`, reject if `balance < max-spend`.

### 5.3 Per-sender sequencing
- Enqueue by `(sender → nonce)`, split into:
  - **ready** (exactly next nonce),
  - **held** (future nonces).
- When a ready tx is accepted/drained/dropped, promote the next-held if contiguous.

---

## 6) Replacement (RBF)

A tx `(sender, nonce)` may be replaced iff:

1. **Same key fields**: same `to`, `value`, `data` length limits OK; gas limit may increase within cap,
2. **Fee bump**: `EffectiveFee(new) ≥ EffectiveFee(old) × (1 + RBF_PCT)`,
3. **No size explosion**: new size ≤ old size × `(1 + SIZE_RBF_PCT)` (default `SIZE_RBF_PCT=0.25`).

If accepted, old tx moves to `replaced-bin` with tombstone TTL; events are emitted via `notify`.

---

## 7) Eviction & Watermark

When reaching `POOL_BYTES_SOFT` or `POOL_TXS_SOFT`, we evict from the tail:

- Maintain a **rolling watermark** `W` = the 5th percentile of `EffectiveFee` over ready set.
- Evict candidates with `EffectiveFee ≤ W` and oldest age first.
- Enforce **per-sender min quota**: at least `MIN_READY_PER_SENDER` (often 0–1) stays, to avoid starvation.

Hard caps (`POOL_*_HARD`) trigger immediate, more aggressive eviction.

---

## 8) Rate Limiting (DoS)

- **Ingress buckets** (token-bucket with jittered refill):
  - per-IP (`tx/s`, `kB/s`), per-connection,
  - per-RPC method (e.g., `tx.sendRawTransaction` stricter),
  - global pool (backstop).
- **Admission latency**: mild, randomized pacing under surge to smooth bursts.
- **Ban windows**: misbehaving peers (invalid spam, repeated underpriced txs) accrue strikes → timed bans.

---

## 9) Drain & Block Building

`drain.pop_ready(gas_budget, byte_budget)` yields an ordered list:
- skips txs failing *current* min-fee floor or post-admission accounting (balance drift),
- ensures block-level gas/bytes budgets respected,
- merges per-sender queues to enforce fairness,
- surfaces *explanations* (why a tx was skipped) for miner telemetry.

Integrations:
- `mempool/adapters/miner_feed.py` → `mining/header_packer.py` and `proof_selector.py`
- `rpc/methods/tx.py` uses `adapters/rpc_submit.py` to admit, and query pending by hash.

---

## 10) Reorg Handling

On reorg:
- gather reverted receipts → identify txs not in new chain,
- re-inject if still valid against updated nonce/balance/floor,
- recompute ready/held; broadcast `pendingTx` again (with dedupe bloom).

---

## 11) Metrics & Telemetry

Exported via `mempool/metrics.py` (Prometheus):
- gauges: `pool_txs`, `pool_bytes`, `ready_txs`, `held_txs`, `floor_gwei`, `watermark_gwei`
- histograms: `admit_latency`, `replace_latency`, `evict_age_secs`, `ingress_tx_size`
- counters: `admitted`, `rejected_{reason}`, `evicted`, `replaced`, `drained`

Event bus (local & WS): `pendingTx`, `droppedTx`, `replacedTx`, `floorChanged`.

---

## 12) Configuration (defaults in `mempool/config.py`)

- **Sizes**: `MAX_TX_BYTES`, `POOL_TXS_SOFT/HARD`, `POOL_BYTES_SOFT/HARD`
- **Fees**: `FLOOR_ALPHA`, `FLOOR_MIN/MAX`, `SURGE_THETA`, `SURGE_BETA`
- **RBF**: `RBF_PCT=0.125`, `SIZE_RBF_PCT=0.25`
- **Sequence**: `NONCE_FUTURE_LIMIT=64`
- **Fairness**: `TOP_K_PER_SENDER=2`, `MIN_READY_PER_SENDER=0`
- **Rate limits**: `INGRESS_TXS_PER_SEC`, `INGRESS_KB_PER_SEC`, per-method overrides
- **TTLs**: `PENDING_TTL_SECS`, `REPLACED_TTL_SECS`, `DROPPED_TTL_SECS`

---

## 13) Interfaces

- **Submit**: `rpc/methods/tx.py::sendRawTransaction` → `adapters/rpc_submit.py::admit()`
- **Query**: `getTransactionByHash` pulls from pool and DB; receipts may be `null` if pending
- **Miner feed**: `adapters/miner_feed.py::iter_ready()`
- **Events**: `mempool/notify.py` publishes; `rpc/ws.py` broadcasts

---

## 14) Testing

See `mempool/tests/`:
- `test_validate.py` — stateless checks & PQ precheck
- `test_admission_and_sequence.py` — nonce gaps, ready/held transitions
- `test_replacement.py` — RBF thresholds & ties
- `test_fee_market.py` — EMA floor & surge behavior
- `test_eviction.py` — watermark eviction & fairness
- `test_drain.py` — selection under budgets
- `test_reorg_reinject.py` — reorg correctness
- `test_adapters.py` / `test_rate_limit.py` — RPC & DoS policies

---

## 15) Operational Guidance

- Monitor `floor_gwei`, `watermark_gwei`, and `rejected_fee_too_low` to tune fee knobs.
- Watch `evicted` + `pool_bytes` oscillations; increase `POOL_BYTES_SOFT` if oscillating under sustained load.
- Set reasonable per-IP buckets in public RPC; require API keys for higher tiers.
- For validators/miners, prefer the drain iterator over hand-rolled heuristics.

---

## 16) Future Work

- Base-fee style mechanism (optional L2-like EIP-1559 variant) with on-chain feedback.
- Bundle-aware admission (access list hints → better parallel execution scheduling).
- ML-assisted spam classifiers (feature-gated; never gate correctness).

