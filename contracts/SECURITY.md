# Animica Contracts — Security Guide & Audit Checklist

This document is a **practical security playbook** for contracts written for the Animica Python VM (`vm_py`). It focuses on **determinism**, **economic correctness**, and **abuse resistance**, with concrete checklists and invariant templates.

> Scope: single-module Python contracts using the deterministic stdlib (`storage`, `events`, `hash`, `abi`, `treasury`, `syscalls`). No standard library I/O; no dynamic imports. See `contracts/CODESTYLE.md` for the deterministic subset.

---

## 1) Threat Model (What We Defend Against)

- **Adversarial callers**: malformed ABI inputs, boundary value abuse, gas griefing, storage bloat attempts, replayed calldata on other chains/versions.
- **Economic manipulation**: balance/ledger invariants broken, fee leakage, bypassing authorization, double-spend via state race assumptions.
- **Non-determinism** across nodes: time/randomness, hash-order reliance, iteration over unsorted maps, environment dependence.
- **Resource exhaustion**: unbounded loops, quadratic data paths, event spam, oversized syscalls payloads.
- **Supply-chain drift**: mismatched toolchain/ABI/gas tables producing incompatible bytecode or semantics.
- **External capability misuse**: DA pin with oversized payloads, AI/Quantum enqueues without size/fee caps, reading results prematurely or ambiguously.

Out-of-scope at contract level: network, consensus, VDF, P2P, and TEE/QPU attestation details (verified elsewhere). Contracts must **consume** those outcomes defensively via deterministic APIs only.

---

## 2) Core Security Principles

1. **Determinism First**: No floats/time/I/O; canonical ordering for any externally visible effects; fixed-size bounds on computations.
2. **Fail-Fast**: Validate inputs early (`abi.require`), reject large/invalid payloads before storage mutations.
3. **Minimal State Surface**: Few, stable storage keys; predictable encoding; no unbounded key spaces.
4. **Checks → Effects**: Validate preconditions, then mutate storage, then emit events.
5. **Explicit Auth**: All privileged actions must check role/storage flags explicitly.
6. **Bound External Capability Use**: Syscalls (DA/AI/Quantum/zk) gated by strict size/shape/budget caps.
7. **Reproducible Builds**: Pin toolchain versions; record manifest & ABI; verify code hash on deploy/verify.

---

## 3) Invariants (Templates You Should Enforce)

> Use `abi.require(<condition>, <short_reason_bytes>)` and **unit tests** to prove each invariant.

### 3.1 Ledger/Balance Invariants
- **Non-Negative Balances**: `balance(addr) >= 0` for every address.
- **Conservation** (if applicable): `sum(balances) == total_supply` (unless mint/burn; then constrained by policy).
- **Monotonic Supply**: `0 <= total_supply <= MAX_SUPPLY`.
- **Single-Writer Rule**: Only one code path updates a given balance key per call.

### 3.2 Authorization & Roles
- **Caller Authorization**: Admin-only functions check `caller == admin` (bytes-equal).
- **Frozen Flags**: If a freeze/paused flag exists, **all** state-changing entrypoints enforce it.
- **Role Updates**: Changing admin/role keys requires current admin + explicit non-zero new role.

### 3.3 Storage Consistency
- **Read-Modify-Write Pattern**: Load → check → write; no blind overwrites.
- **Canonical Encoding**: All ints encoded via `abi.encode_int`; addresses via `abi.encode_address`.
- **Key Namespacing**: Prefix and version keys: `b"mod:v1:balance:" + addr`.

### 3.4 Events ↔ State
- **Event Echo**: For critical transitions (e.g., Transfer), emitted event fields match the exact state delta.
- **Order**: Events are emitted **after** state updates (unless revert-on-failure pattern).

### 3.5 ABI Inputs
- **Bounded Lengths**: `len(bytes_arg) <= CAP`; arrays/tuples count bounded (e.g., `<= 64`).
- **Type Sanity**: Non-negative where required; upper bounds on integers to avoid pathological gas on encoding.

