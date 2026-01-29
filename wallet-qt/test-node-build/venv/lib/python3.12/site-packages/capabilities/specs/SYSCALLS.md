# Capabilities — Contract-Facing Syscalls

**Scope.** This document specifies the deterministic syscall surface exposed to
Python-VM contracts via the `stdlib.syscalls` module. These calls let contracts
pin blobs to DA, enqueue AI/Quantum jobs, verify ZK proofs, read completed
results, and obtain a deterministic random byte stream. All calls are **purely
deterministic** from the chain’s point of view and are metered by **gas** plus
(optional) **capability units** charged via the Treasury hooks.

Authoritative schemas live in `capabilities/schemas/*`. Canonical CBOR encoding
rules live in `capabilities/cbor/codec.py`. Gas costs are defined in
`spec/opcodes_vm_py.yaml` and materialized in `vm_py/gas_table.json`.

---

## 1) Determinism model

A syscall **must** produce the same outcome on every node given the same block
and state:

- **No online I/O.** Inputs are fully provided by the transaction (or by prior
  block proofs recorded on-chain). No network calls or clocks.
- **Bounded inputs.** Sizes and nesting are capped by configuration
  (`capabilities/config.py`), enforced in `capabilities/runtime/determinism.py`.
- **Stable encoding.** Where structs are passed/returned, they are encoded using
  **canonical CBOR** (major/minor types + lexicographic map ordering).
- **Visibility window.** Enqueued compute jobs yield a **deterministic** `task_id`
  but their **result bytes are only readable** after a block finalizes the
  corresponding on-chain proof(s). Until then, `read_result` returns `NoResultYet`.
- **Error classes.** Syscalls fail with deterministic error codes from
  `capabilities/errors.py` (see §6).

---

## 2) Types & limits (contract-visible)

Common scalar limits are loaded from `capabilities/config.py`. Typical defaults
are shown as symbols here; the **configuration is normative**.

- `NamespaceId` — `u32` (0…`MAX_NAMESPACE_ID`)
- `Bytes` — length-capped byte string; limit depends on syscall:
  - `BLOB_PIN_MAX_BYTES`
  - `AI_PROMPT_MAX_BYTES`
  - `Q_CIRCUIT_MAX_BYTES`
  - `READ_RESULT_MAX_BYTES`
  - `ZK_INPUT_MAX_BYTES`, `ZK_PROOF_MAX_BYTES`, `ZK_CIRCUIT_MAX_BYTES`
- `Commitment` — 32-byte DA commitment (NMT root), hex (`0x…`) or raw 32 bytes
- `TaskId` — 32-byte deterministic id:

task_id = H(chainId | height | txHash | caller | payload_digest)

(see `capabilities/jobs/id.py`)
- `Units` — non-gas metering units for ZK/compute accounting (u64)

---

## 3) Syscall surface

