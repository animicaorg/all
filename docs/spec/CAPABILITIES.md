# Capabilities — Syscalls ABI (blob / compute / zk / random / treasury)

This document specifies the **contract-facing ABI** for capability syscalls. Contracts are written for the Python-VM (see `vm_py/stdlib/syscalls.py`) and call into a deterministic host that exposes the following primitives:

- **Blob**: `blob_pin(ns: u16, data: bytes) -> Commitment`
- **Compute**: `ai_enqueue(model: str, prompt: bytes) -> JobReceipt`  
                 `quantum_enqueue(circuit: bytes, shots: u32) -> JobReceipt`
- **Results**: `read_result(task_id: Hash32) -> Option<ResultRecord>`
- **ZK**: `zk_verify(circuit_id: str, proof: bytes, public_input: bytes) -> bool`
- **Random**: `random(n: u32) -> bytes`
- **Treasury (host hooks)**: fee debits/credits when capabilities consume funds (no direct syscall)

These calls are **deterministic** at consensus: enqueue happens in block *B*; the result (if produced) is **consumed from block B+1 onward** via `read_result`. ZK verification is fully in-block deterministic.

Reference implementations:
- Host: `capabilities/host/*`, queue core: `capabilities/jobs/*`
- Encodings: `capabilities/schemas/*`, CBOR codec: `capabilities/cbor/codec.py`
- Integration bridges: `capabilities/adapters/*`, on-chain proofs: `proofs/*`
- Execution binding: `capabilities/runtime/abi_bindings.py`
- AICF orchestrator: `aicf/*`

---

## 1. Determinism & lifecycle

**Enqueue** → **(off-chain compute)** → **Prove** → **Result resolve** → **Read next block**

- A syscall that enqueues work returns a **`JobReceipt`** (deterministic bytes) that includes `task_id`.
- `task_id = H(chainId | height | txHash | caller | payload)` (see `capabilities/jobs/id.py`).
- A result may only be **first read** in the block after the enqueue (B+1). Earlier reads return `None`.
- Resolution is driven by normal block processing: when a block includes a corresponding **ProofEnvelope** (e.g., `AIProof`), the host **injects** a `ResultRecord` keyed by `task_id` into the local cache/state for consumption.
- Reads are **idempotent** and **read-only**; results are prunable per retention policy.

---

## 2. Types & encoding

Canonical encodings are CBOR per CDDL schemas:

- `capabilities/schemas/job_request.cddl`
- `capabilities/schemas/job_receipt.cddl`
- `capabilities/schemas/result_record.cddl`
- `capabilities/schemas/syscalls_abi.schema.json` (JSON form for docs/SDKs)
- `capabilities/schemas/zk_verify.schema.json`

### 2.1 Common scalars

| Name          | Type             | Notes                                           |
|---------------|------------------|--------------------------------------------------|
| `Hash32`      | bytes(32)        | SHA3-256                                      |
| `Commitment`  | bytes(32)        | NMT root of blob (DA)                          |
| `Address`     | bytes(32) / bech | PQ bech32m externally; VM uses 32-byte payload  |
| `u16/u32/u64` | unsigned ints    | CBOR major type 0                               |

### 2.2 Domain separation (CBOR tags)

All host-encoded records are tagged with **explicit CBOR semantic tags**:

- `0x51` = `JobRequest`
- `0x52` = `JobReceipt`
- `0x53` = `ResultRecord`

Implementations MUST ignore unknown fields and MUST NOT reorder map keys (canonical order).

---

## 3. Syscalls

### 3.1 Blob

**Signature**  
`blob_pin(ns: u16, data: bytes) -> Commitment`

- Splits `data` into shares; computes **NMT root** and stores under namespace `ns`.
- Returns the commitment (NMT root). Inclusion & availability are handled by `da/`.

**Errors**
- `CapError.InvalidNamespace`
- `CapError.SizeExceeded` (see §6 limits)

