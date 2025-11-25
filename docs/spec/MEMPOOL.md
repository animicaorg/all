# MEMPOOL — Admission, Replacement, Fee Market, DoS Limits

This document specifies the canonical mempool behavior for Animica nodes. It aligns with the reference implementation in:

- `mempool/config.py` — limits (max txs/bytes), min gas price policy, TTLs, per-peer caps  
- `mempool/validate.py` — stateless checks: sizes, chainId, gas limits, PQ-sig precheck  
- `mempool/accounting.py` — balance/allowance checks, intrinsic gas, max-spend estimate  
- `mempool/priority.py` — effective priority calculator (tip, size, age, RBF thresholds)  
- `mempool/sequence.py` — per-sender nonce queues; ready/held transitions  
- `mempool/policy.py` — admission & replacement rules, ban durations  
- `mempool/fee_market.py` — dynamic floor, surge multiplier, base/tip split, watermark  
- `mempool/evict.py` — memory pressure eviction; per-sender fairness caps  
- `mempool/drain.py` — ordered selection under gas/byte budgets  
- `mempool/limiter.py` — DoS throttles (per-peer, global; tx/s & bytes/s)  
- `mempool/notify.py` — local events (`pendingTx`, `droppedTx`, `replacedTx`)  
- `mempool/adapters/*` — integration with RPC submit / P2P admission / miner feed

Test coverage: `mempool/tests/*`.

---

## 1) Goals & invariants

**Goals**
- High admission throughput with robust DoS resistance.
- Deterministic selection for block building given the same pool state.
- Economic fairness: prioritize by **effective fee** and **readiness** (nonces).
- Predictable UX for replacement (RBF) and expiration.

**Invariants**
1. A pool holds at most one tx per `(sender, nonce)` in the **ready/held** union.  
2. A tx is **ready** iff all lower nonces from the same sender are either on-chain or present in the pool.  
3. Replacement requires a strictly higher **effective price** by at least `RBF_DELTA` (percentage).  
4. Over global/per-sender limits, eviction removes the **lowest priority** entries first while enforcing per-sender fairness.

---

## 2) Objects & queues

### 2.1 Types

- **PoolTx**: decoded `core/types/tx.py` + metadata.
- **TxMeta**:
  - `received_at: Timestamp`
  - `size_bytes: int`
  - `intrinsic_gas: Gas`
  - `gas_limit: Gas`
  - `max_fee_per_gas: GasPrice` (total price cap)
  - `max_priority_fee_per_gas: GasPrice` (tip cap)
  - `sender: Address`
  - `nonce: uint64`
  - `chain_id: uint64`
  - `hash: Hash`
  - `account_balance_at_admission: uint256` (snapshot)
  - `priority: float` (derived)
  - `expires_at: Optional[Timestamp]`

- **EffectiveFee** (price actually *payable* by the tx):

base_floor = fee_market.floor()               # dynamic policy floor (not consensus)
base_used  = max(base_floor, 0)               # non-negative
tip_used   = min(max_priority_fee_per_gas, max_fee_per_gas - base_used)
eff_price  = base_used + tip_used

If `tip_used < 0` → reject (insufficient headroom).

### 2.2 Queues

- **Per-sender nonce queues**: map `sender → {held, ready}`:
- `held`: txs with nonce gaps, keyed by `nonce`.
- `ready`: min-heap keyed by `(nonce, -priority)` (nonce ordering dominates).
- **Global PQ**: heap of **ready** txs keyed by `(-priority, tiebreak)` where `tiebreak = hash` (lexicographic ascending) for determinism.

---

## 3) Admission pipeline

1. **Ingress limits** (fast path):
 - Token buckets check per-peer/per-IP & global (see §6), early drop on exhaustion.
 - Duplicate suppression by tx hash.

2. **Stateless validation** (`mempool/validate.py`):
 - `chain_id` equals local config; `gas_limit` within protocol bounds.
 - Size ≤ `MAX_TX_BYTES`.
 - Intrinsic gas computed; `gas_limit >= intrinsic_gas`.
 - PQ signature **precheck** (fast verify or O(1) domain check). If invalid → reject: `InvalidSignature`.
 - Optional **access list** sanity.

