# SCHEDULER.md — Serial vs Optimistic Execution Guarantees

This document specifies the execution scheduling model used by Animica and the guarantees each mode must provide. It is **consensus-critical**: any deviation that changes gas usage, logs, receipts, or the final `stateRoot` is a hard fork.

Relevant code:
- `execution/scheduler/{serial.py, optimistic.py, lockset.py, merge.py, deps.py}`
- `execution/state/{journal.py,snapshots.py,access_tracker.py,events.py,receipts.py}`
- `execution/runtime/{executor.py,dispatcher.py,fees.py}`
- `execution/access_list/{build.py,merge.py}`

---

## 1) Scope & Model

- A block contains an ordered list of transactions `tx[0..n-1]`.
- The **canonical semantics** is **serial** execution in index order. All other schedulers must be **observationally equivalent** to this baseline:
  - identical per-tx **status** (SUCCESS/REVERT/OOG),
  - identical **gasUsed** per tx and gas accounting (base/tip/refund),
  - identical **logs** sequence (per-tx logs concatenated in tx index order),
  - identical **receipts** and final **stateRoot**.
- Execution is deterministic (no wall-clock, no nondeterministic IO); VM and host calls are bounded and gas-metered.

---

## 2) Read/Write Sets & Locksets

The scheduler relies on a per-tx **access trace** captured by `execution/state/access_tracker.py`:
- `R(tx)`: set of storage locations read.
- `W(tx)`: set of storage locations written (including account metadata, balances, nonces, code hash).
- Locations are `(addr_bytes, key)` pairs for storage and `(addr_bytes, META_FIELD)` for account/meta writes.
- The **lockset** (see `scheduler/lockset.py`) is constructed from `(R,W)`.

Two transactions **conflict** if any of the following holds (using strict serial order precedence):
- **WW**: `W(tx_i) ∩ W(tx_j) ≠ ∅`
- **RW**: `R(tx_i) ∩ W(tx_j) ≠ ∅` when `j < i` in serial order
- **WR**: `W(tx_i) ∩ R(tx_j) ≠ ∅` when `i < j` in serial order

> Note: Reads of **logs/receipts/transient counters** are not allowed from contracts and thus not modeled.

---

## 3) Serial Scheduler (Baseline)

### Definition
`serial.py` executes `tx[i]` strictly in index order against a single mutable state with journaling:
1. Create snapshot `S_i`.
2. Apply `tx[i]` via `runtime/executor.apply_tx(...)`.
3. If SUCCESS or REVERT (not OOG during intrinsic), commit journal; otherwise revert as specified by runtime and proceed.
4. Append logs and build receipt.
5. Continue to `tx[i+1]`. Finalize `stateRoot` after the block.

### Guarantees
- Trivial equivalence (by definition).
- Deterministic and safe under all conflict patterns.
- Reference for test vectors and cross-implementation checks.

---

## 4) Optimistic Scheduler (Parallel Prototype)

`optimistic.py` attempts parallelism while preserving serial equivalence.

### High-level Algorithm
1. **Staging snapshots.** Take a base snapshot `S_base` for the block. Each tx runs on an **isolated fork** (copy-on-write journal) seeded with `S_base` or with a **dependency-applied** snapshot (see below).
2. **Waves.** Partition txs into waves using a dependency prepass (`scheduler/deps.py`). A new tx can share a wave if current wave **does not require** its predecessors' writes by static access list hints (if available) or by conservative heuristics (size/nonce locality). Hints are advisory; correctness relies on post-run conflict checks.
3. **Execute wave in parallel.** For each `tx` in the wave:
   - Run to completion with full gas metering and capture `(R,W)` via `access_tracker`.
   - Buffer **effects** (journal delta), **gasUsed**, and **logs** (with original tx index).
4. **Detect conflicts.** Compare `(R,W)` pairs across the wave and against all **already-committed** transactions from earlier waves using the serial order index. Mark any `tx` that violates WW/RW/WR as **conflicted**.
5. **Merge non-conflicts deterministically.**
   - Sort the **non-conflicted** txs by **original index** and apply their journals to the **global state** in that order (`scheduler/merge.py`), emitting logs and receipts in index order.
   - For **conflicted** txs: discard their speculative journals (not results), and **reschedule** them in a later wave, possibly falling back to serial on pathological contention.
6. **Repeat** until all txs are either merged or definitively reverted per runtime.

### Deterministic Merge & Ordering
- **No reordering:** commit order is always ascending original index.
- Logs are appended per committed tx in index order; per-tx logs are **not** interleaved.
- Gas is exactly what the isolated run measured (the VM/runtime is pure w.r.t. state snapshot and input).
- If a tx is re-executed due to dependency changes, it must be re-run from the latest committed snapshot; the first successful non-conflicting run’s `gasUsed/logs/receipt` becomes final.

