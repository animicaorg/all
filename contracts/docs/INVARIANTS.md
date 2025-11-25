# INVARIANTS — Standard Library (stdlib) Components

This document states **formal invariants** and **required properties** for the Animica Python-VM standard library contracts. It is intended for auditors, contributors, and dapp authors who rely on these modules’ safety guarantees.

The invariants here are enforced by:
- Deterministic VM semantics (`vm_py/specs/*`)
- Canonical ABI encoding (`spec/abi.schema.json`)
- Module-local unit tests (`contracts/tests/*`)
- Cross-module property tests & integration tests (`tests/property/*`, `tests/integration/*`)

---

## Notation & Global Assumptions

- Let `addr ∈ Address`, `b ∈ Bytes`, `u ∈ UInt`, `role ∈ Bytes32`, `task_id ∈ Bytes`, `ns ∈ Namespace`.
- Storage maps are written like `balance: Address → UInt`, `allowance: (Address × Address) → UInt`.
- `ΣX` denotes the sum over a finite set.
- VM guarantees: **serial**, **deterministic**, **gas-bounded**, **no ambient I/O**, **no nondeterminism** except via explicit syscalls (capabilities), which are deterministically **delayed** (next-block visibility).
- Events are canonical (name, field names/types) and emitted only after the state change they describe.
- “Unchanged unless…” explicitly constrains writes to named storage keys.
- All UInt arithmetic uses **saturating/checked** semantics as specified by `stdlib/math/safe_uint.py`.

---

## Cross-Cutting Invariants (All stdlib modules)

1. **Determinism**
   - For any function `f` and inputs `(state, args)`, execution is **purely a function** of `(state, args)` and produces the same `(new_state, events, return)` across all nodes.
   - Capability reads (AI/Quantum/DA/Randomness/zkverify) are **not** visible within the same block as enqueue/produce; visibility flips only on/after the **next block**.

2. **Storage Domain Separation**
   - Each module uses **distinct key prefixes** (`b"token:"`, `b"escrow:"`, `b"roles:"`, etc.). No module writes to another’s namespace.

3. **Event-After-State**
   - If an event `E` describes a state change `Δ`, then `Δ` must be committed **before** `E` is emitted (program order), and emitted exactly once per successful state write.

4. **Access Control Respect**
   - Functions guarded by `only_owner` or role checks **must revert** if caller lacks authorization; no partially performed state changes before revert.

5. **No Silent Wrap/Underflow**
   - All `UInt` operations either succeed under bounds or **revert**; no implicit wrap.

6. **Replay Resistance for Sign-Based Flows**
   - Any signature-based authorizations (e.g., `permit`) consume a **nonce**, strictly **monotonic per-signer**: `nonce[sender]` increments by one on acceptance.

---

## Access Modules

### `access/ownable.py`
- **Single Owner Invariant**: `owner ∈ Address ∪ {∅}`; at most one owner stored.
- **Init-Once**: `init(owner)` may set owner only when owner is `∅`. Thereafter, must revert unless explicit “re-initialize” is defined (not present by default).
- **Transfer**: `transferOwnership(newOwner)` sets `owner := newOwner` iff caller is current `owner` and `newOwner ≠ ∅`.
- **Renounce**: `renounceOwnership()` sets `owner := ∅` iff caller is current owner.
- **Event Consistency**: `OwnershipTransferred{from,to}` emitted iff and only if `owner` changes.

### `access/roles.py`
- **Role Set Integrity**: `hasRole[role][addr] ∈ {true,false}`; default `false`.
- **Grant/Revoke Authorization**: Only an address satisfying the module’s admin policy can `grant`/`revoke`. (Base policy: owner-admin or role-admin mapping if configured.)
- **Idempotence**: Repeated grant of existing membership, or revoke of absent membership, is a **no-op** (no state change, optional event suppression or dedup).
- **Event Consistency**: `RoleGranted{role,account,admin}` iff membership flips `false→true`; `RoleRevoked{role,account,admin}` iff `true→false`.