3. **Accounting** (`mempool/accounting.py`):
 - Fetch `balance` & `nonce` (stateDB read).
 - Compute **worst-case fee debit** `fee_cap * gas_limit`. If `balance < max_spend` → `InsufficientBalance`.
 - Check `nonce >= account_nonce` (otherwise, `NonceTooLow`).

4. **Fee policy check**:
 - Compute `eff_price` against current **floor** and **cap**; if `eff_price < floor` → `FeeTooLow`.
 - Derive `priority` (see §4).

5. **Sequence placement** (`mempool/sequence.py`):
 - If `(sender, nonce)` exists → go to **replacement** (§5).
 - Else insert into sender queue; mark **ready** iff contiguous from `account_nonce` (consider on-chain delta and pool).

6. **Promotion**:
 - When the lowest missing nonce arrives, promote contiguous chain from `held → ready`, update global PQ.

7. **Notify**:
 - Emit `pendingTx(tx.hash)` once accepted; `mempool/notify.py`.

**TTL**: Each tx receives `expires_at = received_at + TTL_PENDING`. Expired entries are purged lazily (sweep pass).

---

## 4) Priority & selection

### 4.1 Priority function

Let:
- `tip = tip_used = min(max_priority_fee, max_fee - base_used)`
- `sz = size_bytes`
- `age = now - received_at` (seconds)

We define a bounded, additive score:

priority = w_tip * tip
+ w_age * min(age, AGE_CAP_S)
+ w_size * (1 / max(sz, 1))

**Defaults** (policy, not consensus):
- `w_tip = 1.0`
- `w_age = 1e-6` (small tie-breaker to avoid starvation)
- `w_size = 2e3` (favor compact txs slightly)
- `AGE_CAP_S = 300` (5 min)

> Implementations MAY choose an equivalent monotone transformation (e.g., log scaling) if stable ordering is preserved.

### 4.2 Drain (block builder view)

`mempool/drain.py` returns an iterator over **ready** txs under `(gas_budget, byte_budget)`:

1. Pop best from global PQ.
2. If inclusion would exceed either budget → skip & continue (non-destructive peek).
3. Yield tx; update budgets; continue.

Determinism: for the same pool & budgets, the sequence is fixed by `(priority, hash)` ordering and nonce constraints.

---

## 5) Replacement policy (RBF)

A tx `T'` replaces existing `T` iff all hold:

1. Same `(sender, nonce)`.  
2. `gas_limit' >= gas_limit` (no shrink).  
3. **Price bump**:  

eff_price’ >= eff_price * (1 + RBF_DELTA)

with `RBF_DELTA = 0.10` (10%) by default.
4. `size_bytes' <= SIZE_BUMP_MAX * size_bytes` (prevent pathological bloating; default `SIZE_BUMP_MAX = 2.0`).

On success:
- Replace in sender queue; update priority; promote if ready.
- Emit `replacedTx(old_hash, new_hash)`.

**Rejection codes**: `ReplacementUnderpriced`, `GasLimitDecrease`, `TooLargeAfterReplace`.

**Flood control**: Per-sender **RBF window** rate-limits replacements to `RBF_MAX_PER_MIN` (default 60/min) with a leaky bucket.

---

## 6) DoS limits & rate control

### 6.1 Token buckets (`mempool/limiter.py`)
- **Per-peer submit**: `(tx/s, bytes/s)` with bursts; default `5 tx/s`, `128 KiB/s`, burst 2×.  
- **Global ingress**: `(tx/s, bytes/s)` sized for node’s capacity; default `2k tx/s`, `32 MiB/s`.
- **RPC vs P2P**: Separate buckets; RPC gets stricter defaults.

### 6.2 Per-sender fairness & caps
- At most `MAX_TXS_PER_SENDER` in pool (ready+held); default `1024`.  
- At most `MAX_READY_PER_SENDER` ready; default `128` (prevents single-sender dominance).  
- Enforced during admission and on promotion.

