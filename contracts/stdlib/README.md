# contracts/stdlib

A curated set of **production-grade, deterministic** Python contracts that cover the most common building blocks on Animica. Each contract is written for the **vm_py** runtime and follows the repo’s determinism, coding, and security conventions.

This stdlib aims to be:
- **Auditable:** tiny, explicit, and documented invariants.
- **Deterministic:** adheres to `contracts/CODESTYLE.md` and VM rules.
- **Stable:** ABIs versioned with strong compat guarantees.
- **Composable:** designed to be linked and called from other contracts and from off-chain clients (SDKs).

> You can deploy any stdlib package as-is, or copy the sources as a starting point for your own modules.

---

## Contents & Capabilities

| Contract | Purpose | Key Capabilities | Events | Notes |
|---|---|---|---|---|
| **Counter** | Minimal example (state, events) | — | `Inc(n)`, `Set(v)` | Mirrors test fixtures; great smoke test. |
| **Escrow** | Timed escrow between two parties | Time locks, treasury transfer | `Deposited`, `Released`, `Refunded` | Deterministic unlock windows; no external clocks. |
| **Token (AN20)** | Fungible token (Animica AN20 spec) | Mint (optional), burn, transfer, allowance | `Transfer`, `Approval`, `Mint`, `Burn` | Safe arithmetic; fees optional via policy flags. |
| **NFT (AN721)** | Minimal non-fungible (unique ids) | Mint, transfer, approve | `Transfer`, `Approval`, `ApprovalForAll` | Deterministic enumeration bounded by gas. |
| **PaymentSplitter** | Revenue split across recipients | Treasury hooks | `PaymentReleased` | Immutable split set at init or via timelock. |
| **MultiSig** | M-of-N account controller | Deterministic proposal/confirm/execute | `ProposalCreated`, `Executed` | No external calls besides treasury—safe for core ops. |
| **Timelock** | Delay sensitive ops | Queue/execute after ETA | `Queued`, `Executed`, `Cancelled` | Time derived from block headers only. |
| **Registry** | Name → address mapping | Owner/role-gated updates | `Registered`, `Unregistered` | Used as upgrade routing (see below). |
| **DA Vault** | Store DA commitments on-chain | **capabilities.da** bridge | `BlobPinned(commitment, ns)` | On-chain index of NMT roots (not the blob bytes). |
| **AI Job Client** | Request/consume AI jobs | **capabilities.compute.ai** | `JobEnqueued(id)`, `JobConsumed(id)` | “Next-block consumption” pattern; see capabilities docs. |
| **Quantum RNG** | Beacon/QRNG consumer | **randomness** adapter | `RngRequested`, `RngServed` | Binds to beacon rounds; deterministic transcript. |

> Exact file paths for these modules live under `contracts/stdlib/<name>/` alongside their `manifest.json` and `abi.json`. If you don’t see a module yet, it’s planned and listed here to set expectations and integration surfaces.

---

## Versioning & Compatibility

- Each contract directory includes a `README.md`, `contract.py`, `manifest.json`, and `abi.json`.
- **SemVer for ABI**:
  - **MAJOR**: breaking ABI/storage changes.
  - **MINOR**: backward-compatible ABI additions (new functions/events/fields).
  - **PATCH**: internal refactors or gas fixes; no ABI change.
- **Code Hash**: reproducible build ensures the `code_hash` in the manifest matches on-chain.

**Upgrade Pattern (Registry-routed):**
1. Deploy a new version (e.g., `an20@2.0.0`).
2. Register it in **Registry** under a stable key (`"an20.latest"`).
3. Off-chain clients resolve addresses via the registry key to avoid direct pointer churn.

This avoids proxy complexity while retaining determinism and clear audit paths.

---

## Determinism Guarantees

All stdlib contracts:
- Use only the **allowed** VM subset (no I/O, no time, no randomness beyond provided adapters).
- Rely on **block context** (height/timestamp) via the VM’s context API when needed.
- Perform **bounds checks** on list/bytes/int to avoid pathological gas growth.
- Emit **canonical events** with stable field names/types.
- Encode/decode via the canonical ABI described by `contracts/schemas/abi.schema.json`.

