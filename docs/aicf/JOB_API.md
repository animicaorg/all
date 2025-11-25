# AICF Job API — Schemas, Statuses, Receipts & Proofs

This document specifies the **contract-facing** and **operator-facing** shapes used by the
AI Compute Fund (AICF) job pipeline:

- Deterministic **Job IDs** and **Receipts** emitted when enqueuing via the VM capabilities
- **Job records** and **status** transitions in the AICF queue/matcher
- **Result records** returned to contracts/clients
- **Proof claims** that connect on-chain proofs to jobs for settlement

Where applicable, objects are encoded as **deterministic CBOR** on the wire (see
`docs/spec/ENCODING.md`) and mirrored as JSON in RPC/CLI views. Typed Python structures
are implemented with `msgspec` under `capabilities/jobs/types.py` and `aicf/types/*`.

---

## 0) Notation & Versioning

- `bytes` shown in JSON examples are hex-prefixed strings (`0x…`), while CBOR uses raw bytes.
- All records include a `schema_version` (`u16`) for forward-compat.
- Deterministic hashing uses **SHA3-256** unless stated otherwise.

---

## 1) Deterministic Job ID & Receipt

### 1.1 `task_id` Derivation

The **task id** binds the request to the chain context and payload:

task_id = SHA3-256(
domain(“AICF_TASK_ID”) ||
uvarint(chainId) ||
uvarint(enqueue_block_height) ||
tx_hash ||
caller_address ||
canonical_cbor(job_payload)        # fields below
)

- `enqueue_block_height` is the block height where the enqueue call is executed.
- The **same inputs** always produce the same `task_id`. Any difference (height, caller, payload)
  yields a new id. See `capabilities/jobs/id.py`.

### 1.2 Job Payload (capabilities ABI)

Two primary kinds:

- **AI** (`JobSpecAI`)
- **Quantum** (`JobSpecQuantum`)

