# Results — Lifecycle, Availability, Prunable Retention

This document specifies how **capability results** (from AI / Quantum jobs and
other syscalls that yield data) move from enqueue → proof-backed resolution →
contract-visible read → prunable retention. It defines invariants required for
determinism and replay safety.

**Related code**
- Runtime bindings: `capabilities/runtime/{abi_bindings.py,state_cache.py,determinism.py}`
- Jobs & stores: `capabilities/jobs/{types.py,id.py,queue.py,receipts.py,result_store.py,resolver.py,index.py}`
- DA bridge (optional large outputs): `capabilities/adapters/da.py`
- RPC read-only: `capabilities/rpc/{mount.py,methods.py,ws.py}`
- Proofs adapter: `capabilities/adapters/proofs.py`
- AICF linkage: `aicf/integration/proofs_bridge.py`
- Hash & domains: `proofs/utils/hash.py`, `core/utils/hash.py`

---

## 1) Terms & objects

### 1.1 Task identity
Jobs created by syscalls derive a deterministic **task_id**:

task_id = H(
“animica.cap.task_id.v1” |
chainId | height | txHash | callerAddress |
canonical(job_payload)
)

- Binds to chain & height for **reorg safety**.
- Idempotent: attempting to enqueue the same payload again returns the same
  receipt; duplicates are suppressed.

### 1.2 ResultRecord
Minimal, consensus-reconstructable record written by the resolver:

- `task_id: bytes32`
- `status: {PENDING, RESOLVED, CONSUMED, EXPIRED}`
- `height_resolved: uint64` (block height that included the proof)
- `proof_nullifier: bytes32` (links to on-chain proof; replay-protected)
- `result_digest: bytes32` (SHA3-256 over canonical result bytes)
- `result_size: uint64` (bytes; optional for small/inline results)
- `mime_type: ascii` (optional; advisory)
- `da_commitment: bytes32?` (optional NMT root for large outputs pinned to DA)
- `expires_at: uint64` (policy-derived watermark for retention)
- Indices: by `task_id`, by `height_resolved`, by `callerAddress` (via a
  separate index keyed off the original JobRequest).

> Consensus depends only on **proof validity** and the **digests**; payload
> bytes and DA retrieval are **non-consensus** conveniences.

---

## 2) Lifecycle

### 2.1 Enqueue (block *h*)
- Contract calls `ai_enqueue(...)` / `quantum_enqueue(...)`.
- Admission builds a **JobReceipt** (idempotent on `task_id`), reserves funds
  per policy, and records `PENDING` in the volatile queue.
- No ResultRecord yet; result store remains empty.

### 2.2 Proof-backed resolution (block *h+Δ*)
- A provider completes the job and submits a proof (AI/Quantum).
- Verifiers check attestations & metrics; `capabilities/adapters/proofs.py`
  emits a **resolution event** with:
  - `task_id`, `proof_nullifier`, `result_digest`, optional `da_commitment`.
- `jobs/resolver.py` writes **ResultRecord{RESOLVED}** at `height_resolved`.
  - Idempotent: if `(task_id)` already linked → reject duplicate resolution.
  - Funds are charged/refunded via policy hooks.

### 2.3 Contract read (first eligible at block *h+Δ+1*)
- **Next-block rule:** contracts may read a result **not earlier than the next
  block** after resolution to guarantee determinism during execution.
- `read_result(task_id)` returns `(result_digest, size, da_commitment, metadata)`
  or **NoResultYet** if not resolved or not yet eligible.
- Optionally, application code fetches bytes via DA using `da_commitment`.

### 2.4 Consumption & retention
- Upon first successful contract read (or a dedicated `consume_result` host
  call), status may transition to **CONSUMED** (implementation choice; does not
  affect determinism).
- Retention watermarks decide when **RESOLVED/CONSUMED** records are pruned to
  compact storage. DA blobs remain or can be GC'd per DA policy.

### 2.5 Expiry
- If no valid proof arrives before a policy TTL, the job **EXPIRES** and
  reserved funds are refunded. No ResultRecord is created.

---

## 3) Invariants

1. **Deterministic read window**  
   A result resolved in block `B` becomes readable from `B+1`. Reads during `B`
   must return **NoResultYet**.

2. **Single link per task**  
   `(task_id → proof_nullifier)` is a **one-to-one** mapping. Duplicate or
   conflicting links are rejected.

3. **Replay protection**  
   Proof nullifiers are unique within policy windows. Resolver refuses a proof
   if its nullifier already appears linked elsewhere.

4. **Digest-first**  
   Consensus-visible state depends only on **digests** (and commitments), never
   on external byte retrieval. Applications may fetch bytes out-of-band.