### 3.6 Capability Usage
- **DA**: `ns` in allowed range; `len(data) <= BLOB_CAP`; check receipt shape.
- **AI/Quantum**: Model/circuit sizes within caps; fee budget checked; result consumption at **next block** only.
- **zk.verify**: Input sizes bounded; verify boolean result handled (no partial trust).

---

## 4) Common Vulnerabilities & How to Avoid Them

### 4.1 Non-Determinism
- **Map iteration order**: Never rely on dict ordering; explicitly `sorted(list, key=...)`.
- **Time/random**: Do not query time; do not use non-deterministic RNG. Use provided deterministic APIs only.
- **Floating point**: Disallowed; use integer math.

### 4.2 Unbounded Work / Gas Griefing
- Loops over user-provided arrays must cap `N`.  
  **Pattern**: `n = min(len(items), MAX_N)` then operate on `items[:n]`.
- Avoid quadratic nested loops; preindex instead; limit event emissions.

### 4.3 Auth Bypass
- Every privileged entrypoint checks the exact role key loaded from storage.  
  **Never** derive permissions from calldata hints.

### 4.4 Replay / Domain Separation
- If you encode messages for signing or hashing inside a contract workflow, tag with **domain bytes** and chainId if applicable.
- Do not accept arbitrary signatures inside the contract unless the message domain is enforced.

### 4.5 Storage Bloat
- User-supplied keys must be bounded in length and count.  
  Consider per-caller quotas, e.g., max items per tx.

### 4.6 Event Confusion
- Events must reflect the **final** state (post-mutation).  
  Ensure no revert after event emission, or emit only after all checks.

### 4.7 Arithmetic Pitfalls (Despite Big Ints)
- Encode **units** explicitly (e.g., 18 decimals).  
  Guard `amount > 0` and `amount <= MAX_AMOUNT`.  
  Avoid multiplication that could generate massive intermediate allocations (still gas-costly).

### 4.8 Syscall Misuse
- Treat `syscalls.ai_enqueue`/`quantum_enqueue` as **expensive**; enforce caps; document when results are readable.  
- Do not block on results or assume synchronous availability.

---

## 5) Secure Patterns (Copyable)