Before contributing changes, run:
```bash
make lint
pytest -k "stdlib or counter or escrow"


⸻

Security Invariants (by module)

Token (AN20)
	•	Conservation: total supply changes only via mint/burn.
	•	No negative balances: transfers revert if balance insufficient.
	•	Allowance safety: overwrite or “increase/decrease” pattern—no race.
	•	Events: every state mutation emits the expected event exactly once.

NFT (AN721)
	•	Uniqueness: a token id maps to at most one owner at a time.
	•	Approval correctness: either per-token or operator (for all).
	•	Transfer validity: sender is owner or approved; zero-address is rejected.

MultiSig
	•	Deterministic id: proposal id = H(target|value|data|nonce).
	•	M-of-N enforced before execution; single-use proposals.
	•	No reentrancy: because only treasury transfer/syscalls are allowed, reentrancy is structurally absent.

Escrow/Timelock
	•	Time source: derived from block header values only; no wall-clock.
	•	Monotonic: queued op can execute only once after ETA; cancel restores funds if specified.

DA Vault
	•	Immutability: commitment entries are append-only (optional pruning flags disabled by default).
	•	Namespace checks: validates namespace ranges and lengths before accepting commitments.

AI Job Client / Quantum RNG
	•	Transcript binding: ties a request to beacon height/round and caller to prevent replay.
	•	Next-block consumption: result becomes readable only after the subsequent block, enforced by the runtime adapter.

⸻

Build, Deploy, Call

All stdlib packages can be built and deployed via the contract tools.

Build:

python -m contracts.tools.build_package \
  --source contracts/stdlib/an20/contract.py \
  --manifest contracts/stdlib/an20/manifest.json \
  --out-dir contracts/build/an20

Deploy:

export RPC_URL=http://127.0.0.1:8545
export CHAIN_ID=1337
export DEPLOYER_MNEMONIC="abandon abandon ..."

python -m contracts.tools.deploy \
  --package contracts/build/an20/AN20.pkg.json

Call (transfer 100 units):

python -m contracts.tools.call \
  --address anim1xxxx... \
  --abi contracts/build/an20/AN20.abi.json \
  --method transfer \
  --args '{"to":"anim1yyyy...","amount":100}'

Verify:

export SERVICES_URL=http://127.0.0.1:8787
python -m contracts.tools.verify \
  --address anim1xxxx... \
  --source contracts/stdlib/an20/contract.py \
  --manifest contracts/build/an20/AN20.manifest.json


⸻

Events & ABI Conventions
	•	Names are PascalCase for events (Transfer, Approval) and lowerCamelCase for function names (transfer, approve).
	•	Field keys are lowerCamelCase; binary data is hex-encoded in RPC responses (0x-prefixed).
	•	Return values use named objects for multi-return (e.g., {"success": true}), not tuples.

Example (AN20) ABI excerpt:

{
  "functions": [
    {"name": "name", "inputs": [], "outputs": [{"type": "string"}]},
    {"name": "balanceOf", "inputs": [{"name":"owner","type":"address"}], "outputs":[{"type":"u256"}]},
    {"name": "transfer", "inputs":[{"name":"to","type":"address"},{"name":"amount","type":"u256"}], "outputs":[{"type":"bool"}]}
  ],
  "events": [
    {"name":"Transfer","inputs":[{"name":"from","type":"address"},{"name":"to","type":"address"},{"name":"amount","type":"u256"}]}
  ]
}


⸻

Gas & Performance Notes
	•	All loops are bounded by input sizes and strict caps; heavy operations (enumeration, large lists) should be avoided in write paths.
	•	Use contracts/tools/bench profiles and tests/bench/* to observe gas/runtime on common flows.
	•	For DA/AI/Quantum integrations, gas charges account for syscall dispatch and receipt handling—see capabilities/specs/GAS.md.

⸻

Testing Guidance
	•	Unit tests: prefer deterministic, table-driven tests; simulate with studio-wasm or execution/runtime.
	•	Property tests: balances never go negative; total supply remains consistent; invariants around timelocks/multisig thresholds.
	•	Integration tests: deploy→call→events; for AI/Quantum, enqueue and validate “next-block consumption.”

Example (pytest snippet):

def test_an20_transfer_roundtrip(omni_client, an20_deployed):
    sender = an20_deployed["deployer"]
    rcpt = an20_deployed["address"]
    abi = an20_deployed["abi"]
    # pre
    b0 = omni_client.contract(rcpt, abi).balanceOf(sender)
    # call
    tx = omni_client.contract(rcpt, abi).transfer(to=sender, amount=1)  # self-transfer
    assert tx.receipt["status"] == "SUCCESS"
    # post
    b1 = omni_client.contract(rcpt, abi).balanceOf(sender)
    assert b1 == b0  # no net change for self-transfer


⸻

Security Checklist (short)
	•	Deterministic: no banned imports, no nondeterministic sources.
	•	Bounds: explicit length/value checks on user input.
	•	State updates: all mutations covered by events.
	•	Access control: owner/roles enforced consistently.
	•	Treasury: only expected transfers; no silent balance movements.
	•	Reentrancy: structurally impossible in stdlib (no arbitrary external calls).
	•	Upgrades: if used, routed through Registry with clear provenance.

See contracts/SECURITY.md for a full checklist.

⸻

Where to go next
	•	Specs: spec/abi.schema.json, spec/openrpc.json, vm_py/specs/*
	•	SDKs: sdk/python, sdk/typescript, sdk/rust
	•	Services: studio-services for verify and deploy relay
	•	Explorer: explorer-web for live inspection & events

If you extend stdlib, add a README.md next to your module describing:
	1.	invariants, 2) ABI/Events, 3) upgrade/migration plan, and 4) test vectors.

