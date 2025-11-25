# Contract Patterns (VM(Py))
**Topics:** upgrades, proxy pinning, access control, pausability  
**Status:** Recommended practices (v1)  
**Scope:** Deterministic Python VM contracts using `stdlib` (no nondeterminism; see `docs/vm/SANDBOX.md`)

This guide catalogs pragmatic patterns for production contracts on the Animica VM(Py) runtime. Patterns here are **deterministic-first** and compatible with the chain’s canonical encoding and syscall model (see `docs/spec/*` and `docs/vm/*`).

---

## 0) Conventions & Storage Layout

- **Key namespaces:** prefix storage keys by feature and version to avoid collisions:
  - `b"cfg:v1:..."` — configuration (owner, pausers, limits)
  - `b"state:v1:..."` — mutable state
  - `b"meta:v1:..."` — metadata (impl code-hash, ABI version)
  - `b"nonce:v1:<addr>"` — per-address meta-tx nonces
- **Events:** keep short ASCII names and small, typed args. Example topics:
  - `b"Upgraded"` (impl_hash)
  - `b"Paused"`, `b"Unpaused"`
  - `b"RoleGranted"`, `b"RoleRevoked"`
- **ABI compatibility:** never reorder args or change meanings in-place. Prefer additive changes behind feature flags or new methods.

---

## 1) Upgrade Patterns

The VM treats contract **code as immutable**, but you can achieve upgradability via **indirection**. We recommend one of the following:

### A) **Registry + Facade (Most Robust Today)**
Clients call a **stable facade** contract. The facade reads an **implementation code hash** (or target address) from storage and **routes semantics** (not bytecode execution) by having **small wrapper methods** that call the active logic (implemented *in the same contract* but gated by config & branching). This avoids dynamic dispatch but lets you **pin versions** and **gate new behavior**:

- `meta:v1:impl_hash` — pinned implementation hash (pure metadata)
- `cfg:v1:impl_epoch` — monotonically increasing epoch/version
- Facade methods consult `impl_epoch` to choose behavior paths (v1 vs v2).

> This is essentially **feature-flagged forward-compat** in a single artifact with governance controlling which branch is active. It preserves determinism and avoids “delegatecall”-like complexity.

### B) **Address Registry (Client-Level Indirection)**
Publish a **name → address** mapping in a small on-chain Registry. Dapps resolve the current implementation address at read time. Governance updates the mapping via a two-phase timelock (see §2).

Pros: simple, safe; Cons: consumers must **resolve via registry** (or cache with expiry).

### C) **UUPS/Transparent-Proxy (Future)**
When cross-contract calls become available in VM(Py) (see `execution/runtime/contracts.py` roadmap), you can implement a classic storage-preserving proxy. Until then, prefer (A) or (B).

#### Two-Phase Upgrade (Timelocked)
For any upgrade indirection, use a **two-phase** process:

1. **Propose**: store `meta:v1:pending_impl_hash` and `meta:v1:eta`. Emit `UpgradedProposed`.
2. **Execute**: after `now ≥ eta`, move `pending_impl_hash → impl_hash` and bump `impl_epoch`. Emit `Upgraded`.

**Guard Rails**
- Enforce **ABI-compatibility** flag in the proposal.
- Require **multi-sig** (see §3) to `propose` and `execute`.
- Record a **link** to verification artifacts (DA commitment) for reproducibility.