**Example (JSON view)**
```json
{
  "ns": 24,
  "data_hex": "0x68656c6c6f...",
  "commitment": "0x2a0e...f9"
}


⸻

3.2 Compute (AI / Quantum)

Signatures
ai_enqueue(model: string, prompt: bytes) -> JobReceipt
quantum_enqueue(circuit: bytes, shots: u32) -> JobReceipt

Determinism
Returns a JobReceipt with:

{
  "task_id": "0x...32bytes",
  "kind": "AI" | "Quantum",
  "requested_at": {"height": 12345},
  "units": {"ai": 512}  // or {"quantum": {"depth": 18, "width": 24, "shots": 256}}
}

	•	The payload (model+prompt / circuit+shots) is hashed into task_id.
	•	Providers run off-chain; a future block includes AIProof/QuantumProof, which the host maps to a ResultRecord:

{
  "task_id": "0x...",
  "status": "OK",
  "output_digest": "0x...32",
  "bytes": 12345,             // optional
  "attest": { "qos": 0.98, "traps_ratio": 0.02 }
}

Errors
	•	CapError.LimitExceeded (size/units)
	•	CapError.NotDeterministic (payload sanitization failed)
	•	CapError.AttestationError (provider identity mismatch — during resolve)

⸻

3.3 Results

Signature
read_result(task_id: Hash32) -> Option<ResultRecord>
	•	Returns null if called in the same block as enqueue or if unresolved/expired.
	•	Returns the immutable ResultRecord once a valid proof is observed.

Errors
	•	None (returns null for absence). Policy failures surface at proof ingestion time.

⸻

3.4 ZK verification

Signature
zk_verify(circuit_id: string, proof: bytes, public_input: bytes) -> bool
	•	Checks proof using the pinned verifying key from zk/registry/*.
	•	Supports Groth16 (BN254), PLONK(KZG), and toy STARK (FRI) per zk/verifiers/*.
	•	public_input is the canonical ABI encoding expected by the circuit.
	•	Gas depends on scheme & input size (see §5).

Errors
	•	Returns False on verification failure. Hard errors:
	•	CapError.PolicyDenied (circuit not allowed)
	•	CapError.SizeExceeded (proof/PI over limit)
	•	CapError.VerifyBackendUnavailable (should be rare; handled as revert)

⸻

3.5 Random

Signature
random(n: u32) -> bytes
	•	Deterministic stub in local mode: PRNG seeded from tx hash and call index.
	•	On networks with a beacon, host may mix beacon output (documented in randomness/), but the transcript is bound so all nodes derive the same bytes for the same block & call.

Errors
	•	CapError.LimitExceeded if n exceeds per-call cap.

⸻

4. Treasury hooks

There is no direct VM syscall for treasury; instead, the host:
	•	Debits the caller (or a provided allowance) when enqueueing compute (AI/Quantum).
	•	Credits providers via AICF settlement off-chain, recorded on-chain per policy.
	•	Fees & splits configured in aicf/config.py and surfaced via capabilities/specs/TREASURY.md.

Contracts can still use the VM stdlib.treasury.transfer() for on-ledger moves.

⸻

5. Gas & metering

Call	Gas base	Gas per-byte / unit	Notes
blob_pin	G_blob	G_blob_byte * len(data)	Plus DA posting fee off-ledger
ai_enqueue	G_ai_base	G_ai_unit * units.ai	Units derive from model/prompt size
quantum_enqueue	G_q_base	G_q_unit * (depth*width*shots)	Rounded to policy table
read_result	G_read	0	Read-only
zk_verify	scheme base	Groth16: G_pair * #pairs; PLONK: MSM	Tuned in capabilities/specs/COMPUTE.md
random	G_rand	G_rand_byte * n	

Exact constants are network parameters; see spec/params.yaml and capabilities/specs/SYSCALLS.md.

⸻

6. Limits & policy

Policy is enforced by zk/integration/policy.py and capabilities/config.py.
	•	Max blob size per call (e.g., 1 MiB)
	•	Max prompt/circuit sizes and unit caps per tx & per block
	•	Allowlist of circuit_id for zk_verify
	•	Per-tx and per-block enqueue quotas (DoS resistance)
	•	Retention: result records prunable after N blocks

Violations yield CapError.LimitExceeded or CapError.PolicyDenied.

⸻

7. Error model

CapError codes (subset):
	•	NotDeterministic
	•	LimitExceeded
	•	InvalidNamespace
	•	PolicyDenied
	•	AttestationError
	•	VerifyBackendUnavailable
	•	NoResultYet (mapped to None at ABI surface)

VM sees a raised revert for hard errors; soft absence returns None (results).

⸻

8. Security considerations
	•	Determinism boundary: No non-deterministic output is available in the same block as enqueue.
	•	Task ID binding: task_id binds chain, height, tx, caller, and sanitized payload.
	•	Attestation: Providers must satisfy identity & quote checks (see proofs/attestations/*).
	•	Replay protection: Nullifiers in proofs/nullifiers.py prevent proof replay across blocks.
	•	ZK malleability: VKs are pinned & hashed in zk/registry/vk_cache.json; circuit allowlist reduces risk.

⸻

9. Examples

9.1 AI enqueue → next-block read

# VM Python (contract)
from stdlib import syscalls, abi

receipt = syscalls.ai_enqueue("gpt-mini", b"sum:1,2,3")
# ... next block ...
res = syscalls.read_result(receipt.task_id)
abi.require(res is not None, b"no result yet")
# Optionally verify output digest if circuit provides one

9.2 Blob pin + verify availability off-chain

# VM Python (contract)
commit = syscalls.blob_pin(24, b"\x00"*4096)
# Clients may fetch via DA endpoints and present proofs to users

9.3 ZK verify (Groth16)

ok = syscalls.zk_verify("zkml/embedding@1", proof_bytes, public_input_bytes)
abi.require(ok, b"zk verify failed")


⸻

10. Test vectors
	•	capabilities/test_vectors/enqueue_and_read.json
	•	da/test_vectors/* for blob/NMT roots
	•	zk/tests/* for Groth16/PLONK/STARK verification

All vectors are round-trippable with canonical encoders; see tests under capabilities/tests/.

⸻

11. Wire-up diagram

Contract → (VM stdlib) → Host syscalls ─┬─▶ DA (blob)
                                        ├─▶ Queue (AI/Quantum) → AICF → Proofs → ResultResolver
                                        ├─▶ ZK verifiers (Groth16/PLONK/STARK)
                                        └─▶ Random (beacon-mixed, deterministic)


⸻

12. References
	•	capabilities/specs/SYSCALLS.md — deeper ABI tables & gas constants
	•	zk/docs/FORMATS.md — proof formats and envelope mapping
	•	randomness/specs/* — beacon protocol
	•	da/specs/* — NMT & availability
	•	aicf/specs/* — economics, SLA, settlement
