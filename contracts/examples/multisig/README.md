# Multisig (N-of-M, PQ-aware permits)

A production-grade, deterministic Python contract that executes actions when **N of M** designated signers approve. It supports both **on-chain approvals** (batched over multiple transactions) and **off-chain permits** signed with post-quantum (PQ) keys (Dilithium3 / SPHINCS+). Off-chain permits are checked on-chain with strict domain separation, replay protection, and explicit expiry heights.

> This example targets the Animica Python VM (`vm_py`) determinism model and the canonical ABI/encoding used across the SDKs.

---

## Why this multisig?

- **Threshold security** — Require `threshold` approvals from a set of `owners` (size `M`).
- **PQ-aware permits** — Owners sign *permits* off-chain with supported PQ algorithms; the contract verifies them deterministically.
- **Replay-safe** — Domain includes `chainId`, `contractAddr`, `nonce`, and `actionHash`; each `nonce` can be consumed exactly once.
- **Deterministic** — No wall-clock time; uses block **height** for expiry, fixed encodings, pure storage.
- **Operational ergonomics** — On-chain approvals or single-tx `execute_with_permits`, plus owner/key rotation.

---

## Concepts & invariants

### Threshold model
- There is a set of **owners** (addresses derived from PQ pubkeys).
- A **threshold** `N` (1 ≤ N ≤ M) must approve any action to execute it.
- Owners can be rotated via a governed action (which itself requires approvals).

### Actions
An **action** is an encoded intent with:
- `to` (address)  
- `value` (u128 — native units; often zero)
- `data` (bytes — opaque ABI-encoded payload for target)
- `gas_limit` (u64 — ceiling enforced by the VM/execution adapter)
- **Action hash**: `H = sha3_256(abi_encode(Action))`

### Nonce & replay
- Each executed action consumes **exactly one** `nonce` in a monotonically increasing sequence.  
- Permits are bound to `(chainId, contractAddr, nonce, H)`.
- If a `nonce` was used, any further attempt at the same `nonce` fails, even if approvals are valid.

### Expiry
- Permits have `expiry_height` (block height). Contract checks `current_height ≤ expiry_height`.

---

## Interface (ABI summary)

**Read**
- `get_config() -> { owners: [address], threshold: u8 }`
- `get_nonce() -> u128` — next expected nonce
- `is_owner(addr: address) -> bool`
- `permit_domain() -> bytes32` — domain/separator id

**Write (on-chain approvals flow)**
- `propose(action: Action) -> u128` — reserves `nonce`, stores proposal; returns `nonce`
- `approve(nonce: u128)` — msg.sender must be an owner
- `revoke(nonce: u128)` — owner may revoke their approval prior to execution
- `execute(nonce: u128)` — if approvals ≥ threshold, executes the action

**Write (one-shot PQ-permit flow)**
- `execute_with_permits(nonce: u128, action: Action, expiry_height: u64, permits: [Permit]) -> bytes`  
  Verifies each permit, ensures threshold unique owners, then executes.

**Owner/threshold management (governed)**
- `set_threshold(new_threshold: u8)` — only via multisig governance
- `add_owner(addr: address)` — via multisig governance
- `remove_owner(addr: address)` — via multisig governance
- `replace_owner(old: address, new: address)` — via multisig governance

> Governance calls are just actions where `to = this`, `data = abi(set_threshold(...))`, etc.

---

## Permit format (PQ-aware)

### Message to sign (SignBytes)
Deterministic canonical bytes (CBOR/ABI as per repo standards):

struct PermitSignBytes {
domain        : bytes32  // “animica.multisig.permit.v1” domain separator
chain_id      : u64
contract_addr : address
nonce         : u128
action_hash   : bytes32  // sha3_256(abi_encode(Action))
expiry_height : u64
alg_id        : u16      // pq/alg_ids.yaml (e.g., Dilithium3, SPHINCS+)
}

### Permit object (submitted to contract)

struct Permit {
signer_addr : address          // derived from signer’s PQ pubkey as per pq/address rules
alg_id      : u16
sig         : bytes            // raw PQ signature bytes
}

**Verification rules (on-chain):**
1. Recompute `SignBytes` from inputs.
2. Verify `sig` under `alg_id` with the **registered** key material for `signer_addr`.
3. Collect unique `signer_addr` entries; require `count ≥ threshold`.
4. Ensure `expiry_height ≥ current_height`.
5. Ensure `nonce == next_nonce()`.
6. Ensure `action_hash` matches the provided `action`.

> PQ addresses: `address = bech32m( alg_id || sha3_256(pubkey_bytes) )`. The contract keeps a mapping `owner → (alg_id, pubkey_hash)` and rejects mismatches.

---

## Storage layout (high-level)
- `owners : Map[address → OwnerRecord{ alg_id: u16, pubkey_hash: bytes32, active: bool }]`
- `threshold : u8`
- `nonce : u128` (next)
- `proposals : Map[u128 → Proposal{ action_hash: bytes32, to: address, value: u128, data: bytes, gas_limit: u64, approvals: Map[address → bool] }>`
- `domain : bytes32` constant = `sha3_256(b"animica.multisig.permit.v1")`

---

## Events

- `Proposed(nonce: u128, action_hash: bytes32, proposer: address)`
- `Approved(nonce: u128, owner: address)`
- `Revoked(nonce: u128, owner: address)`
- `Executed(nonce: u128, action_hash: bytes32, success: bool, ret: bytes)`
- `OwnersChanged(owners: [address], threshold: u8)`
- `OwnerKeyUpdated(owner: address, alg_id: u16, pubkey_hash: bytes32)`

---

## Determinism & safety notes