#### CDDL (capabilities/schemas/job_request.cddl)
```cddl
; Common
TaskId        = bstr .size 32
Address       = bstr .size 32
ModelName     = tstr
Bytes         = bstr
U32           = uint .le 4
U64           = uint .le 8

JobKind = &( ai: 0, quantum: 1 )

JobRequest = {
  schema_version: 0,
  kind: JobKind,
  caller: Address,
  max_fee_u64: U64,
  payload: any   ; JobSpecAI or JobSpecQuantum
}

JobSpecAI = {
  model: ModelName,
  prompt: Bytes,
  max_tokens: U32,
  temperature: float16 / float32,
  qos_hint_ms: U32,        ; optional in CBOR (0 if absent)
}

JobSpecQuantum = {
  circuit: Bytes,          ; serialized IR or provider-native format
  shots: U32,
  depth_hint: U32,         ; optional (0 if absent)
}

1.3 JobReceipt

Returned synchronously to the contract (and emitted in logs) by the capabilities provider.

{
  "schema_version": 0,
  "task_id": "0x2f1d…9c41",
  "enqueue_block": 123456,
  "caller": "0x7ab3…f9c0",
  "kind": "ai",
  "max_fee_u64": 2500000,
  "accepted": true
}

The receipt is also persisted to the AICF queue with initial status = QUEUED.

⸻

2) Job Record (Queue/Matcher)

2.1 Shape (aicf/types/job.py → JobRecord)

{
  "schema_version": 0,
  "task_id": "0x2f1d…9c41",
  "kind": "ai",                         // "ai" | "quantum"
  "request": {
    "caller": "0x7ab3…f9c0",
    "max_fee_u64": 2500000,
    "payload": {
      "model": "llama3-8b",
      "prompt": "0x…",
      "max_tokens": 256,
      "temperature": 0.7,
      "qos_hint_ms": 500
    }
  },
  "priority_score": 0.823,              // internal
  "status": "QUEUED",
  "timestamps": {
    "enqueued": 1713200000,
    "assigned": null,
    "started":  null,
    "completed": null,
    "failed": null,
    "expired": null
  },
  "lease": null,                        // populated on assignment
  "provider_id": null,                  // set when assigned
  "retries": 0
}

2.2 Lease (Lease)

{
  "lease_id": "0x5fe2…77aa",
  "provider_id": "provider:abc123",
  "issued_at": 1713200123,
  "ttl_seconds": 600,
  "renewals": 0,
  "max_renewals": 6
}

The provider must heartbeat to renew the lease while running the job. When a lease
expires, the job returns to the queue (QUEUED) with retries += 1.

⸻

3) Job Status & State Machine

3.1 Status Enum (JobStatus)
	•	QUEUED
	•	ASSIGNED
	•	RUNNING
	•	COMPLETED
	•	FAILED
	•	EXPIRED
	•	CANCELED

3.2 Transitions

QUEUED → ASSIGNED → RUNNING → COMPLETED
   │        │          │          │
   │        │          └────→ FAILED (proof invalid / provider error)
   │        └────→ EXPIRED (lease timeout; requeue if retries left)
   └────→ CANCELED (admin/test scenarios only)

Invariants
	•	Only COMPLETED jobs produce settleable units/payouts.
	•	FAILED/EXPIRED/CANCELED never settle; they may be requeued subject to policy.

⸻

4) Result Record

Written by the result resolver once a block containing a valid proof is finalized.

{
  "schema_version": 0,
  "task_id": "0x2f1d…9c41",
  "kind": "ai",
  "output_digest": "0xd4b1…00a7",     // provider-returned digest hashed canonically
  "units": 3.25,                       // computed via pricing schedule
  "qos": { "p95_latency_ms": 420, "availability": 0.9993 },
  "proof_refs": [
    {
      "proof_type": "AI_V1",           // or "QUANTUM_V1"
      "proof_hash": "0x8a9c…ee12",     // SHA3-256 of canonical envelope
      "nullifier": "0x19ff…00cc",
      "block_height": 123457,
      "tx_hash": "0x6a5e…19bf"
    }
  ],
  "finalized_height": 123458
}

Contracts can load the result via the VM syscall:

read_result(task_id: bytes) -> ResultRecord?  ; null if not yet resolved

RPC (read-only):
	•	aicf.getJob(task_id) → includes job + result if present
	•	aicf.listJobs(filters…)
	•	aicf.getResult(task_id)

⸻

5) Proofs & Claims

A ProofEnvelope (see docs/spec/proofs/ENVELOPE.md) included in a block conveys
verified work. AICF links such proofs to jobs via a ProofClaim:

5.1 ProofClaim (aicf/types/proof_claim.py)

{
  "schema_version": 0,
  "task_id": "0x2f1d…9c41",
  "proof_type": "AI_V1",               // or "QUANTUM_V1"
  "envelope_hash": "0x8a9c…ee12",      // SHA3-256(envelope CBOR)
  "nullifier": "0x19ff…00cc",
  "metrics": {
    "ai_units": 3.25,                  // or quantum_units, traps_ratio, qos…
    "qos": { "p95_latency_ms": 420, "availability": 0.9993 }
  },
  "block_height": 123457,
  "proof_idx_in_block": 2              // optional (for explorers)
}

Constraints
	•	The envelope must pass consensus verification (TEE/QPU attestations, traps/QoS checks).
	•	nullifier enforces one-time claim semantics (prevents replay/reuse).
	•	The resolver must validate that task_id in the claim matches the
deterministic id derived from the original request payload.

⸻

6) Pricing, Units, and Settlement

From ResultRecord + ProofClaim.metrics, the settlement engine computes:

reward = units * base_rate(kind, model/circuit, epoch) * multipliers(qos, depth, …)

Then applies the treasury split (provider/miner/fund) at epoch close, subject to
Γ_fund caps. See docs/aicf/OVERVIEW.md (§4) & aicf/economics/*.

⸻

7) Size Limits & Determinism Guards

Caps enforced in capabilities/runtime/determinism.py:
	•	prompt (AI): e.g. ≤ 64 KiB
	•	circuit (Quantum): e.g. ≤ 128 KiB
	•	max_tokens: sane upper-bounds
	•	ABI rejects non-deterministic or excessively large inputs; returns LimitExceeded.

⸻

8) Error Codes (subset)

Code	Context	Notes
NoResultYet	read_result	Result not produced/finalized
LimitExceeded	enqueue	Input sizes, tokens caps
AttestationError	proof verify	SGX/SEV/CCA/QPU evidence invalid
JobExpired	lease/matcher	TTL exceeded without renew
InsufficientStake	provider registry	Cannot accept lease
QueueOverCapacity	enqueue	Backpressure; retry/backoff

See capabilities/errors.py, aicf/errors.py.

⸻

9) RPC Shapes (Operator Views)

9.1 aicf.getJob

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "aicf.getJob",
  "params": [{ "task_id": "0x2f1d…9c41" }]
}

Result

{
  "task_id": "0x2f1d…9c41",
  "status": "COMPLETED",
  "job": { /* JobRecord sans internals */ },
  "result": { /* ResultRecord */ }
}

9.2 aicf.listJobs

Supports filters: status, provider_id, caller, kind, after_height, pagination.

⸻

10) Examples

10.1 AI Enqueue → Completed
	1.	Contract calls ai_enqueue("llama3-8b", prompt=…, max_tokens=256).
	2.	Receipt:

{"task_id":"0x2f1d…9c41","accepted":true,"enqueue_block":123456,"kind":"ai"}

	3.	Provider runs & posts AI_V1 proof in block 123457.
	4.	Resolver links proof → result; job COMPLETED.
	5.	read_result(task_id) returns the ResultRecord.

10.2 Quantum Enqueue → Failed
	•	Proof verification fails (e.g., trap ratio below threshold) → job FAILED.
	•	No units settle; retries may requeue depending on policy.

⸻

11) Compliance & Auditing
	•	Every payout references:
	•	task_id, provider_id, proof_hash/nullifier, units, rate, split.
	•	A rolling audit log (append-only) is maintained for queue events and settlements.
	•	VK/attestation roots are pinned (see proofs/attestations/vendor_roots/*, zk/registry/*).

⸻

12) References
	•	capabilities/schemas/*.cddl — ABI & job/result wire shapes
	•	capabilities/jobs/* — ids, queue, receipts, result store
	•	aicf/types/*, aicf/queue/*, aicf/economics/*, aicf/rpc/*
	•	docs/spec/proofs/*, docs/aicf/OVERVIEW.md

