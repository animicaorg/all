# Animica VM — Gas Model (v1)

This document defines the **deterministic gas model** used by the Python VM (`vm_py`) and its stdlib. Concrete numbers for each opcode and stdlib call live in:

- `spec/opcodes_vm_py.yaml` — human-edited source of costs
- `vm_py/gas_table.json` — resolved table consumed by the VM at runtime

> The VM is **gas-first**: every instruction and stdlib function must prove it can pay before touching state or producing side-effects.

---

## 1) Goals & invariants

- **Deterministic** across platforms and implementations.
- **Simple, linear** cost forms: `base + k·size` with well-defined rounding.
- **Side-effect safety**: gas is debited **before** work; failure to fund → `OOG`.
- **No wall-clock dependence**: costs are table-driven constants, not timing.
- **Separation of concerns**:
  - The **VM** meters opcodes and stdlib calls.
  - The **execution layer** (see `execution/gas/*`) finalizes refunds and fee splits.

---

## 2) Units & notation

- `gas` — dimensionless cost unit debited from the transaction’s gas budget.
- `B` — bytes (payload size).
- `W32(x)` — words of 32 bytes, i.e. `ceil(x / 32)`.
- `ceil_div(a,b)` — integer ceil division.

All linear terms use **per-32B** word multipliers unless noted.

---

## 3) Meter semantics

The VM uses `GasMeter` (`vm_py/runtime/gasmeter.py`):

