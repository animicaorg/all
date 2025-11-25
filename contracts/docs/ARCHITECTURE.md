# Animica Contracts Architecture

This document describes the contract architecture conventions for **Animica’s Python-VM**:
- storage layout & namespacing
- canonical events and ABI patterns
- determinism and safety rules that affect design
- recommended upgrade, permission, and capability integration patterns

> TL;DR: Contracts run in a **deterministic Python subset** on a minimal VM. Storage is a
> byte→byte key/value map; events are structured and encoded canonically; ABIs follow a
> compact length-prefixed encoding. Favor simple, explicit state machines and stable
> key prefixes. Avoid any assumption about wall-clock time or external I/O.

---

## 1) Execution model & determinism

- **Language**: a restricted, *deterministic* subset of Python (validated at compile time).
  - No network, filesystem, randomness, floating point, or unbounded recursion.
  - Allowed standard library is the **VM stdlib** only (see `vm_py/stdlib/*`).
- **State**: each contract sees a flat KV store with byte keys and values (`bytes`).
- **Atomicity**: a transaction executes atomically against state; on `Revert` or `OOG`,
  **no writes** are committed.
- **Gas**: every operation consumes gas. Gas must be checked before any potentially
  expensive loop or data copy. Use the static gas estimator for headroom.
- **Time**: there is no non-deterministic wall-clock. Flows involving “time” must
  reference **block height** or **beacon rounds** (from the randomness module) when
  needed. Avoid policies requiring sub-block timing.

---

## 2) Storage layout

### 2.1 Principles

- **Prefix by domain**: Every logical field/key must be namespaced by a short ASCII
  prefix (e.g., `b"tok:"`, `b"own:"`, `b"acl:"`), followed by a compact binary suffix
  (account, role id, etc.)
- **Fixed keys** for singletons (e.g., total supply, owner): short ASCII keys like
  `b"tok:total"`, `b"own:owner"`, `b"ms:thresh"`.
- **Composite keys** for maps/lists: `prefix || separator || encoded_id`.
  - Use a **single byte separator** `b":"` consistently.
  - IDs and indexes should be encoded as **big-endian ASCII** for small integers or as
    raw bytes for hashes/addresses.
- **Versioning**: optional `b"v:1:"` preface for contracts expected to evolve.

### 2.2 Key patterns (examples)

| Domain             | Key (bytes)                                   | Value (bytes) / Encoding                                |
|--------------------|-----------------------------------------------|---------------------------------------------------------|
| Owner (Ownable)    | `b"own:owner"`                                | address bytes                                           |
| Roles (RBAC)       | `b"acl:role:" + role_id + b":" + addr`        | `b"1"` present (granted), absent otherwise              |
| Token20            | `b"tok:total"`                                | u256 in canonical bytes (length-prefixed or 32-byte)    |
| Token20            | `b"tok:bal:" + addr`                          | u256 bytes                                              |
| Token20            | `b"tok:allow:" + owner + b":" + spender`      | u256 bytes                                              |
| Nonces             | `b"nonce:" + addr`                            | u64/u128 bytes                                          |
| Timelock queue     | `b"tl:op:" + op_id`                           | serialized operation blob                               |
| Timelock schedule  | `b"tl:eta:" + op_id`                          | height or round (u64) bytes                             |
| Multisig           | `b"ms:thresh"`                                | ASCII decimal threshold                                 |
| Multisig           | `b"ms:nonce:" + ascii(nonce)`                 | `b"1"` if consumed                                      |
| Registry           | `b"reg:name:" + name32`                       | address bytes                                           |
| Escrow             | `b"esc:bal:" + id32 + b":" + party_addr`      | u256 bytes                                              |
| Capabilities (task)| `b"cap:task:" + task_id32`                    | serialized receipt/result pointer                       |

> **Address bytes**: treat addresses as *opaque bytes*. Off-chain, addresses are bech32m
> (`anim1…`) codecs over a payload; **on-chain do not assume a particular length**. Enforce
> only minimal non-emptiness or a chain-specific check in adapters if provided.

### 2.3 Encodings

- **Integers**: use canonical big-endian without leading zeros (or fixed 32-byte for
  balances where appropriate). The VM stdlib offers helpers in `stdlib.math.safe_uint`.
- **Struct blobs**: when storing compound records, prefer **CBOR** with stable field
  names or a documented byte layout. Avoid JSON.

---

## 3) Events

### 3.1 Model

- The VM exposes `emit(name: bytes, args: dict)` with the following **canonicalization**:
  - `name` is a short ASCII label (`b"Transfer"`, `b"Approval"`, `b"Executed"`).
  - `args` keys are ASCII; values are bytes, ints (converted to canonical bytes),
    or small dicts/arrays. The encoder sorts keys lexicographically.
- Events are **ordered** by emission time within a transaction.

### 3.2 Standard event schemas

**Token20**
- `Transfer`: `{ "from": address, "to": address, "value": u256 }`
- `Approval`: `{ "owner": address, "spender": address, "value": u256 }`
- `Mint`: `{ "to": address, "value": u256 }`
- `Burn`: `{ "from": address, "value": u256 }`