### 5.1 Checks → Effects → Events
```python
def transfer(to: bytes, amount: int) -> None:
    abi.require(is_valid_addr(to), b"addr")
    abi.require(0 < amount <= MAX_AMOUNT, b"amt")
    src = caller()  # provided deterministically by the runtime
    sb = _load_balance(src)
    abi.require(sb >= amount, b"bal")
    _save_balance(src, sb - amount)
    tb = _load_balance(to)
    _save_balance(to, tb + amount)
    events.emit(b"Transfer", {
        b"from": abi.encode_address(src),
        b"to": abi.encode_address(to),
        b"value": abi.encode_int(amount),
    })

5.2 Bounded Batch With Canonical Order

MAX_SET = 64
def set_many(pairs: list[tuple[bytes, int]]) -> int:
    n = min(len(pairs), MAX_SET)
    for k, v in sorted(pairs[:n], key=lambda kv: kv[0]):
        abi.require(len(k) <= 32, b"key")
        abi.require(0 <= v <= MAX_VALUE, b"val")
        storage.set(PREFIX + k, abi.encode_int(v))
    return n

5.3 Capability Enqueue (Deterministic)

MAX_PROMPT = 4096
def ai_classify(model: bytes, prompt: bytes) -> bytes:
    abi.require(len(model) <= 64, b"model")
    abi.require(len(prompt) <= MAX_PROMPT, b"prompt")
    rcpt = syscalls.ai_enqueue(model, prompt)  # deterministic receipt bytes
    events.emit(b"AIEnqueued", {b"rcpt": rcpt})
    return rcpt


⸻

6) Audit Checklist (Tick Every Box)

6.1 Determinism & Language Subset
	•	No imports outside stdlib.
	•	No floats/time/random; no recursion; no dynamic eval/exec.
	•	All loops have explicit bounds; slice or range-capped.
	•	Map/list processing sorted before producing storage/events.

6.2 Storage & Encoding
	•	Keys namespaced & versioned (e.g., b"mod:v1:").
	•	Values encoded via abi.encode_* helpers only.
	•	Read-modify-write with validation; no blind overwrite.
	•	Deletion semantics explicit and consistent.

6.3 Authorization
	•	Privileged entrypoints check role from storage; role updates authenticated.
	•	Paused/frozen flags honored by all mutators.
	•	Caller/address validation is strict (length/format).

6.4 Economic Invariants
	•	Non-negative balances; conservation or policy-respecting deltas.
	•	Supply bounds respected; mint/burn paths checked.
	•	No hidden minting/burning via exceptional paths.

6.5 ABI Inputs & Outputs
	•	Bytes lengths bounded; tuple sizes bounded.
	•	Integer domains validated (≥0, ≤cap).
	•	Return values ABI-encodable and minimal.

6.6 Events
	•	Emitted after successful state mutations.
	•	Fields mirror exact state changes and are ABI-encoded.
	•	Event count per call is bounded.

6.7 Capabilities / Syscalls
	•	DA: namespace and size caps; errors handled deterministically.
	•	AI/Quantum: request sizes & fees bounded; next-block read semantics documented.
	•	zk.verify: inputs bounded; boolean handled; no partial trust.

6.8 Gas & Complexity
	•	No quadratic or unbounded paths on user data.
	•	Repeated hashing/encoding minimized (cache locals).
	•	Large loops short-circuit on first failure where safe.

6.9 Build & Supply Chain
	•	Toolchain versions pinned (vm_py, gas table).
	•	Manifest and ABI checked in; code hash verifiable.
	•	Reproducible build tested (same inputs → same artifacts).

⸻

7) Testing Guidance

7.1 Unit Tests
	•	Boundary cases: min/max amounts, empty bytes, max list lengths.
	•	Invariant checks: sums, non-negativity, role enforcement.
	•	Event/state correlation: verify events reflect storage changes.

7.2 Property Tests
	•	Encode/decode idempotence for ABI types used.
	•	Random small arrays under caps—gas/time sanity (no blowups).
	•	Sorting canonicalization: different input orders → same effects.

7.3 Negative Tests
	•	Invalid addresses, oversize bytes, zero/negative amounts, paused/frozen state.
	•	Capability enqueues that exceed limits; ensure deterministic rejection.

⸻

8) Operational Guidance
	•	No secrets in contract code; all secrets must live off-chain.
	•	PII: Avoid storing or emitting user-identifiable data.
	•	Upgrades: Deploy new versions; keep old storage readable via versioned keys; never mutate format in-place without migration code.
	•	Observability: Use concise event names/fields. Avoid logging huge payloads via events.

⸻

9) Red Flags (If You See These, Stop & Fix)
	•	Iteration over dict.items() without sorting → nondeterminism.
	•	Any usage of unsupported imports or random/time/os/json.
	•	Unbounded while or for over calldata length.
	•	Events emitted before checks/effects.
	•	Storing user-controlled variable-length keys without caps.
	•	Assuming immediate availability of AI/Quantum results.

⸻

10) Reviewer Snippets

Address check (example):

def is_valid_addr(a: bytes) -> bool:
    return isinstance(a, (bytes, bytearray)) and len(a) == 32

Require helper:

def require_bytes_le(x: bytes, cap: int, reason: bytes) -> None:
    abi.require(isinstance(x, (bytes, bytearray)) and len(x) <= cap, reason)


⸻

11) Versioning & Disclosure
	•	Bump a contract version constant on material changes.
	•	Maintain a CHANGELOG of externally visible interface changes (ABI, events).
	•	Security fixes should include a test that would have caught the issue.

⸻

Appendix: Minimal Invariant Test Template

def test_supply_conservation():
    pre_total = total_supply()
    transfer(to, amt)
    assert total_supply() == pre_total

def test_events_match_state():
    pre_from = balance(src)
    pre_to = balance(dst)
    tx = transfer(dst, 5)
    ev = last_event("Transfer", tx)
    assert decode(ev["value"]) == 5
    assert balance(src) == pre_from - 5
    assert balance(dst) == pre_to + 5


⸻

Summary: Keep logic small, bounded, canonical, and explicit. Validate early, mutate minimally, emit accurate events, and treat capabilities as capped, asynchronous resources. Determinism is security.