### 6.3 Memory pressure eviction (`mempool/evict.py`)
- Global hard limit `MAX_POOL_BYTES` and `MAX_POOL_TXS`.
- If exceeded, evict the **lowest priority** txs first, but never drop the **last** ready tx for a sender if it would break contiguity for a larger ready chain (to maintain liveness).
- Emit `droppedTx(hash, reason="EvictedLowPriority")`.

### 6.4 Ban & backoff (`mempool/policy.py`)
- Malformed spam, repeated invalid signatures → temporary origin bans.
- Exponential backoff on peers that exceed submit errors.

---

## 7) Fee market & floor policy

**Policy module** (non-consensus) estimates a dynamic **floor** that tracks recent block conditions.

### 7.1 EMA floor

Let `m_t` be the median **paid** base+tip per gas over the last `W` blocks (e.g., `W = 20`). The floor evolves:

floor_{t+1} = clamp(
α * m_t + (1 - α) * floor_t,
min_floor, max_floor
)

Defaults: `α = 0.2`, `min_floor = 0`, `max_floor = +∞`.

### 7.2 Surge multiplier & watermark

When pool occupancy exceeds `SURGE_THRESHOLD` (e.g., `0.7` of bytes limit), apply:

floor_eff = floor * (1 + SURGE_MULT * occ_excess)

with `SURGE_MULT = 2.0`, `occ_excess = max(0, occupancy - SURGE_THRESHOLD)/(1 - SURGE_THRESHOLD)`.

A rolling **watermark** tracks the minimum accepted `eff_price` among the top `K` selected txs to stabilize UX; samples are exported for wallets to estimate.

> These knobs are only **admission** policy; miners/opportunistic builders still select by `priority`.

---

## 8) Reorg handling (`mempool/reorg.py`)

On reorg:
1. Identify reverted receipts and their txs.  
2. Re-inject reverted txs if still valid (signature, TTL, balance).  
3. For affected senders, rebuild ready/held queues and promotions.  
4. Emit `pendingTx` for successfully re-added txs.

---

## 9) Expiration & garbage collection

- **Pending TTL**: default `3 hours` since `received_at`.  
- **Dropped reasons**: `ExpiredTTL`, `InsufficientBalance`, `ChainIdMismatch`, `FeeTooLow`, `InvalidSignature`, `NonceTooLow`, `EvictedLowPriority`.  
- Periodic sweeps remove expired/invalid entries and compact indexes.

---

## 10) Interfaces

### 10.1 RPC submit (`mempool/adapters/rpc_submit.py`)
- `tx.sendRawTransaction`: decode → validate → admit/replace; returns `hash` on success; structured errors on failure.
- `tx.getTransactionByHash`: looks in pool first, then DB.

### 10.2 P2P admission (`mempool/adapters/p2p_admission.py`)
- Fast pre-admission checks; rate-limit; dedupe/bloom; defer heavy checks to pipeline.

### 10.3 Miner feed (`mempool/adapters/miner_feed.py`)
- `iter_ready(gas_budget, byte_budget)` for block assembly.
- Optional hints: top-N by `priority`, sender burst windows.

---

## 11) Metrics & events

Prometheus (`mempool/metrics.py`):
- Gauges: `pool_txs`, `pool_bytes`, `ready_txs`, `held_txs`.
- Counters: `admitted_total`, `replaced_total`, `dropped_total{reason}`, `evicted_total`, `expired_total`.
- Histograms: `admission_latency_ms`, `replacement_latency_ms`, `selection_latency_ms`, `priority_score`.

Events (`mempool/notify.py`):
- `pendingTx`, `replacedTx`, `droppedTx`, `promotedTx` (optional).

---

## 12) Parameter defaults (policy)