```text
debit(n):
  if remaining < n: raise OOG
  remaining -= n

refund(n):
  pending_refund += n     # bounded in execution layer at finalization

consume(op_cost):
  debit(op_cost)

	•	Instruction step: fetch → compute cost (from gas_table) → debit → execute.
	•	Stdlib call: compute base + linear(size) → debit → perform call.
	•	Revert: preserves remaining gas, drops side-effects; refund handling is finalized in the execution layer (see §8).

⸻

4) Cost primitives

To avoid ambiguity, all size-dependent costs use these helpers:
	•	Per-byte linear: base + pB * B
	•	Per-word (32B) linear: base + pW * W32(B)
	•	List of N items: base + pN * N + per-item-size terms
	•	For hashing/encoding, B is the actual input length after ABI framing.

⸻

5) Opcode families (summary)

Actual constants are in vm_py/gas_table.json. The VM resolves each IR opcode mnemonic → cost.

Family	Cost form (symbolic)	Notes
Stack / Move	base	PUSH, POP, DUP, SWAP
Control flow	base	JUMP, JUMPI, CALL, RET (IR-level)
Arithmetic	base	ADD, SUB, MUL, DIV, MOD, NEG
Comparisons	base	EQ, LT, GT, ISZERO
Bit/Bytes ops	base + pW·W32(B)	CONCAT, SLICE, BYTES_CMP (size = produced / touched)
Memory alloc	base + pW·W32(B)	VM alloc is bounded; no dynamic memory gas growth beyond linear pW
ABI helpers (IR)	base + pW·W32(B)	Internal enc/dec steps when part of IR (rare; most via stdlib)

The Python VM IR is intentionally small; most high-level operations (hashing, storage, events, syscalls) are exposed via stdlib, not raw opcodes.

⸻

6) Stdlib calls (charged by the VM)

All stdlib calls have explicit cost entries (stdlib.<name>). The VM computes the size parameter (B, N) deterministically from arguments.

6.1 Storage API (stdlib.storage)
	•	get(key: bytes) -> bytes
Cost: base_get + pW_get * W32(|key|)
	•	set(key: bytes, value: bytes) -> None
Cost: base_set + pW_key * W32(|key|) + pW_val * W32(|value|)
	•	delete(key: bytes) -> None
Cost: base_del + pW_del * W32(|key|)

Refunds for deletes/overwrites are not applied in the VM. The execution layer inspects the write set and applies protocol-level refund rules (see §8).

6.2 Events API (stdlib.events)
	•	emit(name: bytes, args_encoded: bytes)
Cost: base_evt + pW_topic * W32(|name|) + pW_data * W32(|args_encoded|)

args_encoded is the ABI-encoded tuple (see vm_py/specs/ABI.md).

6.3 Hash API (stdlib.hash)
	•	keccak256(data: bytes)
Cost: base_k + pW_k * W32(|data|)
	•	sha3_256(data: bytes)
Cost: base_s256 + pW_s256 * W32(|data|)
	•	sha3_512(data: bytes)
Cost: base_s512 + pW_s512 * W32(|data|)

6.4 ABI helpers (stdlib.abi)
	•	encode(values: tuple) / decode(bytes)
Cost (encode): base_enc + pW_enc * W32(|out|)
Cost (decode): base_dec + pW_dec * W32(|in|)

Enc/dec costs are bounded and linear in the encoded length. Validation failures still charge the attempted work.

6.5 Treasury API (stdlib.treasury)
	•	balance() -> int
Cost: base_balance
	•	transfer(to: address, amount: int) -> None
Cost: base_txf + pW_meta * 1 (constant)
Actual token accounting and fee handling occur in the execution layer; VM charges a small constant to discourage gratuitous calls.

6.6 Random / Deterministic PRNG (stdlib.random)
	•	random(n_bytes: int) -> bytes
Cost: base_rng + pW_rng * W32(n_bytes)

6.7 Syscalls (capabilities bridge) — feature-gated
	•	blob_pin(ns: int, data: bytes) -> commitment
Cost: base_pin + pW_pin * W32(|data|)
	•	ai_enqueue(model: bytes, prompt: bytes) -> task_id
Cost: base_ai + pW_ai_m * W32(|model|) + pW_ai_p * W32(|prompt|)
	•	quantum_enqueue(circuit: bytes, shots: int, params: bytes) -> task_id
Cost: base_q + pW_q_c * W32(|circuit|) + pW_q_p * W32(|params|)
	•	zk_verify(circuit: bytes, proof: bytes, input: bytes) -> bool
Cost: base_zk + pW_zk_c * W32(|circuit|) + pW_zk_p * W32(|proof|) + pW_zk_i * W32(|input|)
	•	read_result(task_id: bytes) -> bytes?
Cost: base_rr + pW_rr * W32(|task_id|)

These costs pay only for on-chain envelope handling. Off-chain compute is priced and settled by AICF economics; the VM never blocks or bills time.

⸻

7) Deterministic size accounting

For any operation that depends on sizes:
	•	Use the post-validation length (e.g., after decoding address length, which must be 33).
	•	For tuples/lists, the size used is the encoded byte length (ABI v1).
	•	W32(B) = ceil_div(B, 32) with integer arithmetic.

⸻

8) Refunds & finalization
	•	The VM may record hints (e.g., storage write kinds) but does not apply refunds directly.
	•	The execution layer combines:
	•	Refund tracker: execution/gas/refund.py
	•	Intrinsic gas and base/tip split: execution/gas/intrinsic.py, execution/runtime/fees.py
	•	Protocol sets a max refund ratio to avoid pathological free execution; enforced during transaction finalization.
	•	A REVERT cancels storage/event side-effects in the state machine while keeping gas already consumed, minus any protocol-defined refund allowances (if any).

⸻

9) Table versioning & feature flags
	•	The VM loads vm_py/gas_table.json at module init (see vm_py/config.py).
	•	A gas table checksum is exported in receipts/metadata for debuggability.
	•	Feature gates:
	•	strict_mode: reject missing/opcode cost entries at startup.
	•	Optional stdlib surfaces (syscalls) are disabled unless explicitly enabled and costed.

⸻

10) Conformance & tests
	•	Unit tests:
	•	vm_py/tests/test_gas_estimator.py — static vs dynamic bounds
	•	vm_py/tests/test_runtime_counter.py — end-to-end inc/get
	•	execution/tests/test_intrinsic_gas.py — intrinsic & OOG edges
	•	execution/tests/test_receipts_hash.py — stability vs spec vectors
	•	Fixtures: vm_py/fixtures/abi_examples.json cover encoding sizes; cross-check per-W32 linearity.

⸻

11) Worked examples
	1.	Hash a 70-byte payload with SHA3-256
cost = base_s256 + pW_s256 * W32(70) = base_s256 + pW_s256 * 3
	2.	Emit event with topic b"Transfer" (8B) and args-encoding 96B
cost = base_evt + pW_topic*W32(8) + pW_data*W32(96) = base_evt + pW_topic*1 + pW_data*3
	3.	Storage set with 24B key and 100B value
cost = base_set + pW_key*W32(24) + pW_val*W32(100) = base_set + pW_key*1 + pW_val*4

⸻

12) Backwards compatibility
	•	Changing any opcode or stdlib cost is a consensus change. Networks pin opcodes_vm_py.yaml → gas_table.json in their release.
	•	New opcodes or stdlib entries must:
	•	Define costs in both spec and resolved table,
	•	Be guarded by a feature flag and/or height-based activation.

⸻

Version: GAS v1 (aligned with vm_py/version.py).