**Access / Ownership**
- `OwnershipTransferred`: `{ "prev": address, "next": address }`
- `RoleGranted`: `{ "role": bytes32, "account": address, "sender": address }`
- `RoleRevoked`: `{ "role": bytes32, "account": address, "sender": address }`

**Timelock / Multisig**
- `Queued`: `{ "op": bytes32, "eta": u64 }`
- `Executed`: `{ "subject_hash": bytes32, "nonce": u64, "approvals": u8, "threshold": u8 }`
- `Canceled`: `{ "op": bytes32 }`

**Registry**
- `NameSet`: `{ "name": bytes32, "addr": address }`

**Capabilities**
- `TaskEnqueued`: `{ "task_id": bytes32, "kind": u8 }`
- `TaskResult`: `{ "task_id": bytes32, "ok": bool }`

> Emit only what consumers need. Over-emitting increases index size and gas.

---

## 4) ABI patterns

The ABI used by Animica’s Python-VM is a compact, deterministic schema (see
`spec/abi.schema.json`). Contracts expose functions with **positional arguments**, typed as:

- `bytes` (opaque), `int` (non-negative only), `bool`
- **address** is represented as `bytes` inside the VM
- arrays are supported as `list[bytes]`/`list[int]` where needed
- return values follow the same rules

### 4.1 Function naming & structure

