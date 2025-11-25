# On-Chain Execution — Contracts & Transactions

**Status:** Adopted  
**Related:** `governance/{GOVERNANCE.md, PROCESS.md, PROPOSAL_TYPES.md, THRESHOLDS.md, PARAMS_GOVERNANCE.md, PQ_POLICY.md}`, `docs/spec/*`, `sdk/*`, `studio-services/*`

This document specifies **how approved governance decisions are enacted on-chain** via a small set of **governance system contracts** and well-defined **transactions** (“execution bundles”). It maps each **proposal type** to the *exact* contract method(s) to call, the arguments to encode, ordering, safety checks, and event receipts that must be observed.

> TL;DR — All changes are executed by a **Timelock → Registry** pattern. Proposals encode a deterministic *execution bundle* (targets + calldata) that is queued in a Timelock and executed after the delay. The bundle’s hash is pinned in the proposal to prevent drift.

---

## 1) Contracts (system set)

System contracts are Python-VM contracts deployed at **fixed addresses** in genesis and exposed via the RPC/SDKs. Only the **Timelock** has permission to mutate registries.

| Contract | Address (example) | Purpose |
|---|---|---|
| `GovernanceCore` | `anim1gov...core` | Proposals, votes, queue/execute orchestration (calls Timelock). |
| `TimelockController` | `anim1gov...time` | Enforces minimum delay for any state change; sole *executor* over registries. |
| `ParamsRegistry` | `anim1sys...parm` | Canonical chain/runtime parameters (gas, limits, Θ/Γ schedules, fee flags). |
| `PolicyRegistry` | `anim1sys...plcy` | Merkle-rooted policies: e.g., `algPolicyRoot`, `poiesPolicyRoot`, `daPolicyRoot`. |
| `UpgradeGate` | `anim1sys...upgd` | Feature flags & activation heights (soft/hard forks), version fields. |
| `TreasuryController` | `anim1sys...tres` | Authorized treasury operations: splits, drips, programmatic payouts (AICF hooks). |

> Concrete addresses are set in `core/genesis/genesis.json` and mirrored in `docs/spec/GENESIS.md`.

### 1.1 Minimal ABIs (human view)

All contracts use the **Python-VM ABI** (see `vm_py/specs/ABI.md`). For reference:

- **TimelockController**
  - `queue(targets: [address], selectors: [bytes4], args: [bytes], eta: u64) -> bytes32 executionId`
  - `execute(executionId: bytes32) -> bool`
  - `cancel(executionId: bytes32)`
  - Events: `Queued(executionId, eta)`, `Executed(executionId)`, `Canceled(executionId)`

- **ParamsRegistry**
  - `setBytes(key: bytes, value: bytes)`
  - `setU64(key: bytes, value: u64)`
  - `setI64(key: bytes, value: i64)`
  - `setAddr(key: bytes, value: address)`
  - Events: `ParamSet(key, valueHash)`

- **PolicyRegistry**
  - `setRoot(name: bytes, root: bytes32)`  → e.g., `("alg", <sha3_512_merkle_root>)`
  - Events: `PolicyRootSet(name, root)`

- **UpgradeGate**
  - `schedule(version: bytes, feature: bytes, height: u64)`  (height or effective timestamp)
  - `activate(version: bytes, feature: bytes)` (no-op if not due)
  - Events: `UpgradeScheduled(version, feature, height)`, `UpgradeActivated(version, feature)`

- **TreasuryController**
  - `transfer(to: address, amount: u128, memo: bytes)`
  - `setSplit(name: bytes, bps: u16)`
  - Events: `Transfer(to, amount)`, `SplitSet(name, bps)`

`GovernanceCore` binds all of the above with proposal lifecycle but does **not** write registries directly; it always calls through the **Timelock**.

---

## 2) Execution Bundles

A **bundle** is the ordered tuple:

Bundle = { targets: [address], selectors: [bytes4], args: [bytes], nonce: u64 }

- **Deterministic hash**:  
  `executionHash = keccak256( encode(Bundle) )`  
  This hash is recorded in the proposal and in `GovernanceCore`. Any deviation (target, selector, args, order) invalidates execution.
- **Timelock ETA**: set by Governance when queueing; must satisfy `eta >= now + minDelay`.

**Selectors** are the first 4 bytes of `keccak256("fnName(type1,type2,...)")` over the canonical Python-VM ABI signature.

---

## 3) Proposal Type → Calls

Below are canonical mappings for each proposal type (see `governance/PROPOSAL_TYPES.md`):

### 3.1 ParamChange
- **Intent:** Change a single chain/runtime parameter (e.g., `min_gas_price`, `block_gas_limit`).
- **Calls (1):**
  - `ParamsRegistry.setU64(key="min_gas_price", value=NEW_U64)` or corresponding setter.
