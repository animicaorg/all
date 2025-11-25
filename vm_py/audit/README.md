# Animica VM — Audit Checklist (contracts & VM)

This guide provides a **practical, repeatable checklist** for auditing:
1) **Contracts** written for the deterministic Python VM, and
2) **VM/runtime changes** that could impact consensus determinism or safety.

It complements the specs:
- `vm_py/specs/DETERMINISM.md`
- `vm_py/specs/IR.md`
- `vm_py/specs/ABI.md`
- `vm_py/specs/GAS.md`

And the code you’ll often touch:
- Validator & compiler: `vm_py/validate.py`, `vm_py/compiler/*`
- Runtime: `vm_py/runtime/*` (engine, gasmeter, sandbox, stdlib APIs)
- Encoding & ABI: `vm_py/abi/*`
- Costs: `vm_py/gas_table.json` (resolved from `spec/opcodes_vm_py.yaml`)

---

## 0) Scope & threat model

- **Determinism-first**: No wall-clock, filesystem, network, or nondeterministic APIs.
- **Gas-first**: Every instruction and stdlib call must pay *before* side effects.
- **Isolation**: Contracts execute in a sandboxed Python subset; “stdlib” is the only IO surface.
- **Capabilities** (blob pin, ai/quantum enqueue, zk.verify) are **feature-gated**; on-chain cost is metered; off-chain economics/attestations handled by other modules.

Non-goals (for this VM layer):
- External time / randomness sources (use `random_api` deterministic stub or beacon adapter).
- Host OS interactions, threads, sockets, or file IO.

---

## 1) Quick pass checklist (10 min)

- [ ] **Compiles cleanly** with `omni vm compile …` and validates under `strict_mode`.
- [ ] **Gas estimate** from `compiler/gas_estimator.py` is plausible vs dynamic usage on sample calls.
- [ ] **Forbidden imports**: none (see `validate.py` + `tests/test_forbidden_imports.py`).
- [ ] **No unbounded loops** over user-controlled data without explicit gas-aware limits.
- [ ] **Storage writes** are intentional; keys are domain-separated and bounded.
- [ ] **Events** encode only public data; arguments ABI-encode deterministically.
- [ ] **Syscalls** (if used) respect size caps and do not leak secrets in payloads.
- [ ] **PRNG** usage (if any) is via deterministic API; seed is contract-visible or documented.
- [ ] **Access control** decisions are explicit and based on canonical inputs (e.g., `caller`).
- [ ] **Tests**: counter-style round-trips pass; ABI vectors and error cases covered.

---

## 2) Contract audit checklist (deep)

### 2.1 Determinism & sandboxing
- [ ] Only allowed Python syntax/AST nodes (see `vm_py/validate.py` and `specs/DETERMINISM.md`).
- [ ] No imports other than allowed `stdlib` (enforced by `runtime/sandbox.py`).
- [ ] No reflection, dynamic `eval`, metaclass tricks, or mutable globals that affect results.

### 2.2 Resource bounds & gas
- [ ] Every path has **bounded** byte/element processing; per-32B (W32) scaling is acceptable.
- [ ] Worst-case input sizes documented; gas table entries exist for any stdlib calls used.
- [ ] Failures (`require`/`revert`) happen **before** heavy work; no large wasted precomputation.

### 2.3 State & invariants
- [ ] Storage keys are **namespaced**; no accidental overlaps between logical subsystems.
- [ ] Reads validated (existence, lengths, types) before use.
- [ ] Invariants documented (e.g., sum of balances, escrow phases) and asserted at boundaries.

### 2.4 Access control / authorization
- [ ] All privileged operations gated (owner/admin/multisig/role) with clear checks.
- [ ] No “write-anywhere” or “transfer-any” code paths.
- [ ] Replay resistance for externally provided payloads (domain tags, nonces, commitments).

### 2.5 ABI & encoding
- [ ] ABI matches `manifest.json`; all functions/events/errors documented.
- [ ] Inputs validated (lengths, ranges); canonical encoding/decoding used.
- [ ] Error paths surface deterministic, bounded messages.

