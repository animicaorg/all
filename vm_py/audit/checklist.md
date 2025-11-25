# Animica VM — Reviewer Checklist
Concise, tickable list for contract & VM/runtime reviews. See also:
- Specs: `vm_py/specs/DETERMINISM.md`, `IR.md`, `ABI.md`, `GAS.md`
- Code: `vm_py/validate.py`, `vm_py/runtime/*`, `vm_py/compiler/*`, `vm_py/abi/*`
- Tests: `vm_py/tests/*`, vectors under `vm_py/fixtures/*`

---

## 1) Determinism & Sandbox
- [ ] **AST allowlist** only (no forbidden nodes: dynamic exec/eval, reflection, generators if banned). Verified by `validate.py`.
- [ ] **Imports**: only `vm_py.stdlib.*`. No `os`, `sys`, `time`, `random`, `socket`, `subprocess`, `ctypes`, or FFI.
- [ ] **Global state**: no hidden mutable globals that change semantics across calls.
- [ ] **Byte-order & integer widths** are explicit; avoid platform-dependent behavior.

## 2) Gas & Resource Bounds
- [ ] **Costs present** for every opcode & stdlib call in `gas_table.json`.
- [ ] **Debit-before-effect**: gas charged prior to observable state changes.
- [ ] **Size terms** scale with `W32(B) = ceil(B/32)` or documented formula.
- [ ] **Loops & recursion** have clear, input-bounded limits; no unbounded iteration over attacker-controlled data.
- [ ] **Failure early**: heavy work gated behind cheap checks; revert paths don’t waste large gas.

## 3) ABI, Encoding, and Inputs
- [ ] ABI matches `manifest.json`: function names, arg/return types, events, and errors.
- [ ] **Input validation**: lengths, ranges, byte formats checked; rejects early with deterministic errors.
- [ ] **Canonical encoding** via `vm_py/abi/*`; round-trip tests pass (scalars/tuples/bytes/address).
- [ ] **Error messages** are bounded; no unbounded string formatting with attacker input.

## 4) Storage & Invariants
- [ ] **Key namespacing**: domain-separated keys; no overlap across subsystems.
- [ ] **State invariants** documented (balances, escrow phases, counters monotonicity) and asserted at boundaries.
- [ ] **Atomicity**: multi-step updates either all commit or revert cleanly on error/OOG.
- [ ] **Snapshot/journal** behavior understood if using host adapters; no partial writes.

## 5) Access Control & Authorization
- [ ] Privileged ops (owner/admin/roles) checked **first**; failures short-circuit.
- [ ] **Caller/chainId** derived from canonical context; no spoofable inputs.
- [ ] **Replay resistance** for any externally provided payloads (nonces/domain tags/commitments).

## 6) Events & Logging
- [ ] Event names/topics are fixed-size and documented; arguments ABI-encoded.
- [ ] No sensitive or secret material in events unless explicitly intended & documented.
- [ ] Event ordering is deterministic and covered by tests.

## 7) Randomness & External Results
- [ ] Uses **deterministic PRNG** (`runtime/random_api.py`) or the chain beacon adapter; no ambient entropy.
- [ ] If consuming **capability results** (AI/Quantum/blob/zk), IDs are deterministic, sizes capped, and protocol-side verification is assumed (contract should not trust unchecked blobs).

## 8) Syscalls / Capabilities (Feature-Gated)
- [ ] Inputs to syscalls (blob_pin, ai_enqueue, quantum_enqueue, zk_verify) are size-limited and sanitized.
- [ ] Deterministic **task-id** matches `capabilities/jobs/id.py` derivation (domain-separated hashing).
- [ ] Behavior when feature flag disabled is explicit (fails deterministically).

## 9) Tests & Vectors
- [ ] Unit tests cover: happy path, boundary sizes, malformed input, revert/OOG, and event ordering.
- [ ] ABI vectors & encoding round-trips included.
- [ ] Gas tests: worst-case inputs approximate static estimates; no unexpected amplification.
- [ ] Determinism tests: repeated runs produce identical outputs/logs/state.

## 10) VM/Runtime Changes (for VM contributors)
- [ ] New IR ops are **pure** and well-specified; spec & tests updated.
- [ ] Sandbox rules updated with any language-surface changes; negative tests added.
- [ ] Gas-table diff reviewed as a **consensus-affecting change**; release/activation plan documented.
- [ ] Hash APIs (sha3/keccak) unchanged or versioned; vectors pass on all supported platforms.

---

## Reviewer Notes (fill in)
- **Contract/Module Name**: __________________________
- **Commit/Version**: ________________________________
- **Reviewer**: ______________________________________
- **Date**: _________________________________________

### Findings Summary
- **Severity**: ☐ Critical ☐ High ☐ Medium ☐ Low ☐ Info  
- **Determinism**: ☐ Pass ☐ Issues (list)  
- **Gas & Bounds**: ☐ Pass ☐ Issues (list)  
- **Access Control**: ☐ Pass ☐ Issues (list)  
- **Storage/Invariants**: ☐ Pass ☐ Issues (list)  
- **ABI/Encoding**: ☐ Pass ☐ Issues (list)  
- **Syscalls/Capabilities**: ☐ Pass ☐ Issues (list)

### Action Items
1. ___________________________________________________
2. ___________________________________________________
3. ___________________________________________________

*Version: checklist v1 (aligned with `vm_py/version.py`).*