Contract import:
```py
from stdlib import syscalls

3.1 blob_pin(ns: int, data: bytes) -> tuple[bytes, int]

Pins data under namespace ns into the DA subsystem and returns the
commitment and length.
	•	Args
	•	ns: NamespaceId (validated by da/nmt/namespace.py)
	•	data: Bytes (≤ BLOB_PIN_MAX_BYTES)
	•	Returns (commitment: bytes32, length: u64)
	•	commitment is the NMT root for the erasure-encoded, namespaced leaf set
	•	length is original payload length in bytes
	•	Gas
	•	G_base_blob_pin + G_per_byte_blob_pin * len(data)
	•	Notes
	•	Deterministically computes the same commitment as da/blob/commitment.py.
	•	The actual storage is handled by the DA service; inclusion proofs are
verified by light-clients using da/sampling/* outside the VM.

⸻

3.2 ai_enqueue(model: bytes, prompt: bytes, params: bytes | None = None) -> bytes

3.3 quantum_enqueue(circuit: bytes, shots: int, params: bytes | None = None) -> bytes

Enqueue a compute job and receive a CBOR-encoded JobReceipt (bytes)
containing at minimum {task_id, kind, payload_digest, reserved_units}.
	•	Args (AI)
	•	model: canonical model id (UTF-8) — bounded by AI_MODEL_ID_MAX_BYTES
	•	prompt: opaque bytes (≤ AI_PROMPT_MAX_BYTES)
	•	params: optional CBOR map for small knobs (≤ AI_PARAMS_MAX_BYTES)
	•	Args (Quantum)
	•	circuit: canonical JSON/CBOR of the trap circuit (≤ Q_CIRCUIT_MAX_BYTES)
	•	shots: u32 (≤ Q_MAX_SHOTS)
	•	params: optional CBOR map (≤ Q_PARAMS_MAX_BYTES)
	•	Returns
	•	receipt_cbor: bytes — canonical CBOR of JobReceipt (per
schemas/job_receipt.cddl), which includes a task_id: bytes32
	•	Gas
	•	G_base_enqueue_{ai|q} + G_per_byte_enqueue * (payload sizes)
	•	Treasury (deterministic reserve)
	•	A reserve of reserved_units is debited from the caller via
capabilities/host/treasury.py. Final settlement (refund/charge) occurs
when proofs land (see TREASURY.md).
	•	Notes
	•	The enqueued payload is normalized and hashed in the host to derive
task_id (see jobs/id.py); the normalization is deterministic and schema-checked
by capabilities/runtime/determinism.py and capabilities/cbor/codec.py.
	•	No result bytes are available at enqueue time (see §3.4).

⸻

3.4 read_result(task_id: bytes) -> bytes

Read the result for a completed job. Returns CBOR-encoded ResultRecord
(see schemas/result_record.cddl) or fails with NoResultYet.
	•	Args
	•	task_id: bytes32
	•	Returns
	•	result_cbor: bytes — canonical CBOR of:

{
  task_id,                      ; bytes32
  status: "ok" | "error",
  output: bytes?,               ; bounded by READ_RESULT_MAX_BYTES
  metrics: {units: u64, qos: u16, latency_ms: u32}?,
  proof_refs: any?,             ; implementation-defined, schema-bound
  error: {code: int, msg: tstr}? ; if status = "error"
}


	•	Gas
	•	G_base_read_result + G_per_byte_read * len(output)
	•	Determinism
	•	Visible only after a block finalizes with the corresponding proofs.
Otherwise raises NoResultYet.

⸻

3.5 zk_verify(circuit: bytes, proof: bytes, public_input: bytes) -> tuple[bool, int]

Verify a zero-knowledge proof against a circuit and input. Fully local and
deterministic.
	•	Args (bounded by ZK_*_MAX_BYTES)
	•	circuit, proof, public_input: canonicalized bytes as per
schemas/zk_verify.schema.json
	•	Returns (ok: bool, units: u64)
	•	units reflects costed verifier work for Treasury accounting
	•	Gas
	•	G_base_zk_verify + G_per_byte_zk * (len(circuit)+len(proof)+len(public_input))
	•	An additional success cost multiplier MAY apply as per gas table.

⸻

3.6 random(n_bytes: int) -> bytes

Returns a deterministic byte string of length n_bytes (≤ RAND_MAX_BYTES).
	•	Seed
	•	Derived from (chainId, height, txHash, caller); when the beacon is
available, a mixed seed is used deterministically (see
capabilities/adapters/randomness.py + randomness/beacon/*).
	•	Gas
	•	G_base_random + G_per_byte_random * n_bytes
	•	Notes
	•	Not suitable for gambling/lotteries on its own. Use beacon-based flows if
economic fairness is required.

⸻

4) ABI & encoding
	•	Contract calls are ordinary Python-VM calls. The VM shim (vm_py/runtime/syscalls_api.py)
validates sizes, normalizes payloads, and forwards to the host provider
(capabilities/host/provider.py) through runtime/abi_bindings.py.
	•	Complex return values (JobReceipt, ResultRecord) are CBOR blobs. This
prevents non-determinism in object layout and guarantees round-trip stability.
	•	All CBOR encoding must be canonical; the reference implementation is
capabilities/cbor/codec.py. Schemas:
	•	schemas/job_request.cddl
	•	schemas/job_receipt.cddl
	•	schemas/result_record.cddl
	•	schemas/zk_verify.schema.json

⸻

5) Gas & Treasury charging
	•	Gas is charged immediately by the VM per the gas table:
	•	Constants: G_base_*, G_per_byte_*, and any success multipliers are
specified in spec/opcodes_vm_py.yaml → vm_py/gas_table.json.
	•	Units (non-gas capability meters):
	•	enqueue returns reserved_units in the receipt and reserves that
amount from the caller via host/treasury.py (deterministic).
	•	zk_verify returns units actually consumed by verification; the host
posts deterministic debits to Treasury.
	•	Final settlement for compute jobs happens when proofs are consumed by
the chain (AICF integration). See capabilities/specs/TREASURY.md.

If gas/units are insufficient at call time, the syscall fails deterministically
with LimitExceeded or CapError and the VM reverts per normal semantics.

⸻

6) Errors

Syscalls raise VmError with a capabilities subtype. Contracts can catch
and turn them into ABI errors if desired. The following codes are reserved:

Code	Name	Meaning
1001	LimitExceeded	Byte/field size exceeds configured cap
1002	NotDeterministic	Input normalization failed (e.g., non-canonical JSON/CBOR)
1003	NoResultYet	read_result before the task has a finalized on-chain result
1004	AttestationError	Malformed or unacceptable attestation/proof reference
1005	TreasuryInsufficient	Insufficient balance for reserve/charge
1006	Unsupported	Model/circuit/algorithm not allowed by current policy
1099	CapError	Generic capability error (fallback)

The exact mapping is defined in capabilities/errors.py. Error messages are
short ASCII strings; no variable node data may be included.

⸻

7) End-to-end examples (illustrative)

7.1 Pin a small blob, then store commitment

from stdlib import syscalls, storage, events

def save_manifest(ns: int, manifest: bytes):
    commit, size = syscalls.blob_pin(ns, manifest)
    storage.set(b"last_manifest_commit", commit)
    events.emit(b"BlobPinned", {b"ns": ns, b"size": size, b"commit": commit})

7.2 Enqueue AI job and read result next block

from stdlib import syscalls, storage, abi

def ai_ask(model: bytes, prompt: bytes):
    receipt_cbor = syscalls.ai_enqueue(model, prompt, None)
    storage.set(b"last_task", receipt_cbor)   # caller can parse task_id off-chain

def get_last_result(task_id: bytes) -> bytes:
    try:
        result_cbor = syscalls.read_result(task_id)
        return result_cbor
    except Exception as e:
        abi.revert(b"NoResultYet")  # deterministic failure until resolved

7.3 Verify a ZK proof

from stdlib import syscalls, abi

def check(circ: bytes, proof: bytes, pub: bytes):
    ok, units = syscalls.zk_verify(circ, proof, pub)
    if not ok:
        abi.revert(b"ZK verify failed")


⸻

8) Conformance
	•	Unit tests under capabilities/tests/* must pass.
	•	Schema round-trip tests (codec + schemas) must pass.
	•	Gas table consistency checks in vm_py/tests/test_gas_estimator.py cover
syscall entries.

Changes to this API require:
	1.	Bumping capabilities/version.py.
	2.	Updating schemas and gas table entries.
	3.	Adding/adjusting tests and vectors.