- Use **verb-object** for mutating calls: `transfer`, `approve`, `mint`, `burn`, `queue`, `execute`.
- Use **get**/**has** for reads: `get_balance`, `get_allowance`, `has_role`, `get_threshold`.
- Separate **view** vs **state-changing** calls clearly in the manifest.

### 4.2 Canonical error semantics

- Use `require(cond, "error message")` from `stdlib.abi` to enforce invariants.
- Error messages are **protocol surface**: keep them stable and concise (snake-case or
  short phrases). Example: `"insufficient_balance"`, `"not_owner"`, `"nonce_used"`.

### 4.3 Nonces & permits (PQ-aware)

- For off-chain approvals (e.g., Token20 Permit), use a **domain-separated** sign payload:

SignBytes = Canonical({
“domain”: “animica.permit.v1”,
“chainId”: ,
“contract”: ,
“owner”: ,
“spender”: ,
“value”: ,
“nonce”: ,
“deadline”: 
})

- The VM verifies the signature **off-chain** (wallet / RPC) before accepting. The contract
validates **nonce freshness** and **deadline** and then sets `allowance`.

> PQ note: signature algorithms and domains are specified in `spec/pq_policy.yaml` and
> `spec/domains.yaml`. Contracts **must not** parse signatures; they only consume the
> already-verified intent by checking on-chain nonces and preconditions.

---

## 5) Standard modules (stdlib) & composition

### 5.1 Access & control

- **Ownable** (`contracts/stdlib/access/ownable.py`): owner in `b"own:owner"`, functions
`only_owner()` checks, emits `OwnershipTransferred`.
- **Roles** (`contracts/stdlib/access/roles.py`): role ids are `bytes32`, stored under
`b"acl:role:" + role + b":" + addr`. Always gate admin operations through a distinct
`ADMIN_ROLE` or owner.

### 5.2 Token (Animica-20)

- **Balances** at `b"tok:bal:" + addr`; **allowances** at `b"tok:allow:" + owner + b":" + spender"`.
- `transfer`, `approve`, `transfer_from`, plus optional `mint`/`burn` extensions gated by
**Ownable** or **Roles**.
- Emits `Transfer`/`Approval` consistently.

### 5.3 Treasury utilities

- **Escrow**: records deposits under a dispute-capable flow. Keys include an escrow id
(`bytes32`) to keep multi-escrow state separate: `b"esc:bal:" + id + b":" + party`.
- **Splitter**: split payments by fixed shares to multiple recipients (pure accounting or
tied to external treasury hooks).

### 5.4 Upgrade proxy

- Minimal proxy that **pins a code hash** (no mutable logic pointer). The proxy forwards
calls to a target that must match an expected hash stored under a fixed key. Upgrades
require replacing the target **and** the pinned hash in a controlled process (timelock
or multisig). See `contracts/stdlib/upgrade/proxy.py`.

### 5.5 Capabilities (off-chain compute / DA / randomness / zk)

The VM exposes **deterministic syscall shims** implemented by the host:
- **AI / Quantum**: `ai_enqueue`, `quantum_enqueue`, `read_result` (results are consumed
next block after proofs confirm). Contract stores returned **task_id** in state and
emits a `TaskEnqueued`; later reads are deterministic.
- **DA blob**: `blob_pin(ns, data)` returns a commitment; store it and emit an event for
indexers.
- **Randomness**: read the beacon output for the current/previous rounds; if you need
commit-reveal, use helper scaffolding to create commitments and verify reveals.
- **zk.verify**: invoke predicate checks over succinct proofs (true/false + units). Always
**bound input sizes** to stay deterministic.

> All capability calls must respect **length caps** and **gas hooks** configured in
> `capabilities/config.py`. Contracts should validate sizes before calling into shims.

---

## 6) Patterns & recipes

### 6.1 Deterministic math & bounds

- Use only integer arithmetic (see `stdlib.math.safe_uint`). Always check for overflows or
saturate (safest).
- Bound loops by input sizes; reject inputs beyond limits with clear messages.

### 6.2 Replay protection

- For action approvals (multisig, permit), tie approval to a **nonce** stored per
signer/subject and increment or mark as consumed.
- Recommended key: `b"nonce:" + addr` → u64. For one-shot ops, `b"ms:nonce:" + ascii(n)`.

### 6.3 Timelocks

- Queue operations with `{op_id → eta_height}`, where `op_id = keccak(subject_bytes)`.
- Execution requires `current_height >= eta` (height taken from the `TxContext`).

### 6.4 Migration

- Add a **version sentinel** key: `b"v:1"`. Upgrades bump to `b"v:2"` and write any
compatibility shims. Avoid changing existing key semantics in place.

### 6.5 Read-only views

- Offer explicit getters for off-chain consumers: `get_balance(addr)`, `get_nonce(addr)`,
`get_commitment(id)`. These should be **pure** and gas-cheap.

---

## 7) Testing guidance

- **Local VM** tests (unit): compile and exercise functions with a fake state.
- **Property tests**: verify invariants like conservation of balances, no reentrancy,
nonce monotonicity, idempotent encode/decode of manifests/ABI.
- **Integration**: deploy on devnet; run end-to-end flows (AICF, DA, randomness).

---

## 8) Manifests & verification

- Every package has a manifest (see `contracts/schemas/manifest.schema.json`) containing:
- `name`, `version`, `abi`, **code hash**, optional metadata/resources.
- **Verify** by re-compiling source and matching the on-chain code hash using
`contracts/tools/verify.py` (talks to `studio-services`).

---

## 9) Security checklist (essentials)

- [ ] Access control for all sensitive mutations (owner/roles/multisig/timelock).
- [ ] Nonce/replay protection where intent is off-chain (permits/multisig).
- [ ] Input size caps for every bytes/array input; bounded loops.
- [ ] Event coverage for critical state changes (transfers, approvals, upgrades).
- [ ] Stable storage keys; backward-compatible migrations.
- [ ] Exact gas awareness on hot paths; avoid unbounded copying.
- [ ] No assumptions about address length; treat as opaque bytes.
- [ ] Deterministic use of block height / randomness beacon (never wall-clock).

---

## 10) Minimal examples

### 10.1 Storage key usage (Token20 balance)
```python
from stdlib.storage import get, set
from stdlib.abi import require
from stdlib.math.safe_uint import uadd, usub

def _bal_key(addr: bytes) -> bytes:
  return b"tok:bal:" + addr

def balance_of(addr: bytes) -> int:
  b = get(_bal_key(addr))
  return int.from_bytes(b, "big") if b else 0

def _write_u256(k: bytes, v: int) -> None:
  require(v >= 0, "neg")
  set(k, v.to_bytes(32, "big"))

def transfer(src: bytes, dst: bytes, value: int) -> None:
  require(value >= 0, "bad_value")
  sb = balance_of(src)
  db = balance_of(dst)
  require(sb >= value, "insufficient_balance")
  _write_u256(_bal_key(src), usub(sb, value))
  _write_u256(_bal_key(dst), uadd(db, value))

10.2 Event emission (Approval)

from stdlib.events import emit

def approve(owner: bytes, spender: bytes, value: int) -> None:
    # ... update allowance in storage ...
    emit(b"Approval", { "owner": owner, "spender": spender, "value": value })

10.3 Timelock ETA guard

from stdlib.abi import require
from stdlib.storage import get
from stdlib.runtime.context import get_block_height

def _eta_key(op_id: bytes) -> bytes:
    return b"tl:eta:" + op_id

def execute(op_id: bytes, subject: bytes) -> None:
    eta_b = get(_eta_key(op_id)) or b"\x00"
    eta = int.from_bytes(eta_b, "big")
    require(get_block_height() >= eta, "eta_not_reached")
    # ... execute subject ...


⸻

11) Glossary
	•	ABI — Application Binary Interface; function/event schema and encoding rules.
	•	KV — Key/Value store (bytes→bytes) exposed to contracts.
	•	Permit — Off-chain signed intent allowing on-chain state change.
	•	Capabilities — Deterministic host-provided syscalls (AI/Quantum/DA/Randomness/zk).

⸻

12) References
	•	spec/abi.schema.json — ABI schema reference
	•	spec/params.yaml — chain parameters (gas tables, limits)
	•	vm_py/specs/* — VM determinism, IR, ABI details
	•	contracts/stdlib/* — reusable modules implemented using the rules above
	•	contracts/tools/* — compile, package, deploy, verify tooling

⸻

Design for clarity and determinism. Prefer explicit state machines, small and stable
schemas, and comprehensive tests. If a feature can be implemented with fewer moving parts,
it likely should be.
