# VM(Py) Gas Model — Metering, Memory/IO, Op Caps

**Status:** Stable (v1)  
**Audience:** Contract authors, VM/runtime implementers, tooling/SDK maintainers  
**Source of truth:** `spec/opcodes_vm_py.yaml` → resolved into `vm_py/gas_table.json` at build time.  
**Implements:** `vm_py/runtime/gasmeter.py`, charged from `vm_py/runtime/engine.py` and stdlib APIs.

> TL;DR: Every IR instruction and stdlib call has a **deterministic** gas cost. Costs are fixed by table,
> linear-in-input-size where applicable, and enforced by a simple **GasMeter**. No hidden I/O or syscalls exist.

---

## 1) Design Goals

- **Determinism:** Same code + inputs ⇒ identical gas burn on all nodes.
- **Simplicity:** A small IR and a compact cost table (no dynamic pricing in VM).
- **Safety:** Hard caps on steps, memory, emitted bytes, and host-IO surfaces.
- **Composability:** Costs for host-bound capabilities (DA/AI/Quantum/ZK) are metered at the *call boundary*.

---

## 2) Units & Metering

- **Unit:** `gas` (integer). No sub-units.
- **Budget:** Each transaction carries `gasLimit` and `gasPrice`. The VM receives a **per-call** budget from the executor.
- **Meter:** `GasMeter.debit(amount)` throws `OOG` (Out-Of-Gas) if balance < amount; charges are **non-refundable** except where explicitly noted (e.g., certain cleanups).