- **Guards:** Value within `PARAMS_GOVERNANCE.md` safe range, enforced on-chain by `ParamsRegistry`.
- **Events Expected:** `ParamSet("min_gas_price", H(value))`

### 3.2 Upgrade
- **Intent:** Schedule activation of a feature flag or protocol version at a height.
- **Calls (1..2):**
  - `UpgradeGate.schedule(version="vX.Y.Z", feature="consensus_XYZ", height=H_ACT)`
  - (Optional later) `UpgradeGate.activate(...)` (usually triggered automatically by node execution layer at ≥ height).
- **Guards:** `height >= head + MIN_ACTIVATION_DISTANCE` (chain param).

### 3.3 PQRotation
- **Intent:** Update PQ **algorithm policy** Merkle root.
- **Calls (1):**
  - `PolicyRegistry.setRoot(name="alg", root=<bytes32 new_root>)`
- **Pre-req:** Root produced by `pq/alg_policy/build_root.py` and attached to proposal.
- **Events Expected:** `PolicyRootSet("alg", root)`

### 3.4 Policy (Consensus / DA / ZK)
- **Intent:** Update consensus policy root (`poies`), DA policy root (`da`), or ZK policy root (`zk`).
- **Calls (1):** `PolicyRegistry.setRoot(name="<poies|da|zk>", root=<bytes32>)`

### 3.5 Treasury
- **Intent:** Move funds or change splits.
- **Calls (1):**
  - Transfer: `TreasuryController.transfer(to, amount, memo)`
  - Split change: `TreasuryController.setSplit(name, bps)`
- **Guards:** Caps and multi-sig thresholds enforced by `TreasuryController`.

### 3.6 Multi-Call Bundles
Some decisions require **ordered** updates (e.g., upgrade schedule *and* parameter nudges). Bundle the calls in one `queue` so they execute atomically under the Timelock.

---

## 4) Lifecycle & Transactions

1. **Propose**
   - Client builds `Bundle`, computes `executionHash`, prepares human description + metadata.
   - Submit via `GovernanceCore.propose(kind, executionHash, ...meta)` (off-chain metadata stored in IPFS/DA optional).
   - Event: `Proposed(proposalId, executionHash)`

2. **Vote**
   - Participants call `cast_vote(proposalId, support, weight?)`.
   - Events: `VoteCast(proposalId, voter, support, weight)`

3. **Queue**
   - If passed, `GovernanceCore.queue(proposalId, eta)` generates **targets/selectors/args** from the stored bundle and calls `TimelockController.queue(...)`.
   - Events: `Queued(executionId, eta)`

4. **Execute**
   - After ETA, anyone can call `GovernanceCore.execute(proposalId)`, which calls `TimelockController.execute(executionId)`.
   - Events: e.g., `PolicyRootSet`, `ParamSet`, `UpgradeScheduled`, and final `Executed(executionId)`

5. **Cancel (optional)**
   - If pre-conditions fail or emergency, `GovernanceCore.cancel(proposalId)` → `Timelock.cancel(executionId)`.

**Replay & Uniqueness:** The Timelock nonce and internal hash prevent replays; execution is idempotent at the Timelock layer and non-reentrant per target call.

---

## 5) Encoding & Tooling

### 5.1 Building calldata
Use the SDK ABI encoders to build `args` per function signature:

**TypeScript (SDK)**
```ts
import { abiEncode, addressOf } from "@animica/sdk";
import { keccak256 } from "@animica/sdk/utils/hash";

const target = addressOf("PolicyRegistry");
const selector = keccak256("setRoot(bytes,bytes32)").slice(0, 4); // bytes4
const args = abiEncode(["bytes","bytes32"], [Buffer.from("alg"), newRoot]);

const bundle = { targets:[target], selectors:[selector], args:[args], nonce: 1n };
const executionHash = keccak256(abiEncode(
  ["address[]","bytes4[]","bytes[]","u64"],
  [bundle.targets, bundle.selectors, bundle.args, Number(bundle.nonce)]
));

Python (SDK)

from omni_sdk.tx.encode import abi_encode
from omni_sdk.utils.hash import keccak256
from omni_sdk.address import address_of

target = address_of("PolicyRegistry")
selector = keccak256(b"setRoot(bytes,bytes32)")[:4]
args = abi_encode(["bytes","bytes32"], [b"alg", new_root])

bundle = {"targets":[target], "selectors":[selector], "args":[args], "nonce":1}
execution_hash = keccak256(abi_encode(
    ["address[]","bytes4[]","bytes[]","u64"],
    [bundle["targets"], bundle["selectors"], bundle["args"], 1]
))

5.2 Propose → Queue → Execute (end-to-end)

TypeScript

import { HttpClient } from "@animica/sdk/rpc/http";
import { Wallet } from "@animica/sdk/wallet";

const rpc = new HttpClient({ url: process.env.RPC_URL! });
const wallet = await Wallet.fromKeystore("governance.keystore", "pass");
// 1) propose
await rpc.call("gov.propose", { kind: "PQRotation", executionHash, metaUri });
// 2) after voting period, queue
await rpc.call("gov.queue", { proposalId, eta: suggestedEta });
// 3) after ETA
await rpc.call("gov.execute", { proposalId });

Python

from omni_sdk.rpc.http import HttpClient
from omni_sdk.wallet.keystore import Keystore

rpc = HttpClient(url=RPC_URL)
ks = Keystore.open("governance.keystore", "pass")
# 1) propose
rpc.call("gov.propose", {"kind":"PQRotation", "executionHash":execution_hash.hex(), "metaUri":meta_uri})
# 2) queue
rpc.call("gov.queue", {"proposalId": proposal_id, "eta": eta})
# 3) execute
rpc.call("gov.execute", {"proposalId": proposal_id})

For safety, use studio-services /simulate to dry-run any bundle against a snapshot before proposing/queueing.

⸻

6) Safety Invariants
	•	Timelock is the sole mutator of registries; onlyTimelock checks on each setter method.
	•	Minimum delay cannot be reduced below the chain constant without a two-step (raise guard).
	•	Execution hash pinning ensures the encoded calls cannot drift post-vote.
	•	Bounds checking in ParamsRegistry prevents out-of-range values (as defined in PARAMS_GOVERNANCE.md).
	•	Version gates in UpgradeGate ensure activation cannot occur earlier than scheduled height.
	•	Pausability (emergency): Security Council can pause GovernanceCore queueing (not execution of already queued bundles) under strict quorum/time-limits (see SECURITY_COUNCIL.md).

⸻

7) Events & Receipts (audit trail)

A successful execution emits:
	1.	Queued(executionId, eta) (Timelock)
	2.	One or more registry events (e.g., PolicyRootSet("alg", root))
	3.	Executed(executionId) (Timelock)
	4.	ProposalExecuted(proposalId) (GovernanceCore)

Wallets/Explorers should stitch these into a human timeline.

⸻

8) Canonical Keys & Names
	•	ParamsRegistry keys (bytes):
	•	b"min_gas_price", b"block_gas_limit", b"target_block_time_ms", b"theta_floor", …
	•	PolicyRegistry names:
	•	b"alg" (PQ alg-policy root), b"poies" (consensus/PoIES), b"da" (data-availability), b"zk" (verifier policy)
	•	UpgradeGate features:
	•	e.g., b"consensus_v2", b"vm_py_1_2", b"zk_verifier_0"

⸻

9) Testing & Reproducibility
	•	Vectors: Every change should include a JSON execution bundle fixture with:
	•	targets, selectors, args (hex), nonce, expected executionHash
	•	Replay test: Submitting the same bundle twice must fail at GovernanceCore (consumed proposal or timelock dedupe).
	•	Simulation: /simulate must return 0 reverts with the same state root delta expected.
	•	Hash commitments: The proposal description should include the executionHash, registry roots (old→new), and any referenced artifact hashes.

⸻

10) Bootstrap & Upgrades of Governance Itself
	•	Before decentralization, a bootstrap multisig controls GovernanceCore roles but still routes through Timelock.
	•	Upgrading GovernanceCore/Timelock requires:
	•	Deploy new contract(s) → propose a two-bundle:
	1.	UpgradeGate.schedule("gov_X", "gov_core_impl", H_ACT)
	2.	ParamsRegistry.setAddr("gov_core_addr", NEW_ADDR) (or registry indirection)
	•	Never allow a bundle that removes the Timelock invariant in a single step.

⸻

11) Operational Checklist (pre-execution)
	•	Bundle encodes the intended calls in correct order.
	•	Execution hash matches off-chain spec and the proposal page.
	•	Bounds checks pass on dry-run.
	•	Timelock ETA ≥ minDelay; no overlap with other critical activations.
	•	Communications prepared (upgrade notes, wallet prompts) if user-visible.

⸻

12) Examples

12.1 PQRotation (update algPolicyRoot)
	•	Build new root with pq/alg_policy/build_root.py.
	•	Bundle: PolicyRegistry.setRoot("alg", new_root).
	•	Vote → Queue → Execute.
	•	Verify via RPC: chain.getParams (header field or registry mirror) reflects the new root and zk/registry/* updated if relevant.

12.2 ParamChange (raise min_gas_price)
	•	Bundle: ParamsRegistry.setU64("min_gas_price", 5_000)
	•	Enforced range: [1_000, 10_000] (example).

⸻

13) References
	•	docs/spec/UPGRADES.md, docs/spec/CAPABILITIES.md
	•	governance/PROCESS.md, governance/THRESHOLDS.md
	•	SDK examples: sdk/*/examples and tests
	•	Studio Services: /simulate, /deploy

