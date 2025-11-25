# Example: Deterministic Escrow (with optional dispute & arbiter)

This example shows a small, production-style escrow contract implemented for the **Animica Python VM**. It uses the stdlib’s treasury/event helpers and follows the deterministic subset rules from [`contracts/CODESTYLE.md`].

It’s intended as a reference for:
- **Funds safety** patterns (deposit → hold → release/refund),
- **Deterministic control flow** (no time.syscalls, only chain-provided block context),
- **Clear events** for off-chain indexers & UIs,
- **Dispute & arbiter** logic that’s easy to audit.

> TL;DR  
> Buyer deposits → funds held → either *release* to Seller, or *refund* to Buyer if expired, or *dispute* and let Arbiter *resolve*. All paths emit canonical events.

---

## Files

contracts/examples/escrow/
├─ contract.py        # the contract source (Animica-Python subset)
├─ manifest.json      # ABI + metadata for tooling/RPC
└─ README.md          # this file

The compiled package (IR + manifest digest) will be produced under `contracts/build/` when you run the build step below.

---

## Contract interface (summary)

> The exact ABI is in `manifest.json`. Names and shapes below match the stdlib version in `contracts/stdlib/treasury/escrow.py`.

**Constructor / init**
- `init(buyer: address, seller: address, arbiter: address, amount: uint, deadline_height: uint)`
  - Sets the parties, the amount to hold, and the block-height deadline for a no-dispute refund path.
  - May only be called once (guarded by storage).

**Actions**
- `deposit()`  
  Buyer transfers `amount` into the contract’s escrow balance. Emits `Deposited(buyer, amount)`.
- `release()`  
  If deposited and not disputed, pays `amount` to `seller`. Emits `Released(seller, amount)`. Finalizes.
- `refund()`  
  If deposited and not disputed, and `block.height >= deadline_height`, refunds to `buyer`. Emits `Refunded(buyer, amount)`. Finalizes.
- `dispute(reason: bytes)`  
  Buyer or Seller can open a dispute before resolution. Emits `Disputed(opener, reason)`.
- `resolve(to_seller: bool)`  
  Arbiter decides outcome of an open dispute: either pay seller or refund buyer. Emits `Resolved(arbiter, to_seller, amount)`. Finalizes.
- `cancel_before_deposit()`  
  Either party can cancel if no deposit has occurred yet (optional convenience). Emits `Cancelled()`.

**Views (read-only)**
- `state() -> { buyer, seller, arbiter, amount, deposited: bool, disputed: bool, finalized: bool, deadline_height: uint }`
- `balance() -> uint`  (escrow balance held by contract treasury)
- `parties() -> { buyer, seller, arbiter }`

**Events (canonical names)**
- `Deposited(buyer, amount)`
- `Released(seller, amount)`
- `Refunded(buyer, amount)`
- `Disputed(opener, reason)`
- `Resolved(arbiter, to_seller, amount)`
- `Cancelled()`

All addresses are standard **Animica addresses** (bech32m `anim1…`) at the ABI layer; tooling also accepts 32-byte raw addresses where indicated and normalizes consistently.

---

## Lifecycle & invariants

init → (await deposit)
│
├── deposit() ──┬── release()  → finalize → invariant: escrow balance -> 0
│               ├── refund()   → finalize → invariant: escrow balance -> 0
│               └── dispute() ── resolve(to_seller|buyer) → finalize (balance -> 0)
│
└── cancel_before_deposit() (optional path, only if !deposited)

**Key invariants**
1. At most one terminal outcome (Released | Refunded | Resolved | Cancelled).
2. Funds conservation: `escrow_balance` is either `0` or `amount`, never drifts.
3. After finalize, no state changes are permitted; all mutating methods revert.
4. `refund()` only valid if `block.height ≥ deadline_height` and `!disputed`.

---

## Quickstart (local simulation)

You can simulate calls without a node using the VM tools or the browser-based simulator.

