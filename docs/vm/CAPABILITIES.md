# VM(Py) Contract Capabilities — Syscalls (AI / Quantum / DA / Random / ZK)

**Status:** Stable (v1)  
**Audience:** Contract authors, reviewers, runtime maintainers  
**Source of truth:** `capabilities/host/*`, `capabilities/runtime/*`, `docs/spec/CAPABILITIES.md`, `docs/vm/SANDBOX.md`

This document specifies the **contract-facing syscall surface** exposed by the VM(Py) sandbox. All calls are:

- **Deterministic** at the interface boundary (fixed shapes; size caps; canonical encodings).
- **Gas-charged** with a **base fee + size components**; some calls also debit/credit via the treasury hooks.
- **Bounded** by policy (max input sizes, per-block quotas, rate limits).
- **Asynchronous by design** for off-chain compute: contracts **enqueue now** and **consume results next block**.

> The VM accepts only imports from the allowlisted `stdlib` (see `docs/vm/SANDBOX.md`). Capability syscalls live under `stdlib.syscalls` and related helpers.

---

## 0) Common Semantics

### Deterministic Task IDs
For off-chain compute (AI/Quantum), a **task_id** is derived deterministically:

task_id = H(chainId | block_height | tx_hash | caller_address | syscall_payload)

- `H` = SHA3-256 with a domain tag.
- Same inputs ⇒ same `task_id`; different height/tx ⇒ different `task_id`.

### Result Lifecycle
1. **Enqueue**: contract calls `ai_enqueue(...)` or `quantum_enqueue(...)`. A **receipt** is recorded off-chain; the `task_id` is returned.
2. **Proof resolution**: providers return attested outputs in a later block; results are normalized into **Proofs** and applied to a **result store** by the host (see `capabilities/jobs/resolver.py`).
3. **Consume**: a contract can call `read_result(task_id)` in a subsequent block to fetch a **deterministic, immutable** result record.

### Errors (subset)
These map to `capabilities/errors.py`:
- `NotDeterministic` — payload violates determinism rules (e.g., non-canonical or over-sized).
- `LimitExceeded` — input length or per-block quota exceeded.
- `NoResultYet` — `read_result` called before result availability.
- `AttestationError` — provider attestation invalid or proof mismatch.

Errors surface as **reverts** with canonical codes (see §7).

---

## 1) Data Availability (DA)

**Purpose:** Commit content-addressed blobs to the DA layer and obtain a commitment (NMT root).

