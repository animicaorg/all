# Contract Patterns: Upgrade, Proxy Pinning, Pausability, Role Design

This document captures battle-tested patterns for Animica contracts on the deterministic Python-VM. These patterns are designed to be:
- **Deterministic** (no wall-clock, no non-deterministic IO),
- **Auditable** (clear invariants, stable storage keys, pinned code hashes),
- **Governable** (roles, multisig, timelock),
- **Safe-by-default** (pause switches; bounded inputs; replay protection).

> Use these patterns and their checklists to keep code simple, explicit, and verifiable.

---

## 1) Upgrade Strategies

### 1.1 Choose the simplest viable approach

1. **Immutable Contract (preferred)**  
   - Deploy new versions to **new addresses**.  
   - Migrate state via explicit migration calls/tools if needed.
   - Pros: trivial to audit; zero proxy complexity.  
   - Cons: consumer endpoints must switch address.

2. **Pinned-Proxy (recommended when upgradeability is required)**  
   - A thin proxy **stores a target address and an expected code hash**.  
   - Calls are forwarded only if the target’s **code hash matches** the pinned hash.  
   - Upgrades require updating **both** target and pinned hash in a governed process.

3. **Upgradeable with Policy Gates**  
   - Same as Pinned-Proxy, but the setter functions are gated by **multisig + timelock** and covered by **pause** semantics.

> **Never** use a mutable proxy without code-hash pinning. Unpinned proxies are a common root cause of supply-chain attacks.

### 1.2 Canonical upgrade flow (governed)

1. **Propose**: Create a manifest of the new implementation: `(name, version, abi, code_hash)`.  
2. **Queue**: Timelock `set_target(new_target, expected_hash)` encoded as `subject = keccak(abi_encode(...))`, store `eta_height`.  
3. **Verify**: On or after `eta_height`, verify the **implementation code hash** matches the manifest.  
4. **Approve**: Multisig executes the queued operation; proxy writes `target` and `pinned_hash`.  
5. **Post-checks**: Read-only smoke calls; events confirm the new implementation.  
6. **Rollback plan**: Keep a previous (n−1) manifest ready; a queued downgrade path exists.

**Events**  
- `UpgradeProposed`: `{ "target": address, "code_hash": bytes32, "eta": u64 }`  
- `Upgraded`: `{ "target": address, "code_hash": bytes32 }`

---

## 2) Proxy Pinning Pattern

The proxy **must** verify implementation integrity on every update and optionally on each call.

### 2.1 State keys
- `b"proxy:target"` → address bytes  
- `b"proxy:pinned_hash"` → bytes32