---

## Control Modules

### `control/pausable.py`
- **Binary State**: `paused ∈ {true,false}`; initialized `false` unless otherwise specified.
- **Monotonic Segments**: `pause()` changes `paused: false→true`; `unpause()` changes `true→false`; no other values or intermediate states.
- **Guarded Functions**: Any function marked `whenNotPaused` must revert if `paused==true`.
- **Events**: `Paused{caller}` iff `false→true`; `Unpaused{caller}` iff `true→false`.

### `control/timelock.py`
- **Queue Discipline**: Operations (opaque payload `op_id`) can be **queued** with `eta = now + delay`.
- **No Early Execute**: `execute(op_id)` must revert unless `now ≥ eta`.
- **Uniqueness**: A queued `op_id` cannot collide; re-queue with the same key must either overwrite deterministically (with event) or revert—module uses **idempotent replace with fresh eta** rules as documented in source.
- **Cancellation**: `cancel(op_id)` removes the queued record iff it exists and caller is authorized.
- **Event Sequence**: `Queued{op_id,eta}` → later `Executed{op_id}` or `Cancelled{op_id}`, never both.

---

## Math Module

### `math/safe_uint.py`
- **Closure & Totality**: For supported widths (as defined), `add/sub/mul/div` are defined for all inputs, reverting on overflow, divide-by-zero, or underflow.
- **Order-Preserving**: If `a ≤ b` then `a + c ≤ b + c` for all `c` where sums are defined (no overflow).
- **No Rounding Surprises**: Division semantics are **floor** with exactness checks where exposed.

---

## Token Modules

### `token/fungible.py` (Animica-20)
- **Non-Negative Balances**: `∀a. balance[a] ≥ 0`.
- **Supply Conservation**: `Σ balance[a] == totalSupply`.
- **Transfer Postconditions**: For `transfer(from,to,amt)`:
  - Require `balance[from] ≥ amt`.
  - New balances: `balance[from]' = balance[from] - amt`, `balance[to]' = balance[to] + amt`.
  - All others unchanged.
  - `Transfer{from,to,amt}` emitted iff `amt>0`, after balances update.
- **Allowance Semantics**:
  - `allowance[owner,spender] ≥ 0`.
  - `approve(owner→spender, amt)` sets allowance exactly to `amt` (overwrite semantics).
  - `transferFrom(owner→to, amt)` requires `allowance[owner,caller] ≥ amt` and decrements it by `amt`.
- **Zero-Address Rules**: Transfers **to** or **from** zero-address are forbidden unless explicitly defined as mint/burn in extensions.

### `token/mintable.py`
- **Mint**:
  - Authorized (owner or role) only.
  - Post: `balance[to]' = balance[to] + amt`, `totalSupply' = totalSupply + amt`.
  - Events: `Transfer{0x00.., to, amt}`.
- **Burn**:
  - Authorized (owner/role or holder via `burnSelf` variant).
  - Post: `balance[from]' = balance[from] - amt`, `totalSupply' = totalSupply - amt`.
  - Events: `Transfer{from, 0x00.., amt}`.

### `token/permit.py` (PQ-aware off-chain approvals)
- **Domain Separation**: Sign domain includes `(chainId | contract_address | "Permit" | nonce[owner])`.
- **Nonce Monotonicity**: `nonce[owner]' = nonce[owner] + 1` exactly on successful permit use.
- **Single-Use**: A signature with `(owner,spender,value,deadline,nonce)` cannot succeed twice.
- **Deadline**: Must revert if `now > deadline`.
- **Allowance Setting**: On success, `allowance[owner,spender]' = value` (overwrite).

---

## Treasury Modules