5. **Reorg safety**  
   - If `B` is orphaned, roll back ResultRecords linked to `B`.
   - Replaying on the new chain is deterministic because the same proof tied to
     a new block yields the same `result_digest` and a new `height_resolved`.
   - Contract reads re-evaluate under the active chain head.

---

## 4) Availability tiers

| Tier        | What is stored                                  | Purpose                                  |
|-------------|--------------------------------------------------|------------------------------------------|
| Hot (KV)    | ResultRecord + small inline bytes (optional)     | Fast reads; RPC summaries                |
| Warm (FS)   | Result byte blobs (content-addressed by digest)  | Local dev/test convenience               |
| Cold (DA)   | DA `commitment` (+ retrieval proof on demand)    | Network-available archival availability  |

**Policy knobs**
- `max_inline_bytes` — inline threshold (0 to disable).
- `retain_hot_blocks` — number of recent blocks kept hot.
- `retain_warm_days` — window before FS compaction.
- `retain_cold_days` — DA retention target (best effort).
- `result_ttl_blocks` — expiry watermark for PENDING jobs.

---

## 5) Prunable retention & GC

### 5.1 Watermarks
- `resolved_watermark = head_height - retain_hot_blocks`
- Records with `height_resolved ≤ resolved_watermark` are candidates for GC.

### 5.2 GC passes
1. **Hot→Warm demotion**: drop inline bytes, keep ResultRecord + FS blob / DA ptr.
2. **Warm compaction**: delete FS blobs whose digest exists in DA (or when
   policy allows no-DA loss).
3. **Index vacuum**: purge secondary indexes for pruned tasks; keep a slim
   tombstone (task_id, height_resolved) for audit until `retain_cold_days`.

### 5.3 Safety
- ResultRecord **header** (task_id, digest, height) should remain until the
  outer audit window elapses, enabling light clients to verify historical
  resolution facts without payload bytes.

---

## 6) RPC & events (read-only)

- `cap.getResult(task_id) → ResultView`  
  Returns status, digests, sizes, DA commitment, and resolution height.
- `cap.listJobs(caller?, status?, from_height?, limit?)`
- WS topic: `cap.jobCompleted` (task_id, height_resolved, result_digest)

**Note:** RPC must not stream raw result bytes; it may optionally expose a
local byte fetch for small/inlined results when enabled for dev nets.

---

## 7) Error cases

- **NoResultYet**: not resolved or next-block window not reached.
- **Expired**: job TTL elapsed without proof; funds refunded.
- **ResultPruned**: metadata exists but bytes are pruned locally; use DA.
- **ProofConflict**: attempted re-link with different nullifier.
- **ChainMismatch**: task_id not from this chain (bad client).

---

## 8) Reorg handling

On reorg rollback of block `B`:
- Remove `(task_id → proof_nullifier)` links created at `B`.
- Recompute `state_cache` impacts (if any) on pending reads.
- If the same proof reappears in `B'`, write a fresh ResultRecord at the new
  `height_resolved`.

Pseudo:
```python
for rr in result_store.by_height(B.height):
    result_store.unlink(rr.task_id)
    indexes.remove(rr.task_id)


⸻

9) Integrity & verification
	•	result_digest = sha3_256(domain | canonical_bytes)
Domain tag e.g. b"animica.cap.result.v1".
	•	If da_commitment is present, light clients can verify availability using
DA proofs without fetching bytes.

⸻

10) Metrics

Expose Prometheus counters/gauges:
	•	cap_results_resolved_total{kind}
	•	cap_results_consumed_total{kind}
	•	cap_results_pruned_total{tier}
	•	cap_results_bytes_hot/warm/cold
	•	cap_results_lookup_latency_ms_bucket
	•	cap_results_reorg_rollbacks_total

⸻

11) Operator guidance
	•	For dev/test, enable inline bytes up to a small cap (e.g., 16–64 KiB).
	•	For public nets, prefer DA-only for payloads; keep hot store metadata
minimal to reduce disk churn.
	•	Monitor GC durations and ensure they are bounded (run in background with
quotas).

⸻

12) Test plan (must pass)
	•	Resolve → next-block read boundary.
	•	Duplicate proof / duplicate task resolution rejection.
	•	DA-backed result: digest matches bytes; retrieval proof verifies.
	•	Reorg: resolve at B, read allowed at B+1; orphan B → read becomes NoResultYet.
	•	GC: after watermark, hot bytes gone; DA path still verifies.
	•	Expiry: pending jobs refund; no ResultRecord created.

⸻

13) Back-compat & upgrades
	•	If domain tags change, bump to *.v2 and migrate by dual-hashing during a
grace window.
	•	Extend ResultRecord only by append-only optional fields to preserve
on-disk compatibility.

