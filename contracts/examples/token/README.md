# Animica Example — Fungible Token (A20)

A production-quality reference implementation of a fungible token built on the
Animica Python-VM. The example showcases the **A20** interface (ERC-20–like),
optional **Mintable** and **Permit** extensions, event emission, and deterministic
resource accounting. It is intended as a starting point for projects that want a
clean, auditable token with PQ-safe signing and fully deterministic behavior.

> This example pairs with the stdlib modules in `contracts/stdlib/token/`:
> - `fungible.py` (core A20)
> - `mintable.py` (owner/roles-gated mint/burn)
> - `permit.py` (off-chain approvals with PQ signature domain)

---

## What’s in this folder

- `contract.py` — your token implementation (imports from `contracts/stdlib/token/*`).
- `manifest.json` — ABI + metadata used by build/deploy/verify.
- (Built artifacts land in `contracts/build/` after running the commands below.)

If you don’t see `contract.py`/`manifest.json` yet, create them using the patterns
shown in `contracts/stdlib/token/` and `contracts/interfaces/itoken20.json`.  

---

## Prerequisites

- Python 3.11+
- `make`, `virtualenv` (optional)
- Local devnet or accessible node
  - Set **RPC_URL** and **CHAIN_ID** in your environment (see `.env.example`)
- This repo’s tools and libraries:
  - `contracts/requirements.txt` pins: VM, SDK, tooling
  - `contracts/tools/*.py` — build/deploy/call/verify helpers
- (Optional) a funded mnemonic for the deployer on your target network

Quick bootstrap:

```bash
cd contracts
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env to set RPC_URL, CHAIN_ID, and DEPLOYER_MNEMONIC (or use the devnet faucet)


⸻

Quickstart (Devnet/Testnet)

1) Lint & type-check (recommended)

cd contracts
make lint

2) Build (compile → IR → package)

This resolves imports from the stdlib and produces a deterministic package with
a code hash pinned in the manifest.

cd contracts
make build EX=token
# Artifacts:
# - build/token.ir.json         (intermediate, optional)
# - build/token.pkg.json        (deployable package: code + ABI + manifest)
# - build/token.codehash.txt    (sha3-256 of the bytecode)

3) Deploy

Two paths are supported:

A. Using the Makefile target (testnet):

cd contracts
make testnet-deploy EX=token
# Prints tx hash and deployed address (bech32m anim1…)

B. Using the deploy tool directly (works on any network):

cd contracts
python -m contracts.tools.deploy \
  --package build/token.pkg.json \
  --rpc "$RPC_URL" \
  --chain-id "$CHAIN_ID" \
  --mnemonic "$DEPLOYER_MNEMONIC"
# => writes build/token.address.txt and prints the receipt

Tip: On devnet you can fund the deployer with the faucet via studio-services
or use the pre-funded test accounts in tests/devnet/seed_wallets.json.

⸻

Interact (transfer / approve / permit / mint / burn)

Grab your deployed address:

ADDR=$(cat contracts/build/token.address.txt)

Balance Of

python -m contracts.tools.call \
  --to "$ADDR" \
  --abi contracts/interfaces/itoken20.json \
  --fn balanceOf \
  --args '["anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq0u5l9"]'

Transfer

python -m contracts.tools.call \
  --to "$ADDR" \
  --abi contracts/interfaces/itoken20.json \
  --fn transfer \
  --args '["anim1recipientaddressxxxxxxxxxxxxxxxxxxxxxx", "1000000000000000000"]' \
  --sign --mnemonic "$DEPLOYER_MNEMONIC"

Approve (classical)

python -m contracts.tools.call \
  --to "$ADDR" \
  --abi contracts/interfaces/itoken20.json \
  --fn approve \
  --args '["anim1spendereeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "500000000000000000"]' \
  --sign --mnemonic "$DEPLOYER_MNEMONIC"

Permit (off-chain approval, PQ domain)
	1.	Produce a permit signature off-chain, then
	2.	Submit it on-chain with a single transaction.

# 1) Create and sign the permit payload (tools handles domain bytes for PQ algs)
python -m contracts.tools.call \
  --abi contracts/interfaces/itoken20.json \
  --fn buildPermit \
  --args '["anim1owner...", "anim1spender...", "500000000000000000", 3600]' \
  --sign --mnemonic "$DEPLOYER_MNEMONIC" \
  --dry-run > /tmp/permit.json

# 2) Submit signature to contract (no owner key access required on-chain)
python -m contracts.tools.call \
  --to "$ADDR" \
  --abi contracts/interfaces/itoken20.json \
  --fn permitSubmit \
  --args @/tmp/permit.json \
  --sign --mnemonic "$DEPLOYER_MNEMONIC"

The exact function names may differ depending on your contract.py
(for the stdlib template: permit_submit(caller, spender, value, deadline, sig)).
Adjust --fn and --args accordingly.

Mint / Burn (if Mintable is enabled)

# Mint 1 token (18 decimals) to recipient — requires OWNER/ROLE auth in your contract
python -m contracts.tools.call \
  --to "$ADDR" \
  --abi contracts/interfaces/itoken20.json \
  --fn mint \
  --args '["anim1recipient...", "1000000000000000000"]' \
  --sign --mnemonic "$DEPLOYER_MNEMONIC"

# Burn from your own balance
python -m contracts.tools.call \
  --to "$ADDR" \
  --abi contracts/interfaces/itoken20.json \
  --fn burn \
  --args '["500000000000000000"]' \
  --sign --mnemonic "$DEPLOYER_MNEMONIC"


⸻

Verify Source (Reproducible Build)

Use studio-services to recompile your source and match the on-chain code hash.

python -m contracts.tools.verify \
  --source contracts/examples/token/contract.py \
  --manifest contracts/examples/token/manifest.json \
  --address "$ADDR" \
  --services-url "$SERVICES_URL"

	•	If successful, the service records a verified artifact keyed by the contract address.
	•	You can host the artifact or pin it to DA (Data Availability) and store the commitment.

⸻

ABI & Manifest
	•	ABI must follow contracts/schemas/abi.schema.json.
	•	Manifests follow contracts/schemas/manifest.schema.json and should include:
	•	name, version, abi, codeHash, metadata (decimals, symbol, name)
	•	Validate locally:

python -m contracts.tools.abi_gen --validate contracts/examples/token/manifest.json


⸻

Integration with Wallet & SDKs
	•	Wallet Extension: Dapps call window.animica.request({ method: "tx_send", ... }).
	•	SDKs:
	•	Python: sdk/python/omni_sdk/contracts/client.py
	•	TypeScript: @animica/sdk contracts/client
	•	Rust: animica-sdk::contracts::client

Each SDK can load your ABI and auto-generate typed clients (see sdk/CODEGEN.md).

⸻

Determinism & Gas Notes
	•	Strict subset of Python — see contracts/CODESTYLE.md.
	•	No non-deterministic APIs (time, RNG, I/O, network).
	•	Gas charges follow vm_py/gas_table.json via the VM IR op costs and stdlib calls.
	•	Events are deterministic and included in the receipt bloom as per execution/.

⸻

Troubleshooting
	•	OOG / Revert: Re-run with --trace (if supported by your node) or simulate using studio-wasm.
	•	Chain ID mismatch: Ensure CHAIN_ID in .env matches the node’s value.
	•	Address format: All addresses are bech32m (anim1…). Use the tools’ built-in
validation; pass checksummed addresses to avoid mistakes.
	•	Permit failures: Confirm the signing domain matches the chain and contract
address; verify the PQ algorithm and signature bytes.

⸻

Cleanups

rm -f contracts/build/token.*


⸻

Next Steps
	•	Add role-based mint controls (contracts/stdlib/access/roles.py).
	•	Emit richer events (mint, burn, permit) for indexers.
	•	Add a timelock (contracts/stdlib/control/timelock.py) for privileged ops.
	•	Provide a simple UI panel in studio-web for mint/burn (devnet only).