**Signature**
```python
from stdlib import syscalls

def blob_pin(ns: int, data: bytes) -> bytes:  # returns 32-byte commitment
    ...

Determinism & Limits
	•	ns (namespace) must be in the chain’s allowed range (see chain params).
	•	data size is capped by policy (e.g., ≤ 1 MiB in devnet; consult capabilities/config.py).
	•	Commitment returned is the NMT root (32 bytes) as canonical bytes.

Gas & Fees
	•	Gas = BASE_DA + α * len(data).
	•	Optional treasury debit for persistence tiers.

Notes
	•	Retrieval is off-chain via the DA client; contracts operate on commitments and may store them for later verification or linking.

Example

from stdlib import syscalls, storage, abi

def save_doc(ns: int, b: bytes):
    abi.require(len(b) <= 1_000_000, b"too big")
    commit = syscalls.blob_pin(ns, b)
    storage.set(b"doc_commit", commit)


⸻

2) AI Compute (AICF)

Purpose: Enqueue AI work to attested providers; consume results next block.

Signatures

from stdlib import syscalls

def ai_enqueue(model: bytes, prompt: bytes, opts: bytes=b"") -> bytes:  # task_id (32B)
    ...

def read_result(task_id: bytes) -> tuple[bool, bytes]:  # (ready, digest_or_err)
    ...

Payload Rules
	•	model: identifier or policy-keyed tag (e.g., b"animica/text-embed-1").
	•	prompt: raw bytes (UTF-8 text or binary), size-capped.
	•	opts: optional CBOR/ABI-encoded params (deterministic, bounded).
	•	All inputs are opaque bytes at the VM boundary; the host validates schema.

Result & Proofs
	•	read_result returns:
	•	ready = True and digest (hash of output) when finalized and provider’s attestation validated to a chain-accepted proof.
	•	Otherwise ready = False with a short diagnostic or empty bytes.

Gas & Fees
	•	Enqueue gas = BASE_AI + β * (len(model)+len(prompt)+len(opts)).
	•	Optional compute fee escrow (debited from caller) recorded by treasury hooks; payout occurs after proof settlement (see AICF docs).

Example

from stdlib import syscalls, abi, storage

def request_embed(msg: bytes) -> bytes:
    abi.require(len(msg) <= 4096, b"prompt too large")
    tid = syscalls.ai_enqueue(b"animica/text-embed-1", msg)
    storage.set(b"last_ai_task", tid)
    return tid

def consume_embed() -> bytes:
    tid = storage.get(b"last_ai_task")
    ready, digest = syscalls.read_result(tid)
    abi.require(ready, b"no result yet")
    return digest


⸻

3) Quantum Compute

Purpose: Enqueue quantum jobs with trap-based sanity checks and attestation.

Signatures

from stdlib import syscalls

def quantum_enqueue(circuit: bytes, shots: int, opts: bytes=b"") -> bytes:  # task_id
    ...

def read_result(task_id: bytes) -> tuple[bool, bytes]:
    ...

Payload Rules
	•	circuit: deterministic encoding (e.g., OpenQASM subset or chain-defined IR), bounded size.
	•	shots: bounded integer (policy).
	•	opts: deterministic options (e.g., layout seeds), bounded.

Attestation
	•	Provider identity & environment attested; traps outcomes verified. Host maps to QuantumProof inputs for consensus.

Gas & Fees
	•	Similar to AI: BASE_Q + γ * (len(circuit)+len(opts)).

⸻

4) Randomness

Purpose: Access the chain’s beacon (commit–reveal + VDF; optional QRNG mix), and use a deterministic local PRNG.

Signatures

from stdlib import syscalls, random

def rand_beacon(round_id: int|None=None) -> bytes:  # 32B beacon output
    ...

def randbytes(n: int) -> bytes:  # deterministic per-call PRNG (see SANDBOX)
    return random.randbytes(n)

Semantics
	•	rand_beacon(None) returns the latest finalized beacon output (32 bytes).
	•	Specific round_id must be ≤ current; future rounds are rejected.
	•	randbytes(n) is local deterministic (seeded from tx-hash & call index); never use for protocol beacons.

Gas
	•	rand_beacon: small constant.
	•	randbytes: BASE_RAND + δ * n.

⸻

5) Zero-Knowledge Verification (ZK)

Purpose: Verify ZK proofs (Groth16/PLONK/STARK) against pinned verifying keys and policy.

Signature

from stdlib import syscalls

def zk_verify(circuit_id: bytes, proof: bytes, public_inputs: bytes) -> tuple[bool, int]:
    """Returns (ok, units) where units measure verification cost for accounting."""
    ...

Determinism & Policy
	•	circuit_id must be allowlisted (see zk/integration/policy.py).
	•	Verifying key (VK) pinned by hash (see zk/registry/vk_cache.json); changes require governance.
	•	public_inputs must match the ABI for the circuit; strictly bounded.

Gas & Units
	•	Gas is proportional to proof size and circuit-specified operations.
	•	units reflect normalized verifier work (used for treasury or metering; OK to ignore in simple contracts).

Example

from stdlib import syscalls, abi

def verify_membership(proof: bytes, pub: bytes) -> bool:
    ok, units = syscalls.zk_verify(b"merkle-membership-v1", proof, pub)
    abi.require(ok, b"invalid proof")
    return True


⸻

6) Encoding & ABI

All syscall parameters are ABI/CBOR canonicalized by the runtime. Contracts pass and receive bytes and small scalars only; complex shapes must be encoded deterministically by the caller and decoded off-chain as needed. This prevents hidden interpreter variance and ensures reproducible SignBytes.

⸻

7) Error Model

Syscalls revert with a canonical code + message:
	•	b"cap:not_deterministic" — payload fails determinism checks.
	•	b"cap:limit_exceeded" — size/quota exceeded.
	•	b"cap:no_result_yet" — result not available.
	•	b"cap:attestation_error" — provider evidence invalid.
	•	b"cap:policy_violation" — circuit not allowlisted / namespace forbidden.

Contracts should bubble these as ABI reverts or map to domain errors.

Example:

from stdlib import syscalls, abi

def try_consume(tid: bytes):
    ready, out = syscalls.read_result(tid)
    abi.require(ready, b"cap:no_result_yet")
    return out


⸻

8) Gas & Treasury Interactions
	•	Each syscall has a BASE gas cost plus per-byte increments.
	•	Off-chain compute may require escrowed fees; treasury hooks debit at enqueue and credit providers at settlement.
	•	Gas tables and fee schedules are versioned; see execution/gas/table.py and capabilities/specs/TREASURY.md.

⸻

9) Security Notes
	•	Inputs are length-capped and schema-checked to prevent DoS (CPU/memory).
	•	Results are immutable once finalized and referenced by task_id.
	•	DA commitments are content-addressed; contracts must treat them as opaque and verify separately if needed.
	•	ZK verification uses pinned VKs and typed envelopes to avoid malleability.

⸻

10) Versioning & Compatibility
	•	Syscall shapes are semver-gated via capabilities/version.py.
	•	Adding new fields must remain strictly optional and length-bounded.
	•	Removing or retyping fields requires a major version bump and allowlist update.

⸻

11) Quick Reference (Cheat Sheet)

Category	Function	Returns	Notes
DA	blob_pin(ns, data)	commitment: bytes32	NMT root; size-capped.
AI	ai_enqueue(model, prompt, opts=b"")	task_id: bytes32	Enqueue; escrow may apply.
AI/Q	read_result(task_id)	(ready: bool, digest_or_err: bytes)	Deterministic, immutable on ready.
Quantum	quantum_enqueue(circuit, shots, opts=b"")	task_id: bytes32	Attested, trap-audited.
Random	rand_beacon(round_id=None)	bytes32	Latest or specific finalized round.
Random	random.randbytes(n)	bytes	Local deterministic PRNG.
ZK	zk_verify(circuit_id, proof, public_inputs)	(ok: bool, units: int)	VK pinned; allowlist enforced.


⸻

See also:
	•	docs/spec/CAPABILITIES.md — chain-level ABI and economics
	•	capabilities/specs/* — detailed specs (COMPUTE, SECURITY, RESULTS, TREASURY)
	•	docs/vm/SANDBOX.md — determinism rules & allowlist