### 2.6 Events & confidentiality
- [ ] Events contain no sensitive material unless explicitly public.
- [ ] Topic/name bytes are small and fixed; arguments encoded once.

### 2.7 Randomness & external results
- [ ] Uses `random_api` (deterministic) or chain beacon adapter; no hidden entropy.
- [ ] If consuming capability results (AI/Quantum), IDs are deterministic, and **results are verified** by the protocol before used for critical decisions.

### 2.8 Economic safety
- [ ] Calls to treasury APIs or value-moving functions are minimal and auditable.
- [ ] Fees/costs explicitly passed and documented; no unbounded rebating logic.

### 2.9 Negative & fuzz testing
- [ ] Adversarial inputs (max sizes, empty, malformed) revert deterministically.
- [ ] ABI fuzz: random bytes fail decode quickly with bounded gas.
- [ ] Storage mutation fuzz: random key/value sequences preserve invariants or revert.

---

## 3) VM/runtime audit checklist (for VM contributors)

### 3.1 Instruction & stdlib determinism
- [ ] New/changed IR opcodes are **pure** and deterministic.
- [ ] Stdlib function semantics are host-independent; no wall-clock or OS calls.
- [ ] Hashing uses fixed implementations (sha3/keccak wrappers) with test vectors.

### 3.2 Gas accounting
- [ ] Every opcode & stdlib call has a **gas entry** in `gas_table.json`.
- [ ] Size terms computed with `W32(B) = ceil(B/32)`; integer arithmetic only.
- [ ] Meter debits **before** effects; OOG rolls back effects properly.

### 3.3 Sandbox & imports
- [ ] `runtime/sandbox.py` forbids `os`, `time`, `random` (nondet), `socket`, `subprocess`, etc.
- [ ] Contract-global objects cannot mutate interpreter behavior across calls.

### 3.4 ABI & encoding
- [ ] Encoding/decoding is canonical; round-trip tests pass for scalars/tuples/bytes.
- [ ] Error reporting does not allocate unbounded strings.

### 3.5 Capabilities/syscalls (feature-gated)
- [ ] Size caps enforced on inputs; domain tags applied to any on-chain commitment.
- [ ] Deterministic task-id derivation matches `capabilities/jobs/id.py`.
- [ ] Disabled when feature flag is off; costs present even if disabled.

### 3.6 Tests & vectors
- [ ] New code extends tests in `vm_py/tests/*` and relevant `execution/tests/*`.
- [ ] Cross-checks with spec vectors (ABI, gas, runtime) are updated.
- [ ] Fuzz harness (if applicable) runs under CI with timeouts.

### 3.7 Backwards compatibility / consensus
- [ ] Any gas-table or opcode semantic change is treated as a **consensus change** with a plan (height flag or network release notes).
- [ ] `strict_mode` catches missing costs or unrecognized opcodes at startup.

---

## 4) How to run audit helpers

**Compile only**
```bash
python -m vm_py.cli.compile path/to/contract.py --out out.ir

Simulate a call (local, deterministic)

python -m vm_py.cli.run --manifest examples/counter/manifest.json --call get

Inspect IR & gas estimate

python -m vm_py.cli.inspect_ir out.ir


⸻

5) Audit deliverables (suggested)
	•	Threat model summary & trust boundaries.
	•	Checklist table with pass/fail/rationale.
	•	Gas profiling: worst-case input sizes & measured consumption.
	•	Invariants: prose + references to assert locations.
	•	Test artifacts: inputs, expected returns/events, failure cases.
	•	Reproducible steps (commands) to re-run the audit locally.

⸻

6) References
	•	Determinism & allowed features — vm_py/specs/DETERMINISM.md
	•	IR & interpreter — vm_py/specs/IR.md, vm_py/runtime/engine.py
	•	ABI — vm_py/specs/ABI.md, vm_py/abi/*
	•	Gas model — vm_py/specs/GAS.md, vm_py/runtime/gasmeter.py
	•	Example contracts — vm_py/examples/*
	•	Tests — vm_py/tests/*

Version: audit-notes v1 (aligned with vm_py/version.py).