**Example (upgrade bookkeeping only)**
```python
from stdlib import storage, events, abi

PAUSE_KEY = b"cfg:v1:paused"
IMPL_HASH_KEY = b"meta:v1:impl_hash"
PENDING_HASH_KEY = b"meta:v1:pending_impl_hash"
ETA_KEY = b"meta:v1:eta"
EPOCH_KEY = b"cfg:v1:impl_epoch"

def _now() -> int:
    # Block timestamp from context once exposed; until then, use head height proxy where applicable.
    # Placeholder: enforce ETA via governance process external to contract if timestamp not available.
    return 0

def propose_upgrade(new_hash: bytes, eta: int, caller: bytes):
    _only_admin(caller)
    abi.require(len(new_hash) == 32, b"bad hash")
    storage.set(PENDING_HASH_KEY, new_hash)
    storage.set(ETA_KEY, eta)
    events.emit(b"UpgradedProposed", {b"hash": new_hash, b"eta": eta})

def execute_upgrade(caller: bytes):
    _only_admin(caller)
    eta = int.from_bytes(storage.get(ETA_KEY) or b"\x00", "big")
    abi.require(_now() >= eta, b"too early")
    new_hash = storage.get(PENDING_HASH_KEY)
    abi.require(new_hash is not None, b"no proposal")
    storage.set(IMPL_HASH_KEY, new_hash)
    epoch = int.from_bytes(storage.get(EPOCH_KEY) or b"\x00", "big") + 1
    storage.set(EPOCH_KEY, epoch.to_bytes(8, "big"))
    storage.set(PENDING_HASH_KEY, b"")
    storage.set(ETA_KEY, (0).to_bytes(8, "big"))
    events.emit(b"Upgraded", {b"hash": new_hash, b"epoch": epoch})


⸻

2) Proxy Pinning & Reproducibility

Whether you route via a facade or registry, pin what you run:
	•	Pin implementation hash (e.g., VM bytecode/IR hash) in meta:v1:impl_hash.
	•	Pin ABI version in meta:v1:abi_version.
	•	Emit both in Upgraded events.
	•	Publish artifacts (source, manifest, IR) to DA and record the commitment. Store the 32-byte commitment key:
	•	meta:v1:artifact_commit → NMT root (see DA syscall).

Client Verification Checklist
	1.	Resolve address/facade.
	2.	Read impl_hash, artifact_commit, impl_epoch.
	3.	Fetch artifact from DA, recompute hash, compare.
	4.	Enforce minimum impl_epoch per app policy.

⸻

3) Access Control (Roles & Multisig)

Role Model

Define a minimal set:
	•	OWNER — can grant/revoke roles; can transfer ownership.
	•	ADMIN — can propose/execute upgrades; manage pausers/operators.
	•	OPERATOR — can mutate day-to-day config; no upgrades.
	•	PAUSER — can pause/unpause (see §4).

Store as sets keyed by role:
	•	cfg:v1:role:owner -> bytes(address)
	•	cfg:v1:role:admin:<addr> -> b"1"
	•	cfg:v1:role:operator:<addr> -> b"1"
	•	cfg:v1:role:pauser:<addr> -> b"1"

Multisig (Threshold on Permits)

Until native multi-sig contracts are available, use off-chain aggregation of PQ signatures into a single permit checked on-chain:
	•	Message = H("permit/animica" | contract_addr | method | params_hash | nonce)
	•	Store per-method nonce:v1:<method> and require monotonic increments.
	•	The calldata includes (approvals, threshold), where approvals is a deterministic concatenation of signer addresses and their signatures; the contract verifies that:
	•	All signers hold the required role.
	•	len(unique(signers)) ≥ threshold.
	•	Nonce matches and then increments.

This mirrors “meta-tx + threshold” and keeps deterministic on-chain logic.

Guards

def _only_owner(caller: bytes):
    abi.require(storage.get(b"cfg:v1:role:owner") == caller, b"not owner")

def _has_role(role_prefix: bytes, who: bytes) -> bool:
    return (storage.get(role_prefix + b":" + who) or b"") == b"1"

def _only_admin(caller: bytes):
    abi.require(_has_role(b"cfg:v1:role:admin", caller), b"not admin")


⸻

4) Pausability (Circuit Breaker)

Implement a global pause and optionally feature-scoped pauses:
	•	cfg:v1:paused -> b"0"/b"1"
	•	cfg:v1:pause:<feature> -> b"0"/b"1"

Modifiers (guards)

def _when_not_paused():
    abi.require((storage.get(PAUSE_KEY) or b"0") == b"0", b"paused")

def pause(caller: bytes):
    abi.require(_has_role(b"cfg:v1:role:pauser", caller), b"not pauser")
    storage.set(PAUSE_KEY, b"1")
    events.emit(b"Paused", {})

def unpause(caller: bytes):
    _only_admin(caller)  # stricter to unpause
    storage.set(PAUSE_KEY, b"0")
    events.emit(b"Unpaused", {})

Pattern
	•	Write paths (transfer, configure, enqueue) check _when_not_paused().
	•	Read-only getters remain callable while paused.
	•	Selective pause: guard only risky endpoints (e.g., “withdraw”, “upgrade”).

⸻

5) Permit / Meta-Tx Pattern (Optional)

Support gasless ops or multisig by validating a permit:
	•	Domain: "permit:animica/<chainId>"
	•	Typed fields (canonically encoded):
	•	contract, method, params_hash, nonce, expiry
	•	Verify PQ signatures (Dilithium3/SPHINCS+) against role holders.
	•	Enforce expiry ≥ now, nonce unused, then mark nonce consumed.

Store:
	•	nonce:v1:<signer> → last used
	•	cfg:v1:permit:domain → static tag for versioning

⸻

6) Defensive Patterns
	•	Reentrancy: today there are no cross-contract calls; once available, use a reentrancy guard:
	•	state:v1:entered -> b"0"/b"1"; set before, unset after.
	•	Rate limits: per-address rolling counters:
	•	state:v1:rl:<addr>:window, state:v1:rl:<addr>:count
	•	Bounds & clamping: always validate sizes before syscalls (DA pin, AI/Quantum enqueue).
	•	Determinism: never branch on unbounded bytes without explicit caps; all inputs must be canonicalized.

⸻

7) Events & Telemetry

Emit compact, indexable events for off-chain indexers:
	•	Upgraded {hash, epoch}
	•	Paused {} / Unpaused {}
	•	RoleGranted {role, addr} / RoleRevoked {role, addr}
	•	ParamChanged {key, old, new} (optional)

⸻

8) Checklists

Upgrade (Two-Phase)
	•	Proposed impl_hash equals DA artifact hash.
	•	ABI compatibility flag set and validated.
	•	Timelock eta ≥ minimum (policy).
	•	Executed after eta; epoch incremented; events emitted.

Access Control
	•	OWNER rotated via on-chain transferOwnership.
	•	ADMIN/PAUSER sets reviewed; no EOA single points (use threshold).
	•	All privileged methods guarded.

Pausability
	•	All state-changing endpoints gate _when_not_paused.
	•	Unpause requires stronger role.
	•	Pause emits events; idempotent.

Reproducibility
	•	Artifact pinned (DA commitment) + impl_hash stored.
	•	CI verifies artifact hash vs chain state before deploy.
	•	Version/epoch displayed in UI.

⸻

9) Minimal Facade Skeleton (Putting It Together)

from stdlib import storage, events, abi

# --- Keys
OWNER = b"cfg:v1:role:owner"
PAUSER = b"cfg:v1:role:pauser:"
ADMIN  = b"cfg:v1:role:admin:"
PAUSE  = b"cfg:v1:paused"
IMPLH  = b"meta:v1:impl_hash"
EPOCH  = b"cfg:v1:impl_epoch"

# --- Setup (one-time)
def init(owner: bytes, impl_hash: bytes):
    abi.require(storage.get(OWNER) is None, b"already init")
    storage.set(OWNER, owner)
    storage.set(ADMIN + owner, b"1")
    storage.set(PAUSE, b"0")
    storage.set(IMPLH, impl_hash)
    storage.set(EPOCH, (1).to_bytes(8, "big"))
    events.emit(b"Initialized", {b"owner": owner, b"impl": impl_hash})

# --- Guards
def _only_owner(caller: bytes): abi.require(storage.get(OWNER) == caller, b"not owner")
def _only_admin(caller: bytes): abi.require((storage.get(ADMIN + caller) or b"") == b"1", b"not admin")
def _when_not_paused(): abi.require((storage.get(PAUSE) or b"0") == b"0", b"paused")

# --- Roles
def grant_admin(who: bytes, caller: bytes):
    _only_owner(caller)
    storage.set(ADMIN + who, b"1")
    events.emit(b"RoleGranted", {b"role": b"admin", b"addr": who})

def revoke_admin(who: bytes, caller: bytes):
    _only_owner(caller)
    storage.set(ADMIN + who, b"0")
    events.emit(b"RoleRevoked", {b"role": b"admin", b"addr": who})

# --- Pause
def pause(caller: bytes):
    abi.require((storage.get(PAUSER + caller) or b"") == b"1", b"not pauser")
    storage.set(PAUSE, b"1")
    events.emit(b"Paused", {})

def unpause(caller: bytes):
    _only_admin(caller)
    storage.set(PAUSE, b"0")
    events.emit(b"Unpaused", {})

# --- Example write op guarded by pause
def set_limit(limit: int, caller: bytes):
    _when_not_paused()
    _only_admin(caller)
    storage.set(b"cfg:v1:limit", limit.to_bytes(8, "big"))
    events.emit(b"ParamChanged", {b"key": b"limit"})


⸻

10) References
	•	docs/spec/UPGRADES.md — network-level rules and feature flags
	•	docs/vm/SANDBOX.md — determinism, allowed stdlib, gas model
	•	capabilities/specs/* — syscalls & host guarantees
	•	docs/vm/CAPABILITIES.md — contract-facing syscall surface