### `treasury/escrow.py`
- **Funds Conservation**:
  - Ledger: `held[escrow_id] ≥ 0`, `balance[user]` adjusted only via explicit debit/credit helpers.
  - `Σ held + Σ free_balances == Σ deposits - Σ finalized_withdrawals` (within this module’s domain).
- **States**: `state ∈ {OPEN, DISPUTED, RESOLVED}` with valid transitions:
  - `OPEN → DISPUTED → RESOLVED`; direct `OPEN → RESOLVED` allowed by both-party confirmation.
- **No Partial Loss**: Assets locked in `OPEN` or `DISPUTED` cannot be lost; on `RESOLVED`, funds move according to resolution rule, then escrow record becomes terminal.
- **Events**: `Deposited`, `Disputed`, `Resolved`, `Withdrawn` align with state changes and amounts.

### `treasury/splitter.py`
- **Shares Integrity**: `shares[i] ≥ 0`, `Σ shares[i] > 0`.
- **Proportionality**: A distribution of `amount` yields payments `p_i = floor(amount * shares[i] / Σshares)`, and remainder `r = amount - Σ p_i` handled per documented policy (e.g., remainder to smallest-index or left in buffer). Policy is **fixed** and deterministic.
- **No Negative Payouts**: `p_i ≥ 0` for all `i`.

---

## Registry Module

### `registry/name_registry.py`
- **Mapping Totality**: `mapping: bytes32 → Address ∪ {∅}`.
- **Set/Unset**:
  - `set(name, addr)` sets `mapping[name] := addr` (addr may be `∅` to clear).
  - Authorized caller policy applies (owner/role or open, as configured).
- **Event Fidelity**: `NameSet{name, addr}` emitted iff the stored value changes.
- **No Ghost Entries**: A `get(name)` must reflect the exact stored value; no caching outside storage map.

---

## Upgrade Module

### `upgrade/proxy.py` (Predictable Proxy, Code Hash Pinned)
- **Immutability of Pin**: `impl_code_hash_pinned` is set at construction and **never changes**.
- **Delegate Target Integrity**: Active implementation’s **computed code hash** must equal the pinned hash; otherwise calls **revert**.
- **Admin-Only Upgrade Slot (if present)**:
  - If an upgrade entry-point exists, it only allows **switching address** where `code_hash(new_impl) == pinned_hash`. No arbitrary code.
- **Storage Layout Stability**:
  - Proxy uses fixed slots for admin/pin to avoid collision with implementation’s storage.
  - No writes to implementation’s reserved namespaces.

---

## Capabilities Modules

> These are thin wrappers that translate contract calls into **deterministic** host syscalls. All inputs are length-capped; results follow **next-block** visibility.

### `capabilities/ai_compute.py`
- **Deterministic Task ID**: `task_id = H(chainId | height | txHash | caller | payload)`; repeated enqueue with identical payload in the **same tx** yields same `task_id`; across different txs task_id differs via `txHash`.
- **Read Visibility**: `read_result(task_id)` returns `None` **until** the block after the provider’s proof is accepted; then returns a **stable byte string** thereafter.
- **Idempotent Consume**: Re-reading the same `task_id` yields identical bytes; wrapper itself does not mutate state.

### `capabilities/quantum.py`
- Same invariants as AI; additional **traps/QoS** constraints enforced off-chain are not exposed to the contract, but the result’s **deterministic availability** is.

### `capabilities/da_blob.py`
- **Commitment Correctness**: `pin(data, ns)` returns commitment `C` such that `verify(C, ns, data)` holds under DA rules (NMT root). The contract only stores `C`, never raw data.
- **Namespace Validity**: `ns` must satisfy range checks; invalid namespace reverts.

### `capabilities/randomness.py`
- **Beacon Read-Only**: `get_beacon()` returns the **finalized** beacon for the latest sealed round; value is immutable thereafter.
- **Commit/Reveal Helper**: If provided, helpers build commitments with strict **domain separation** and **timing** (commit before deadline; reveal within window), otherwise revert.

