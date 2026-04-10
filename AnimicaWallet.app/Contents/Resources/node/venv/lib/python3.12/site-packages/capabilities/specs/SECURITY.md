# Security Notes — Abuse Prevention, Replay & Nullifier Handling

This document outlines **security invariants** for the capabilities subsystem
(syscalls exposed to deterministic contracts for AI/Quantum work, DA pinning,
zk.verify, and related host bindings). It ties together queue/escrow semantics,
proof nullifiers, idempotency, and reorg handling to preserve consensus safety
and determinism.

**Related code:**
- `capabilities/runtime/{abi_bindings.py,determinism.py,state_cache.py}`
- `capabilities/host/{provider.py,compute.py,blob.py,zk.py,random.py,treasury.py}`
- `capabilities/jobs/{id.py,queue.py,receipts.py,result_store.py,resolver.py}`
- `proofs/{nullifiers.py,ai.py,quantum.py,receipts.py}` (nullifiers & evidence verification)
- `aicf/{economics/*,treasury/*,integration/*}` (pricing/split/settlement)
- `da/*` (result pinning for large payloads)
- `execution/runtime/{fees.py,system.py}` (system debits/credits)

---

## 1) Threat model (at a glance)

**Adversaries:**
- **Spam/DoS** callers attempting to fill queues, exhaust bandwidth/CPU, or force
  expensive verification paths.
- **Replay** adversaries attempting to re-use proofs or job results across blocks,
  windows, or chains to double-charge or claim duplicated rewards.
- **Forgery / evidence tampering**: bogus TEE/QPU attestations, altered result
  digests, or mismatched metadata.
- **Non-determinism injection**: inputs whose interpretation varies across nodes,
  producing state divergence.
- **Economic griefing**: escrow drain attempts, settlement manipulation, or SLA
  gaming (e.g., low-quality outputs, trap evasion).

**Trust boundaries:**
- Consensus accepts/denies blocks purely by the **single acceptance rule**
  and schema/attestation checks. Off-chain components (AICF, DA retrieval, QRNG
  mixers) must not influence consensus except through verified **proofs** that
  bind to a specific block/height and **policy roots**.

---

## 2) Determinism & domain separation

1. **Canonical encoding**
   - All syscall payloads are encoded as **deterministic CBOR** (integer forms,
     sorted map keys). Host code must reject non-canonical encodings.
   - JSON inputs (if any) must be normalized before hashing (sorted keys,
     UTF-8, no NaNs).

2. **Hash domains**
   - Every cryptographic hash includes an explicit **domain tag** (e.g.,
     `b"animica.cap.task_id.v1"`, `b"animica.proof.nullifier.v1"`).
   - Chain-binding (`chainId`) and **height binding** are mandatory where noted.

3. **Size & shape guards**
   - `capabilities/runtime/determinism.py` enforces max lengths for all strings,
     arrays, and blobs; rejects non-finite numbers; clamps recursion and depth.

4. **Time independence**
   - Syscalls are **pure** at consensus boundaries: no wall-clock, network I/O,
     or nondeterministic sources are read during block execution.

---

## 3) Identities, receipts & deterministic IDs

### 3.1 `task_id` (idempotent job identity)

Jobs created by `ai_enqueue(...)` / `quantum_enqueue(...)` derive:

task_id = H(
“animica.cap.task_id.v1” |
chainId | height | txHash | callerAddress |
canonical(job_payload)    # model/prompt or circuit/params
)

- Collisions are computationally infeasible.
- **Cross-chain replay** is prevented by `chainId` inclusion.
- **Reorg safety:** `height` inclusion ensures tasks from orphaned blocks
  produce different IDs when re-enqueued at new heights.

### 3.2 JobReceipt

- Contains `task_id`, reserved units/amount, expiry (TTL), and a digest of the
  canonical payload.  Receipts are **verifiable** client-side and **replay-safe**
  server-side (queue dedupe on `task_id`).

---

## 4) Proof nullifiers & replay protection

Each proof kind (HashShare, AI, Quantum, Storage, VDF) defines a **nullifier**
computed over its canonical body and **validity window** (e.g., round/epoch):

nullifier = H(
“animica.proof.nullifier.v1” |
proof_type_id | window_id | canonical(proof_body)
)

**Rules:**
- Nullifiers are **single-use** within their window; nodes persist a TTL set
  to reject duplicates (see `proofs/nullifiers.py` and consensus nullifier store).
- Proof envelopes bind to the **header template** (height/roots/nonces) to
  prevent relocation across blocks.
- **Cross-network** replay is prevented by the chain binding inside the proof
  body or its referenced header hash.

**Result replay**: `capabilities/jobs/resolver.py` records a one-time linkage
`(task_id → proof)`; attempts to attach a second proof to the same `task_id`
are rejected as duplicates.

---

## 5) Escrow, charging, refunds (abuse-resistant)