| Parameter                      | Default            | Notes                                  |
|-------------------------------|--------------------|----------------------------------------|
| `MAX_POOL_BYTES`              | 1.5 GiB            | soft target; eviction above            |
| `MAX_POOL_TXS`                | 1,000,000          |                                         |
| `MAX_TX_BYTES`                | 128 KiB            | hard cap per tx                         |
| `MAX_TXS_PER_SENDER`          | 1024               | fairness                                |
| `MAX_READY_PER_SENDER`        | 128                | fairness                                |
| `TTL_PENDING`                 | 3h                 | expiration                              |
| `RBF_DELTA`                   | 10%                | minimum price bump                      |
| `SIZE_BUMP_MAX`               | 2.0×               | anti-bloat during RBF                   |
| `EMA_ALPHA`                   | 0.2                | fee floor filter                        |
| `SURGE_THRESHOLD`             | 0.7                | occupancy trigger                       |
| `SURGE_MULT`                  | 2.0                | surge slope                             |
| Per-peer `tx/s`               | 5                  | RPC stricter than P2P                   |
| Per-peer `bytes/s`            | 128 KiB            |                                         |
| Global `tx/s`                 | 2000               |                                         |
| Global `bytes/s`              | 32 MiB             |                                         |

> Operators MAY tune with environment or config file; values are **not** consensus.

---

## 13) Pseudocode (informative)

### 13.1 Admission

```text
admit(tx):
  if !limiter.allow(peer, tx.size): reject(RateLimited)
  if pool.contains(tx.hash): return Duplicate

  if !validate_stateless(tx): reject(InvalidTx)
  acct = state.read_account(tx.sender)
  if !accounting.can_afford(acct, tx): reject(InsufficientBalance)

  eff = fee_market.effective_price(tx)
  if eff < fee_market.floor_effective(): reject(FeeTooLow)

  q = queues[tx.sender]
  if q.has_nonce(tx.nonce):
     return replace(q, tx)

  if pool.per_sender_count(tx.sender) >= MAX_TXS_PER_SENDER:
     evict_lowest_from_sender(tx.sender) or reject(SenderFull)

  meta = compute_meta(tx, eff)
  q.insert(tx, meta)
  promote_if_contiguous(q)
  global_ready_heap.maybe_add(tx)
  notify.pending(tx.hash)
  return Ok

13.2 Replacement

replace(q, tx'):
  tx = q.get(tx'.nonce)
  if tx'.gas_limit < tx.gas_limit: reject(GasLimitDecrease)
  if size(tx') > SIZE_BUMP_MAX * size(tx): reject(TooLargeAfterReplace)
  if eff(tx') < eff(tx) * (1 + RBF_DELTA): reject(ReplacementUnderpriced)

  q.swap(tx', meta')
  global_ready_heap.update(tx' or noop if held)
  notify.replaced(tx.hash, tx'.hash)
  return Ok

13.3 Drain

drain(gas_budget, byte_budget):
  out = []
  while budgets_ok and !heap.empty():
    cand = heap.peek()
    if cand.gas_limit > gas_budget or cand.size > byte_budget:
       heap.skip(cand)       # non-destructive; candidate may fit later
       continue
    heap.pop()
    out.push(cand)
    gas_budget  -= cand.gas_limit
    byte_budget -= cand.size
  return out


⸻

14) Security considerations
	•	Nonce pinning: Attackers may submit a low-fee low-nonce tx to block higher-nonce spends. Mitigations:
	•	RBF with modest RBF_DELTA,
	•	Fee floor & surge to reject lowball pins during congestion,
	•	Per-sender caps prevent deep chains of held txs.
	•	Replacement churn: Sender may spam RBF to create work. Mitigation:
	•	RBF rate limit; min price bump; size growth check.
	•	Selective relay: Nodes may censor low-fee txs. Users should bump or retry via alternate peers.
	•	Mem exhaustion: Enforced global bytes/txs caps + eviction by priority maintain liveness.

⸻

15) Versioning & compatibility

This is MEMPOOL v1. Policy knobs are non-consensus and can evolve between minor releases. RPC semantics (tx.sendRawTransaction) remain stable; structured error codes are forward-compatible by string keys.

⸻

16) References (implementation)
	•	mempool/tests/test_validate.py — stateless boundaries
	•	mempool/tests/test_admission_and_sequence.py — ready/held transitions
	•	mempool/tests/test_replacement.py — RBF correctness
	•	mempool/tests/test_fee_market.py — floor & surge behavior
	•	mempool/tests/test_eviction.py — fairness & eviction
	•	mempool/tests/test_drain.py — selection ordering
	•	mempool/tests/test_rate_limit.py — token buckets
	•	rpc/methods/tx.py — RPC submit path integration
	•	mining/adapters/* — builder consumption