### `capabilities/zkverify.py`
- **Boolean, Deterministic**: `zk_verify(circuit, proof, input)` returns `True/False` with **no** side effects. For the same `(circuit,proof,input)` the outcome is stable forever.

---

## Utils

### `utils/events.py`
- **Canonical Names**: Event names are fixed strings; no dynamic composition.
- **Field Order & Types**: Exactly match ABI schema; encoder must not reorder or rename fields.

### `utils/bytes.py`
- **Length Guards**: Public helpers never allocate unboundedly; they revert on caps breach.
- **Round-Trip**: `hex_to_bytes(bytes_to_hex(x)) == x`.

---

## Module Interaction Invariants

1. **Pausable × Token**  
   If `paused==true`, all state-changing token methods **revert**; read-only views unaffected.

2. **Ownable/Role × Treasury**  
   Only authorized calls may `deposit/resolve/withdraw` as policy defines; unauthorized calls do not mutate escrow state.

3. **Permit × Allowance**  
   A valid permit **overwrites** `allowance[owner,spender]` *exactly once* (nonce bump), immediately usable in subsequent `transferFrom`.

4. **Proxy × Any Module**  
   Calls via proxy yield the same effects as direct calls when `impl_code_hash == pinned`. If hash mismatch, **no state change** occurs.

5. **Capabilities × Storage**  
   Contracts that **persist** capability outputs (e.g., AI result digests) must:
   - Write exactly the bytes returned by the capability.
   - Treat subsequent reads as **read-only**; no mutation allowed to persisted bytes except explicit overwrite flows documented by the contract.

---

## Liveness & Progress Properties (Non-Consensus Hints)

- **AI/Quantum Result Progress**: If a valid job is enqueued and an eligible provider is live, the result becomes available within policy-bound time windows (off-chain SLA). Contracts do **not** assume liveness; they only observe when availability flips.
- **Timelock Progress**: Any queued op executable at `now ≥ eta` will execute if called; the module does not autonomously progress time.

---

## Audit Checklist (Quick)

- **Access**: Owner/roles checks on every guarded method; no state writes before guards.
- **Math**: All arithmetic via `safe_uint`; no direct Python ints for critical balances.
- **Events**: Emitted after state change; fields match ABI; no sensitive data leaks.
- **Permit**: Domain separation includes chainId + contract + purpose tag + nonce; nonce strictly increments.
- **Token**: `Σ balances == totalSupply` holds across all paths incl. mint/burn.
- **Escrow**: State machine transition constraints; funds conservation; finalization rules.
- **Capabilities**: Input caps respected; next-block visibility assumed; persisted outputs immutable unless explicitly versioned.
- **Proxy**: Code-hash pin enforced; admin-only upgrade; no dynamic code loading.

---

## Proof Sketch References

- **Supply Conservation**: By induction over operations (mint/burn/transfer), base `Σ=0`; each op preserves the invariant as defined.
- **Permit Replay Safety**: The signed message includes `nonce`; acceptance increments `nonce`; any second attempt with the same signature fails the nonce check.
- **Timelock Safety**: No execute path without `now ≥ eta`; queuing defines unique `op_id`; cancel removes the key; `execute` consumes it exactly once.

---

## Testing Links

- Unit tests: `contracts/tests/*.py`
- Property tests: `tests/property/test_vm_storage_props.py`, `test_tx_codec_props.py`
- Integration: `tests/integration/test_vm_deploy_and_call.py`, `test_capabilities_ai_flow.py`, `test_capabilities_quantum_flow.py`
- Token/Permit: `contracts/tests/test_token_stdlib.py`
- Treasury: `contracts/tests/test_escrow_stdlib.py`
- Proxy pin: `contracts/tests/test_upgrade_proxy_pinned.py`

---

## Change Control

Any change that can affect an invariant must:
1) Update this document,
2) Add/adjust unit/property tests,
3) Bump affected manifests/ABI versions accordingly.