### Option A: VM CLI (local)
```bash
# compile + package (see "Build & Package" below first) then:
python -m vm_py.cli.run \
  --manifest contracts/examples/escrow/manifest.json \
  --call state

Option B: In browser via studio-wasm
	•	Dev run studio-wasm and open the preview page.
	•	Load contracts/examples/escrow/contract.py + manifest.json.
	•	Use the simulate panel to call init, then deposit, etc., observing events.

⸻

Build & package

Use the contracts build tool to produce a deterministic package (IR + metadata).

python -m contracts.tools.build_package \
  --src contracts/examples/escrow/contract.py \
  --manifest contracts/examples/escrow/manifest.json \
  --out contracts/build \
  --name escrow
# → creates contracts/build/escrow.pkg.json

The package embeds the code hash referenced by the manifest so RPC/services can verify deploys.

⸻

Deploy to a running devnet

You can deploy either with the Python SDK helper or via studio-web UI.

Option A: Python SDK

# Assumes RPC_URL and CHAIN_ID are set or passed as flags.
python -m contracts.tools.deploy \
  --package contracts/build/escrow.pkg.json \
  --rpc ${RPC_URL:-http://127.0.0.1:8545} \
  --chain-id ${CHAIN_ID:-1337} \
  --mnemonic "${DEPLOYER_MNEMONIC}"
# Prints deployed address (anim1…)

Initialize it (one-time):

python -m contracts.tools.call \
  --address anim1xyz... \
  --fn init \
  --args '{
    "buyer":   "anim1buyer…",
    "seller":  "anim1seller…",
    "arbiter": "anim1arbiter…",
    "amount":  500000,                 # 0.5 tokens if decimals=6
    "deadline_height":  (HEAD+100)     # choose a safe future height
  }'

Option B: studio-web
	•	Open Deploy page → load package → connect wallet → sign & send.
	•	After deploy, switch to Contracts or Tools to call init.

⸻

Common flows (CLI snippets)

Replace ANIM_ADDR with the deployed escrow address.

1) Deposit (Buyer)

python -m contracts.tools.call \
  --address ANIM_ADDR \
  --fn deposit \
  --sender "anim1buyer…" \
  --value 500000        # exact amount required by init()

2) Release (Seller)

python -m contracts.tools.call \
  --address ANIM_ADDR \
  --fn release \
  --sender "anim1seller…"

3) Refund after deadline (Buyer)

python -m contracts.tools.call \
  --address ANIM_ADDR \
  --fn refund \
  --sender "anim1buyer…"

4) Dispute (Buyer or Seller)

python -m contracts.tools.call \
  --address ANIM_ADDR \
  --fn dispute \
  --args '{"reason": "goods not as described"}' \
  --sender "anim1buyer…"

5) Resolve (Arbiter)

# Pay seller:
python -m contracts.tools.call \
  --address ANIM_ADDR \
  --fn resolve \
  --args '{"to_seller": true}' \
  --sender "anim1arbiter…"

# Or refund buyer:
python -m contracts.tools.call \
  --address ANIM_ADDR \
  --fn resolve \
  --args '{"to_seller": false}' \
  --sender "anim1arbiter…"

6) Read state

python -m contracts.tools.call --address ANIM_ADDR --fn state --read
python -m contracts.tools.call --address ANIM_ADDR --fn balance --read


⸻

Events

Indexers and UIs should listen for these canonical names:
	•	Deposited(buyer: address, amount: uint)
	•	Released(seller: address, amount: uint)
	•	Refunded(buyer: address, amount: uint)
	•	Disputed(opener: address, reason: bytes)
	•	Resolved(arbiter: address, to_seller: bool, amount: uint)
	•	Cancelled()

The receipt builder in execution/ guarantees deterministic ordering within a tx; topics and data are ABI-encoded per the VM’s event rules.

⸻

Verification (source ↔ on-chain code)

You can verify the published code hash against source using the studio-services helper:

python -m contracts.tools.verify \
  --address ANIM_ADDR \
  --src contracts/examples/escrow/contract.py \
  --manifest contracts/examples/escrow/manifest.json \
  --services ${SERVICES_URL:-http://127.0.0.1:8787}

This recompiles the source, computes the code hash, and stores a verification record tied to the contract address.

⸻

Gas & determinism notes
	•	All paths are gas-bounded; no unbounded loops or recursion.
	•	No network I/O or wall-clock; the only “time” input is block.height from the deterministic context.
	•	Treasury transfers are atomic with state updates; if a transfer would fail (insufficient treasury balance), the call reverts.
	•	The deadline is a block height (not seconds) to avoid clock skew.

⸻

Security checklist (quick)
	•	Single-use init; re-initialization forbidden.
	•	No reentrancy: transfers use treasury API with no user callbacks.
	•	Finalize flag locks all mutators.
	•	Dispute path requires explicit arbiter signature/authorization.
	•	Input sizes capped (e.g., reason length) to prevent event bloat.
	•	Emits clear events for every money-moving action.

See contracts/SECURITY.md for the full audit checklist.

⸻

Troubleshooting
	•	“refund() reverted (deadline not reached)”: Wait until block.height >= deadline_height. Use the explorer or state() view to see the current head.
	•	“deposit() amount mismatch”: The value sent must equal amount set during init.
	•	“resolve() unauthorized”: Only the configured arbiter can call resolve.
	•	“already finalized”: Once any of release/refund/resolve/cancel succeeds, the contract is sealed.

⸻

Extending

This minimal escrow is a good starting point for:
	•	Milestone-based payouts (split amount into tranches),
	•	Partial refunds (arbiter splits by ratio),
	•	On-chain evidence references (pin DA commitments and include in dispute),
	•	Time-locked escalation (deadline → auto refund unless extended by both parties).

Keep extensions within the deterministic subset and mind the gas budget.

⸻

License
This example follows the repo’s main license. See LICENSE at the root.