- Uses **block height** only (no wall-clock).
- All hashing is **sha3-256**; no float math; fixed integer widths.
- Action execution uses deterministic gas metering; `gas_limit` caps resource usage.
- Key rotation must be executed **via multisig** and updates `(alg_id, pubkey_hash)` pair.
- Replay is prevented by `(chainId, contractAddr, nonce, action_hash)` domain.
- `execute_with_permits` rejects duplicate owners and mismatched `alg_id`.

---

## Quickstart (devnet)

### 1) Environment

export RPC_URL=http://127.0.0.1:8545
export CHAIN_ID=1337
export DEPLOYER_MNEMONIC=“abandon … art”

### 2) Deploy
You can deploy via the contracts tooling or directly via SDKs. Two typical paths:

**A) Tooling (build package → deploy):**

From repo root

python -m contracts.tools.build_package 
–manifest contracts/examples/multisig/manifest.json 
–source   contracts/examples/multisig/contract.py 
–out      contracts/build

python -m contracts.tools.deploy 
–package contracts/build/multisig.package.json

**B) SDK (Python) snippet (pseudo):**
```python
from omni_sdk.rpc.http import HttpClient
from omni_sdk.wallet.signer import Dilithium3Signer
from omni_sdk.contracts.deployer import deploy_package

rpc = HttpClient(os.environ["RPC_URL"])
signer = Dilithium3Signer.from_mnemonic(os.environ["DEPLOYER_MNEMONIC"])
with open("contracts/build/multisig.package.json","rb") as f:
    pkg = f.read()
res = deploy_package(rpc=rpc, chain_id=int(os.environ["CHAIN_ID"]), signer=signer, package=pkg)
print("address:", res["address"])

3) Initialize owners / threshold

This is typically executed by the deployer as the initial governor action.

	•	Call add_owner(addr) for each intended owner (or use a batch helper if provided).
	•	Call set_threshold(N); must satisfy 1 ≤ N ≤ number of active owners.

(If your contract constructor supports initial owners & threshold, they can be pre-encoded in the deploy package and executed atomically at deploy time.)

⸻

Usage patterns

Option A — On-chain approvals
	1.	Propose an action:

nonce = propose({to, value, data, gas_limit})

	2.	Each owner submits approve(nonce) from their own account.
	3.	Any account may trigger execute(nonce) once approvals ≥ threshold.

Option B — One-shot permits (off-chain PQ signatures)
	1.	Off-chain, a coordinator prepares SignBytes with:
	•	chain_id, contract_addr, nonce = next_nonce(), action_hash, expiry_height.
	2.	Collect Permit{signer_addr, alg_id, sig} from N distinct owners.
	3.	Submit a single transaction:

execute_with_permits(nonce, action, expiry_height, [permits...])

This is gas-efficient and reduces on-chain coordination while preserving security.

⸻

Permit building (reference pseudo)

from omni_sdk.utils.hash import sha3_256
from omni_sdk.utils.cbor import dumps
from pq.py.sign import sign_detached  # or wallet signer

domain = sha3_256(b"animica.multisig.permit.v1")
action_hash = sha3_256(abi_encode_action(to, value, data, gas_limit))

sign_bytes = {
  "domain": domain,
  "chain_id": CHAIN_ID,
  "contract_addr": multisig_addr,
  "nonce": next_nonce,               # read from contract
  "action_hash": action_hash,
  "expiry_height": head_height + 100,  # e.g. 100 blocks
  "alg_id": 0x0101,                  # example: Dilithium3
}
msg = dumps(sign_bytes)              # canonical encoding

sig = sign_detached(msg, secret_key) # PQ signature
permit = {"signer_addr": owner_addr, "alg_id": 0x0101, "sig": sig}


⸻

Gas & limits
	•	execute_with_permits costs scale with:
	•	Number of permits k (verification loops)
	•	Size of data (target call payload)
	•	Enforce caps:
	•	k ≤ M and M bounded by a safe maximum (e.g., 64)
	•	data length ≤ policy cap (see vm_py/execution gas table)
	•	gas_limit is per-action; failed target calls still consume gas and bubble a Revert status in the Executed event.

⸻

Security checklist
	•	Domain separation constant is versioned & hashed once (bytes32).
	•	chainId and contractAddr included in SignBytes.
	•	nonce strictly monotonic; consumed on first execution attempt (success or revert is recorded).
	•	Unique owner set enforced; duplicates don’t increment approvals.
	•	Key material pinning: (alg_id, pubkey_hash) bound per owner.
	•	Rotation only via multisig action; prevents unilateral key changes.
	•	No wall-clock time; expiry by height.
	•	Deterministic hashing/encoding; no non-deterministic I/O.

⸻

Troubleshooting
	•	ThresholdTooHigh — Lower threshold or add more owners first.
	•	PermitExpired — Increase expiry_height (in blocks) and re-collect signatures.
	•	NonceMismatch — Fetch get_nonce(); rebuild SignBytes for that nonce.
	•	OwnerMismatch / AlgIdMismatch — Make sure the signer’s (alg_id, pubkey_hash) matches the registered owner record.
	•	DuplicateSigner — Remove duplicates from permits.

⸻

Next steps
	•	Add a batched update_owners([adds],[removes],new_threshold) action helper.
	•	Add an optional permit list memo to aid UX without affecting hashing.
	•	Integrate with the wallet extension’s PQ sign flow for smoother collection.

⸻

Files in this example (to be added)
	•	contract.py — the multisig contract implementation
	•	manifest.json — ABI + metadata
	•	tests_local.py — unit tests against vm_py
	•	deploy_and_test.py — deploy to a devnet and run a smoke flow

See other examples in contracts/examples/* for structure and helper tooling.