### 2.2 Guarded setter
```python
from stdlib.abi import require
from stdlib.storage import get, set
from stdlib.events import emit
from stdlib.hash import keccak256

def _k_target(): return b"proxy:target"
def _k_hash():   return b"proxy:pinned_hash"

def set_target(new_target: bytes, expected_hash: bytes) -> None:
    # Governance gate applied externally (owner/role + timelock + multisig)
    require(len(new_target) > 0, "bad_target")
    require(len(expected_hash) == 32, "bad_hash")
    # Optional: verify off-chain beforehand; re-check on-chain via code-hash registry if available.
    set(_k_target(), new_target)
    set(_k_hash(), expected_hash)
    emit(b"Upgraded", { "target": new_target, "code_hash": expected_hash })

2.3 Dispatch (conceptual)
	•	The VM runtime or host adapter resolves calls to target.
	•	Before forwarding, compare code_hash(target) against pinned_hash; revert if mismatch.
	•	Keep the proxy surface minimal: set_target, get_target, get_pinned_hash.

Anti-patterns
	•	Unpinned or hash-ignored proxy.
	•	Single-EOA control over upgrades.
	•	Allowing target change and hash change in separate transactions (do both atomically).

⸻

3) Pausability (Circuit Breaker)

A global pause can stop state-changing functions during incidents.

3.1 State & events
	•	Key: b"pause:global" → b"1" (paused) or absent (unpaused)
	•	Events:
	•	Paused: { "by": address }
	•	Unpaused: { "by": address }

3.2 Pattern

from stdlib.storage import get, set
from stdlib.abi import require
from stdlib.events import emit

def _k_pause(): return b"pause:global"

def paused() -> bool:
    return (get(_k_pause()) == b"1")

def _require_not_paused():
    require(not paused(), "paused")

def pause(by: bytes) -> None:
    # Gate: ROLE_PAUSER or owner
    set(_k_pause(), b"1")
    emit(b"Paused", {"by": by})

def unpause(by: bytes) -> None:
    # Gate: ROLE_PAUSER or owner (optionally stricter)
    set(_k_pause(), b"")
    emit(b"Unpaused", {"by": by})

def transfer(src: bytes, dst: bytes, value: int) -> None:
    _require_not_paused()
    # ... mutate balances ...

Coverage
	•	Gate all mutating entrypoints (transfer, mint, burn, execute, set_target, etc.).
	•	Allow view methods while paused.

Options
	•	Sub-system pauses: b"pause:minting", b"pause:escrow".
	•	Rate-limited unpause: require timelock or dual approvals to unpause.

⸻

4) Role Design (RBAC)

Bytes32 role IDs, explicit admin role, and least privilege.

4.1 IDs & naming
	•	Deterministic IDs: ROLE_ADMIN = keccak256(b"ROLE_ADMIN")[:32], similarly for domain roles:
	•	ROLE_MINT, ROLE_PAUSER, ROLE_UPGRADER, ROLE_ESCROW_ARBITER.
	•	Store grants at: b"acl:role:" + role_id + b":" + addr → b"1".

4.2 Admin topology
	•	A single ADMIN role that can grant/revoke other roles.
	•	Optionally, a role-specific admin (e.g., ROLE_MINT_ADMIN) if delegating authority is needed.
	•	Prefer multisig for ADMIN.

4.3 Gates

from stdlib.abi import require
from stdlib.storage import get

def _has_role(role_id: bytes, addr: bytes) -> bool:
    return get(b"acl:role:" + role_id + b":" + addr) == b"1"

def _only_role(role_id: bytes, caller: bytes) -> None:
    require(_has_role(role_id, caller), "missing_role")

def mint(caller: bytes, to: bytes, amount: int) -> None:
    _only_role(ROLE_MINT, caller)
    # ... mutate state ...

Best practices
	•	Narrow roles to the minimal surface: ROLE_UPGRADER only calls set_target.
	•	Rotateable keys: allow revocation without affecting other roles.
	•	Observability: emit RoleGranted / RoleRevoked.

⸻

5) Governance: Multisig + Timelock

Combine multisig for approvals with a timelock for observability and reaction time.
	•	Multisig enforces threshold N-of-M approvals; protects against single-key loss.
	•	Timelock provides a cooldown window (eta_height) before execution.
	•	Sensitive ops (upgrade, pauser changes, treasury drains) must pass both.

Subject encoding

subject = keccak256(encode({
  "contract": target_addr,
  "method": "set_target",
  "args": [new_target, expected_hash],
  "nonce": admin_nonce
}))

Store eta under b"tl:eta:" + subject. Require height >= eta at execution.

⸻

6) Permits & PQ Signatures (off-chain approvals)

Use permit-style approvals for spend/intents:
	•	Off-chain signature across a domain-separated message (see spec/domains.yaml).
	•	Contract checks nonce freshness, deadline, and preconditions.
	•	Signature verification occurs in the wallet / node; the contract ensures replay safety.

Nonce keys
	•	b"nonce:" + owner → u64 (monotonic)
	•	For one-shot ops (multisig execution): b"ms:nonce:" + ascii(nonce) → b"1" when consumed

⸻

7) Capability Gating (AI/Quantum/DA/Randomness/zk)
	•	Validate input sizes and model/circuit identifiers before syscall.
	•	Persist task_id and consume results next block only.
	•	Gate enqueue with a role (ROLE_COMPUTE) or fees escrow.
	•	Emit TaskEnqueued / TaskResult.
	•	For DA, persist commitment and provide read-only accessors.

⸻

8) Storage Migration Pattern
	•	Version sentinel: b"v:1" → present.
	•	On upgrade, write b"v:2", run a one-shot migration guarded by a b"migrated:2" flag.
	•	Migrations must be idempotent and bounded (scan small key-ranges, or chunk).
	•	Emit Migrated: { "from": 1, "to": 2 }.

Example

def migrate_v2() -> None:
    if get(b"migrated:2") == b"1":
        return
    # transform keys, e.g., balances to fixed 32-byte encoding
    # ...
    set(b"migrated:2", b"1")
    emit(b"Migrated", {"from": 1, "to": 2})


⸻

9) Testing & Verification Patterns
	•	Property tests: conservation of supply, pause coverage (all mutators revert when paused), RBAC gates.
	•	Fuzz: ABI decode/encode, manifest & proxy state transitions, multisig subject hashing.
	•	Integration: devnet deploy, upgrade with timelock, verify source → code hash match.
	•	Negative tests: wrong code hash update, unpause without authority, replayed nonce.

⸻

10) Anti-Patterns (avoid)
	•	Unpinned proxy or updating target/hash in separate txs.
	•	Single-EOA admin for upgrades/pauses.
	•	Pausable pattern that misses some mutators.
	•	No nonces for off-chain intents (permits/multisig).
	•	Unbounded loops or large copying without gas checks.
	•	Wall-clock assumptions; use block height / beacon only.
	•	Dynamic role IDs derived from user input.

⸻

11) Checklists

Upgrade (Pinned-Proxy)
	•	set_target requires governance (multisig + timelock).
	•	pinned_hash updated atomically with target.
	•	Post-upgrade smoke calls pass; Upgraded event emitted.
	•	Rollback path queued.

Pausability
	•	All mutators gated by _require_not_paused().
	•	Pauser role scoped; unpause requires stricter approvals or timelock.
	•	Coverage tests confirm reverts while paused.

RBAC
	•	Fixed role IDs; clear admin hierarchy.
	•	Least-privilege gates at each sensitive function.
	•	Events for grant/revoke; rotation tested.

Permits/Multisig
	•	Domain-separated sign bytes (chainId, contract addr, nonce, deadline).
	•	Monotonic nonce per subject; replay tests included.

Capabilities
	•	Input size caps; model/circuit whitelist; fees/escrow if applicable.
	•	Result read is next-block deterministic; events emitted.

⸻

12) Snippets (ready-to-use)

12.1 Proxy getters

def get_target() -> bytes:
    from stdlib.storage import get
    return get(b"proxy:target") or b""

def get_pinned_hash() -> bytes:
    from stdlib.storage import get
    return get(b"proxy:pinned_hash") or b""

12.2 Timelock guard

from stdlib.abi import require
from stdlib.storage import get, set
from stdlib.events import emit
from stdlib.hash import keccak256
from stdlib.runtime.context import get_block_height

def queue(subject: bytes, eta: int) -> None:
    set(b"tl:eta:" + subject, eta.to_bytes(8, "big"))
    emit(b"Queued", {"op": subject, "eta": eta})

def can_execute(subject: bytes) -> bool:
    eta_b = get(b"tl:eta:" + subject) or b"\x00"
    eta = int.from_bytes(eta_b, "big")
    return get_block_height() >= eta

12.3 Pause decorator (lightweight)

def only_when_active(fn):
    def inner(*args, **kwargs):
        _require_not_paused()
        return fn(*args, **kwargs)
    return inner


⸻

13) References
	•	contracts/stdlib/access/ownable.py, contracts/stdlib/access/roles.py
	•	contracts/stdlib/control/pausable.py, contracts/stdlib/control/timelock.py
	•	contracts/stdlib/upgrade/proxy.py
	•	contracts/stdlib/capabilities/*
	•	spec/abi.schema.json, spec/domains.yaml, spec/opcodes_vm_py.yaml
	•	contracts/tools/* (build, deploy, verify)

Design for clarity. Pin code, gate power, add pause, and test the path you plan to use in production.