### Liveness & Fallback
- If a wave produces conflicts above a threshold, switch that subset to **serial** mode to avoid thrashing.
- The algorithm must always terminate because the serial fallback is always available.

---

## 5) Correctness Argument (Sketch)

Let `⟦tx⟧_S → (Δ, gas, logs)` denote deterministic application of `tx` to state `S`, yielding a journal `Δ`.

- **Serial baseline:** `S_{i+1} = S_i ⊕ Δ_i` applying in index order.
- **Optimistic wave:** Each `tx_i` runs on some `S*` snapshot that is a prefix-commit of earlier indices. If `(R_i,W_i)` is **disjoint** from any non-committed earlier `tx_j`’s writes, then `Δ_i` **commutes** with earlier `Δ_j` and produces the same result as serial when merged in index order.
- Conflict detection ensures we **only** merge commuting `Δ`’s; non-commuting ones are re-run after dependencies are committed. Thus the final state equals the serial baseline.

---

## 6) Gas, Refunds, Fees, and Events

- **GasUsed** is a pure function of `(tx, params, prestate_for_tx)`. Running on a snapshot that is a valid serial prefix ensures the same GasUsed as serial.
- **Refunds** and **fee accounting** are part of `Δ` and thus merged with the same ordering guarantees.
- **Events/logs** are buffered per-tx and appended in index order at merge time → identical bloom/logsRoot vs serial.

---

## 7) Snapshots & Journals

- **Snapshot API:** `snap = snapshots.take()` returns an identifier; `journal.begin(snap)` starts a forked write set.
- **Commit:** `journal.commit()` materializes writes into the KV; **only** during merge in index order.
- **Abort:** `journal.abort()` discards speculative results for conflicted txs.
- Snapshots are **content-addressable** by height+tx-index for reproducibility in tests.

---

## 8) Access Lists (Hints) & Dynamic Capture

- If a tx provides an **access list** (from `execution/access_list/…`), it may be used to pre-cluster and reduce conflicts.
- Hints are **non-normative**. The actual `(R,W)` captured by the access tracker is authoritative for conflict checks.

---

## 9) Edge Cases

- **Self-dependencies:** Multiple txs from the same sender with sequential nonces naturally conflict via account nonce/balance writes (WW), so they serialize.
- **Contract deployment:** Writes `codeHash` and initial storage; conflicts detected via account/meta and storage WW.
- **Revert/OOG:** A tx that REVERTs or OOGs still produces deterministic `(R,W)` up to the failure point; only its committed effects follow serial semantics (REVERT has no state writes, OOG after intrinsic prevents execution).
- **Blob/DA checks:** Any DA-related cost checks performed via adapters are pure and accounted in gas; they must not read mutable global state outside `(R,W)` capture.

---

## 10) Determinism Invariants

1. **No reordering:** commit and emission order equals tx indices.
2. **Pure runtime:** VM + host calls are deterministic given `(tx, params, snapshot)`.
3. **Fixed hashing/serialization:** all hashes use canonical encodings (see `STATE.md`).
4. **Stable conflict predicate:** same `(R,W)` on re-execution over same snapshot.
5. **Idempotent merge:** committing the same non-conflicting set twice is equivalent to once (guarded by indices and snapshot lineage).

---

## 11) Config & Tuning (Non-Consensus)

- Max workers / wave size, conflict backoff thresholds.
- Heuristics for pre-bucketing (by sender, by access-list domains).
- These affect **performance only**; correctness is enforced by conflict detection + deterministic merge.

---

## 12) Testing Matrix

- **Equivalence**: For randomized blocks, assert serial vs optimistic equality of:
  - per-tx status/gasUsed/logs/receipts
  - final `stateRoot`, `receiptsRoot`, `logsRoot`
- **Adversarial conflicts**: same-sender sequences; cross-contract hot keys; widespread WW collisions.
- **Reorg safety**: re-run both schedulers after reorg and compare roots.
- **Pathological hints**: incorrect/under-specified access lists; ensure dynamic capture prevents divergence.

Unit & integration tests live under:
- `execution/tests/test_scheduler_serial.py`
- `execution/tests/test_scheduler_optimistic.py`
- `execution/tests/test_executor_roundtrip.py`

---

## 13) Future Work (Non-Consensus)

- **Static planning** using prior block access summaries.
- **Lock elision** for read-only calls coalesced across txs.
- **Speculative prefetch** and write-combining guarded by the same conflict rules.

*End of spec.*