Pseudocode:
```python
class GasMeter:
    def __init__(self, limit: int):
        self.limit = limit
        self.used  = 0
    def debit(self, amount: int):
        new = self.used + amount
        if new > self.limit:
            raise OOG
        self.used = new
    def remaining(self) -> int:
        return self.limit - self.used


⸻

3) Memory Model (VM-local)
	•	Stack + Locals only. No dynamic heap, no recursion.
	•	Value types: int, bool, bytes, address, small tuples/arrays per ABI.
	•	Byte arrays: bounded by policy (default ≤ 64 KiB per value; see caps).
	•	Gas for memory: Billed per operation (load/store/concat/slice/hash-per-byte), not by heap curves.

Contract storage (persistent key/value) is a host surface via stdlib.storage and is priced separately.

⸻

4) Cost Categories
	1.	IR Core — arithmetic, logic, control flow.
	2.	Bytes & ABI — concatenate, slice, encode/decode.
	3.	Hashing — keccak256, sha3_256, sha3_512 with per-byte slope.
	4.	Events — emit topic/data; scales with encoded bytes.
	5.	Storage — storage.get/set/del; per-op base + bytes slope.
	6.	Treasury — treasury.transfer; fixed fee + amount validation.
	7.	Capabilities — blob_pin, ai_enqueue, quantum_enqueue, zk_verify, random. All billed at call boundary.
	8.	Admin/Meta — require, revert, no-op, bounds-check (small, fixed).

⸻

5) Representative Gas Schedule (v1)

Canonical values live in vm_py/gas_table.json. The table below is illustrative; defaults shipped with this repo match or closely track these magnitudes.

5.1 IR Core

Opcode	Cost (gas)
CONST / MOVE	2
ADD / SUB	5
MUL	8
DIV / MOD	12
LT/GT/EQ	4
NOT / AND / OR	4
JUMP / JUMPI	6 / 8
SHA3_KECCAK_PREP*	0

* bookkeeping; real hashing costs below.

5.2 Bytes / ABI

Operation	Cost (gas)
BYTES_LEN	2
BYTES_CONCAT(a,b)	15 + 1 * (len(a)+len(b))
BYTES_SLICE(a, off, n)	12 + 1 * n
ABI_ENCODE(tuple/array/scalars)	20 + 1 * total_bytes
ABI_DECODE(...)	25 + 2 * total_bytes
ADDRESS_ENC/DEC (33 bytes payload)	10

5.3 Hashing

Hash	Base (gas)	Per 64-byte block (gas)
keccak256	24	6
sha3_256	28	8
sha3_512	36	10

Charge as: base + slope * ceil(len/64).

5.4 Events

Action	Cost (gas)
events.emit(name, args) (overhead)	40
Topics computation	per-hash cost (see hashing table)
Data payload (encoded args bytes)	6 + 1 * payload_len

Emitted bytes cap applies (see §8 Caps).

5.5 Storage (Persistent)

Operation	Cost (gas)
storage.get(key)	80 + 1 * key_len + 1 * value_len
storage.set(key, value)	160 + 1 * key_len + 2 * value_len
storage.delete(key)	120 + 1 * key_len

Exact read/write multipliers may be tuned network-wide; deletes do not refund in v1.

5.6 Treasury

Operation	Cost (gas)
treasury.transfer(to, amount)	200

Execution layer separately verifies balances and applies side-effects.

5.7 Capabilities

Syscall	Cost (gas)
blob_pin(ns, data)	300 + 2 * data_len
ai_enqueue(model, prompt)	1_000 + 2 * (len(model)+len(prompt))
quantum_enqueue(circuit, shots, params)	1_200 + 2 * (circuit_bytes + params_bytes)
zk_verify(circuit_id, proof_bytes, public_bytes)	2_500 + 2 * (len(proof_bytes)+public_len)
random(nbytes) (deterministic stub)	30 + 1 * nbytes

These costs meter on-chain validation overhead. Off-chain work is priced by AICF economics, not VM gas.

⸻

6) Refunds
	•	No general memory refunds.
	•	No storage “clear” refunds in v1 (prevents griefing). Future upgrades may consider bounded refunds with strict anti-abuse.
	•	Failure paths (require/revert) do not refund already-burned gas.

⸻

7) Out-of-Gas & Errors
	•	Any debit() that would exceed the budget raises OOG ⇒ the call reverts with OOG status.
	•	abi.revert(msg) consumes the (small) revert emission cost already spent; no additional charge beyond work performed.
	•	Atomicity: A contract call is atomic; on OOG, no storage writes or events are committed.

⸻

8) Operational Caps (Hard Limits)

Enforced by validator/runtime in addition to gas:
	•	Max IR steps per call: 1,000,000
	•	Max nested tuple/array depth: 8
	•	Max bytes per single ABI value: 65,536
	•	Max total event data per call (encoded): 128 KiB
	•	Max events per call: 128
	•	Max storage key length: 256 bytes
	•	Max storage value length: 64 KiB
	•	Max random(nbytes) nbytes: 4 KiB
	•	Recursion: disallowed at the language level (validator rejects).

Concrete values sync from vm_py/gas_table.json and the network’s params; executors should assert them.

⸻

9) Determinism Rules
	•	No ambient I/O (clock, fs, network, RNG). RNG is a deterministic stub seeded from tx hash (see runtime/random_api.py).
	•	Hashing costs depend solely on input length (not alignment, not hardware).
	•	ABI (en|de)code cost uses the encoded size, not host object size.

⸻

10) Versioning & Upgrades
	•	The gas schedule is versioned with the VM: vm_py/version.py.
	•	Any change to spec/opcodes_vm_py.yaml ⇒ new vm_py/gas_table.json and a network feature-flag gate.
	•	SDKs should fetch chain.getParams to surface current gas caps to dapps.

⸻

11) Worked Examples

11.1 Counter: inc()
	•	Steps: selector check (free at host), read storage.get("count"), add 1, storage.set, emit Inc{value}.
	•	Approximate gas (ignoring function dispatch internals):
	•	get: 80 + k + v  (k=5, v≈1) → ~86
	•	ADD: 5
	•	set: 160 + k + 2v → ~167
	•	emit: 40 + topics (2×sha3_256 on small inputs ≈ 28 each) + payload (≈ 6+payload_len) → ~102
	•	Total ≈ 360 gas (illustrative).

11.2 Hash-heavy: hash(bytes N)
	•	keccak256: 24 + 6 * ceil(N/64)
	•	For N=4096: 24 + 6 * 64 = 408 gas.

⸻

12) Tables & Files
	•	Spec (authoritative): spec/opcodes_vm_py.yaml
Declares opcodes and base/slope parameters.
	•	Resolved table: vm_py/gas_table.json
Built artifact used by the runtime; pinned in repos for reproducibility.
	•	Runtime: execution/gas/table.py, vm_py/runtime/gasmeter.py

⸻

13) Testing & Conformance
	•	Unit tests:
	•	vm_py/tests/test_gas_estimator.py (static bounds)
	•	vm_py/tests/test_runtime_counter.py (dynamic usage)
	•	execution/tests/test_intrinsic_gas.py, execution/tests/test_receipts_hash.py
	•	Cross-check: SDK encoders must reproduce encoded sizes the VM charges for.

⸻

14) Security Notes
	•	Overly-large bytes arguments are rejected before execution (validator caps).
	•	Event spam bounded by per-call counts and payload size.
	•	Storage amplification (huge values) is capped by value length limits and charged proportionally.

⸻

This document describes the intent and shape of the gas model. Exact numbers are pinned by the shipped gas table and may evolve via gated network upgrades.
