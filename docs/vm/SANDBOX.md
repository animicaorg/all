# VM(Py) Sandbox — Forbidding Nondeterminism & Allowed Libraries

**Status:** Stable (v1)  
**Audience:** Contract authors, reviewers, VM/toolchain maintainers  
**Source of truth:** `vm_py/validate.py`, `vm_py/runtime/*`, `vm_py/stdlib/*`, `docs/vm/DETERMINISM.md`

This document defines the **determinism contract** for Python-based smart contracts and lists what is **allowed** vs **forbidden** inside the VM sandbox.

---

## 1) Goals

- **Bit-for-bit determinism** across nodes, platforms, and time.
- **Auditability:** small, explicit allowlist; everything else is rejected.
- **Resource safety:** bounded memory/CPU, predictable gas, and size caps.

---

## 2) High-level Rules

1. **No ambient I/O:** no filesystem, network, clock, environment, or randomness.
2. **No concurrency:** no threads, async, signals, or shared state beyond the VM’s own storage APIs.
3. **Purely integer semantics:** floats are banned to avoid platform drift.
4. **Stable hashing only:** `sha3_256`, `sha3_512`, and `keccak256` via the VM’s hash APIs.
5. **Deterministic PRNG only:** available as a *sandbox stub* seeded from the **transaction hash** (see §5.3).
6. **Explicit side-effects:** storage, events, and syscalls are explicit instructions and charged gas.
7. **Bounded loops only:** must have statically known or policy-capped bounds (enforced at compile time).
8. **No reflection or dynamic code:** `eval/exec`, dynamic imports, and runtime attribute introspection beyond primitives are disallowed.

Violations are rejected at **validation time** (`ValidationError`) before IR is produced.

---

## 3) Disallowed Python Features (Non-exhaustive)

- **Imports:** Any `import` other than the VM-provided `stdlib` surface is rejected.
- **I/O & OS:** `os`, `sys`, `pathlib`, `io`, `subprocess`, `shutil`, `socket`, `ssl`, `urllib`, `http*`, `ctypes`, `multiprocessing`.
- **Time/entropy:** `time`, `datetime`, `random`, `secrets`, `uuid`, `hashlib` (use VM hash APIs instead).
- **Concurrency:** `threading`, `asyncio`, `signal`, `queue`.
- **Reflection/dynamic:** `inspect`, `types`, `eval`, `exec`, `compile`, `globals`, `locals`, `vars`, `getattr`/`setattr` (beyond safe primitives).
- **Parsing/regex side channels:** `re` (unbounded worst-case), `json` (use ABI enc/dec), `pickle` (unsafe).
- **Floats/decimals:** `float`, `decimal`, `fractions` (non-portable semantics).
- **Data science / large libs:** anything not in the explicit allowlist.

> The validator enforces an AST **whitelist** and rejects disallowed nodes and names with source spans.

---

## 4) Allowed Builtins & Patterns

- **Builtins (subset):** `len`, `range` (with static/capped bounds), `int`, `bool`, `bytes`, `bytearray` (bounded), tuple/list/dict *construction* (bounded), `enumerate`, `min`/`max` (on ints), `abs`, `all`/`any` (bounded).
- **Control flow:** `if/else`, `for` over `range(K)`, `while` only when compiled to bounded loop with static guard; `break`/`continue` permitted within bounds.
- **Data:** bytes and integer arithmetic; small tuples/lists/dicts with **policy-capped** sizes.
- **Errors:** use `abi.revert(b"message")` (not Python `raise`).

If in doubt, the validator rejects; extend the allowlist via governance if needed.

---

## 5) VM-Provided Libraries (Allowlist)

These are the **only** imports permitted:

```python
from stdlib import storage, events, hash, abi, treasury, syscalls, random

5.1 stdlib.storage
	•	get(key: bytes) -> int|bytes
	•	set(key: bytes, value: int|bytes) -> None
	•	delete(key: bytes) -> None
Keys/values are size-capped; gas accounts for key length and value length.

5.2 stdlib.events
	•	emit(name: bytes, args: dict[bytes, int|bytes]) -> None
Event name and payload sizes are capped and charged.

5.3 stdlib.random
	•	randbytes(n: int) -> bytes
Deterministic PRNG seeded from the tx hash within a call context; same inputs → same outputs. Upper-bound on n enforced by policy.

5.4 stdlib.hash
	•	keccak256(b: bytes) -> bytes32
	•	sha3_256(b: bytes) -> bytes32
	•	sha3_512(b: bytes) -> bytes64
Stable, pure functions; gas proportional to input size.

5.5 stdlib.abi
	•	require(cond: bool, msg: bytes=b"") -> None
	•	revert(msg: bytes) -> None
	•	encode(..)/decode(..) for declared ABI scalars/tuples (bounded).
Use these rather than Python json/struct.

5.6 stdlib.treasury
	•	balance() -> int
	•	transfer(to: bytes, amount: int) -> None
No floating value types; all int with bounds.

5.7 stdlib.syscalls (capability shims)
	•	blob_pin(ns: int, data: bytes) -> bytes32
	•	ai_enqueue(model: bytes, prompt: bytes) -> task_id
	•	quantum_enqueue(circuit: bytes, shots: int) -> task_id
	•	zk_verify(circuit_id: bytes, proof: bytes, public_inputs: bytes) -> bool
These are deterministic envelopes with length caps. Enqueue calls do not yield variable-latency results inside the same block; results are consumed next block via host resolution (see Capabilities spec). The sandbox enforces that call shapes are deterministic and size-bounded; gas includes a fixed call surface cost plus size components.

⸻

6) Numeric & Data Determinism
	•	Integers only: unlimited precision with policy caps. No floats.
	•	Byte order: big-endian where applicable, mandated by ABI helpers.
	•	Hashing: via stdlib.hash only.
	•	Equality/ordering: defined only for supported types; mixed-type comparisons are rejected at validation.

⸻

7) Loop & Memory Caps
	•	Static bounds or manifest/policy hints must allow an upper bound at compile time.
	•	Per-call caps: maximum locals/stack slots, bytes allocations, event arg totals, and storage touched entries.
	•	Estimator uses caps to produce a safe gas upper bound; exceeding dynamic size caps triggers Revert.

⸻

8) Deterministic PRNG (Details)
	•	Source: Tx hash (and call index within the transaction), mixed through a VM-local stream cipher.
	•	Scope: per-call; reseeded for each call entry.
	•	Use cases: sampling, shuffling with bounded domain sizes.
	•	Never used for consensus beacons or security-critical randomness—see randomness/ module for the on-chain beacon.

⸻

9) Versioning & Reproducibility
	•	The runtime exposes vm_version, gas_table_version, and bytecode_hash for receipts.
	•	Tooling pins encoder and gas tables; changing them requires a version bump and recompile.

⸻

10) Examples

✅ Allowed

from stdlib import storage, events, hash, abi

def set_name(b: bytes):
    abi.require(len(b) <= 64, b"too long")
    storage.set(b"name", b)
    events.emit(b"SetName", {b"len": len(b)})

def id32(b: bytes) -> bytes:
    return hash.sha3_256(b)

❌ Rejected (nondeterminism)

import time               # forbidden import
def now():
    return time.time()    # nondeterministic


⸻

11) Review Checklist
	•	No disallowed imports or AST nodes.
	•	Loops have static or policy bounds.
	•	All bytes/collections sizes enforced via require(...).
	•	Only stdlib.* calls for side-effects.
	•	No floats or implicit coercions.
	•	Gas estimator upper bound generated and reasonable.

⸻

12) Extending the Allowlist

Changes to the allowlist or sandboxed surfaces must:
	1.	Demonstrate deterministic semantics and size/gas caps.
	2.	Include tests (validator + runtime) and docs updates.
	3.	Ship behind a feature flag and gated version bump.

⸻

See also:
	•	docs/vm/COMPILER.md — pipeline & static gas estimation
	•	docs/spec/ENCODING.md — canonical encodings & hashing
	•	docs/spec/CAPABILITIES.md — syscalls ABI and determinism rules