- **Reserve at enqueue**: caller-funded mode requires sufficient balance;
  otherwise syscall reverts. Treasury-funded / hybrid modes are policy-gated.
- **Upper bounds**: reserve is capped by `policy.reserve_cap` and by payload
  size limits; prevents “infinite reservation” griefing.
- **Pay-as-proven**: charge **actual** priced units when a proof is accepted.
  Excess reserve is refunded. Missing/expired jobs refund per policy.
- **No double-charge**: `task_id` idempotency + resolver linkage ensure at most
  one debit path per job.

---

## 6) Reorg handling & idempotency

- **Derived state only**: AICF ledgers, result stores, and queue positions are
  reconstructed from canonical blocks. On reorg, derived writes are rolled back
  and re-applied via deterministic replay.
- **Idempotent keys**:
  - Job resolution: `(task_id, proof_nullifier, block_hash)`
  - Settlement batches: `(epoch_id, merkle_root_of_payouts)`
- **Read-result determinism**: by policy, a contract may read a job result **no
  earlier than the next block** after resolution. If the resolution block is
  orphaned, the read is not available (and will not appear in the new chain).

---

## 7) Input validation & content binding

- **AI / Quantum** jobs:
  - Payload digest is stored in the queue and echoed into proof metadata so the
    final proof binds **exactly** the requested work (model/hash, circuit hash,
    params, prompt digest).
  - TEE/QPU attestations are verified against vendor roots (pinned PEMs) and
    policy-allowed measurements/algorithms. Any mismatch → reject.
- **Large outputs**:
  - Must be summarized by digest and optionally **pinned to DA**. Only the
    digest participates in consensus.

---

## 8) DoS & resource controls

- **RPC**: Per-route **token buckets** and global ingress caps; rejects large
  payloads early with clear status codes.
- **Queue**: Max outstanding jobs per caller, max total queue length, and
  back-pressure on admission (`capabilities/jobs/queue.py`).
- **CPU bound checks**: Prioritize cheap, constant-time checks first:
  shape/size → canonicalization → hash/digest → signature/attestation → heavy
  cryptography last. zk.verify has configured cost caps.
- **DA endpoints**: rate-limited POST/GET with optional API keys; proof requests
  are cached and LRU-bounded.

---

## 9) Privacy notes

- Prompts/circuits may be sensitive. The baseline dev/test networks offer **no
  confidentiality** beyond transport encryption. Production networks should
  prefer **TEE-sealed payloads** (payload encrypted to provider measurement)
  with deterministic transcript hashes that still allow consensus verification.

---

## 10) Monitoring, audit, and forensics

Emit structured logs and Prometheus labels for:
- Enqueue/drop reasons (caller, size, policy code)
- Resolver events (task_id, proof_nullifier, block_height)
- Settlement lines (epoch_id, provider_id, amount, split)
- DA pins (commitment, namespace, size)
- Rate-limit counters and rejection tallies

Logs must include **idempotency keys** for replay diagnostics.

---

## 11) Policy & upgrade safety

- All consensus-affecting knobs (price schedule, splits, reserve caps, SLA
  thresholds, allowed algorithms/measurements, vendor roots digests) are
  committed via **policy roots** in headers. Nodes reject headers whose policy
  roots they don’t recognize.
- Root rotations use **versioned trees** and grace windows; attest verifiers
  consult the active policy at the proof’s height.

---

## 12) Developer checklist

- [ ] Hash domain tags present on every digest / nullifier / id.
- [ ] ChainId and (where relevant) height included in identities.
- [ ] Deterministic CBOR used end-to-end; reject non-canonical encodings.
- [ ] All inputs length-bounded; recursion/array depth clamped.
- [ ] Queue admission enforces per-caller and global limits.
- [ ] Resolver writes are idempotent; reorg replay is safe.
- [ ] DA usage pins **digests**; consensus never depends on off-chain fetch.
- [ ] Attestation chains verified against **pinned** vendor roots per policy.
- [ ] Tests cover replay/nullifier reuse, reorgs, and economic caps.

---

## 13) Example pseudocode fragments

**Duplicate job guard (enqueue):**
```python
if queue.exists(task_id):
    return queue.get_receipt(task_id)  # idempotent
queue.admit(task_id, payload_digest, caller, reserve_amount)

Proof resolution (link once):

if resolver.has_link(task_id):
    raise DuplicateResolution
resolver.link(task_id, proof_nullifier, block_hash)
result_store.put(task_id, result_digest, height=block_height)
escrow.charge_or_refund(task_id, actual_amount)

Nullifier set (TTL):

if nullifier in nullifier_set:
    reject("NullifierReuse")
nullifier_set.add(nullifier, ttl=policy.nullifier_ttl)


⸻

Status: Normative for devnet/testnet. Mainnet may add stricter limits
(privacy-preserving payload seals, stricter DA quotas, SLA-driven payouts).
