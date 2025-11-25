# Name Registry — Example

A minimal, deterministic **bytes32 ⇄ address** registry built on the Python VM.
This example demonstrates how to:

- Compile & deploy a simple registry contract.
- Register (`set`) and remove (`remove`) mappings.
- Resolve names to addresses (`get`) and check existence (`has`).
- Listen for canonical **events** to index updates.
- Drive the contract end-to-end from the SDKs and CLI tools.

> The on-chain logic is designed to be boring and safe: constant-time where reasonable,
> deterministic hashing, strict input caps, and crisp revert reasons for misuse.

---

## What ships in this example

contracts/examples/registry/
├─ contract.py          # the registry contract (deterministic Python subset)
├─ manifest.json        # ABI + metadata (name/functions/events/errors)
├─ tests_local.py       # unit tests executed against vm_py locally
└─ deploy_and_test.py   # deploy to a devnet/testnet and run a smoke test

> If some files aren’t present yet, they’ll arrive in subsequent steps of the walkthrough.
> The README already documents the intended behavior and interfaces.

---

## Interface (ABI overview)

### Functions

- `set(name: bytes32, addr: address) -> None`  
  Registers or updates a mapping. Emits `NameSet`.

- `get(name: bytes32) -> address | None`  
  Returns the registered address (or empty/zero address if none).

- `has(name: bytes32) -> bool`  
  Fast existence check.

- `remove(name: bytes32) -> None`  
  Deletes a mapping. Emits `NameRemoved`.

> Some deployments additionally support a **reverse** map (address→name) with
> `setReverse`, `getReverse`, and a `ReverseSet` event. If included, it is optional
> and gated behind a small gas cost increase. See your manifest for the exact surface.

### Events

- `NameSet(name: bytes32, addr: address)`
- `NameRemoved(name: bytes32)`

Event names and argument order are canonical and match `contracts/stdlib/utils/events.py`.

---

## Name → bytes32 conventions

The contract accepts **raw 32-byte keys**. Off-chain, it’s common to derive a key as:

```python
# Python (SDK helper)
from omni_sdk.utils.hash import sha3_256

label = "alice.anim"            # your human label
name_key = sha3_256(label.encode("utf-8"))  # 32 bytes

Or, if you already have a hex string:

name_key = bytes.fromhex("e3ab…c0")  # strip "0x" first if present

Registry keys are opaque to the contract. How you encode (e.g., sha3_256,
keccak256, normalized case, dotted namespace) is a client convention.

⸻

Build → Deploy → Use

Prerequisites
	•	A node RPC endpoint (RPC_URL) and CHAIN_ID (devnet is often 1337).
	•	A funded account mnemonic (DEPLOYER_MNEMONIC) for deployment.
	•	Python 3.10+ and the repo’s pinned tools (see contracts/requirements.txt).

1) Build the package

python -m contracts.tools.build_package \
  contracts/examples/registry \
  --out contracts/build

This produces a reproducible bundle in contracts/build/ with a code hash
referenced by manifest.json.

2) Deploy to the chain

RPC_URL=http://127.0.0.1:8545 \
CHAIN_ID=1337 \
DEPLOYER_MNEMONIC="your twelve/… words" \
python -m contracts.tools.deploy \
  --package contracts/build/registry.pkg.json

The tool prints the contract address on success.

Prefer using a test/dev account; the tool never transmits secrets to servers.

3) Register a mapping

Python SDK:

from omni_sdk.rpc.http import HttpClient
from omni_sdk.contracts.client import ContractClient
from omni_sdk.utils.hash import sha3_256

RPC_URL = "http://127.0.0.1:8545"
CHAIN_ID = 1337
ADDRESS  = "anim1…"  # output from deploy step

# Load ABI from the example manifest
import json, pathlib
abi = json.loads((pathlib.Path("contracts/examples/registry/manifest.json")).read_text())

http = HttpClient(RPC_URL)
c = ContractClient(http, address=ADDRESS, abi=abi, chain_id=CHAIN_ID)

label = "alice.anim"
name_key = sha3_256(label.encode())

# write (transaction)
tx_hash = c.send("set", name_key, "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq9")  # example addr
receipt = c.wait(tx_hash)

# read (call)
resolved = c.call("get", name_key)
print("resolved:", resolved)

TypeScript SDK:

import { HttpClient } from "@animica/sdk/rpc/http";
import { ContractClient } from "@animica/sdk/contracts/client";
import { sha3_256 } from "@animica/sdk/utils/hash";

const rpcUrl = "http://127.0.0.1:8545";
const chainId = 1337;
const address = "anim1…"; // from deploy

const abi = await (await fetch("/contracts/examples/registry/manifest.json")).json();
const http = new HttpClient(rpcUrl);
const c = new ContractClient(http, { address, abi, chainId });

const label = "alice.anim";
const nameKey = sha3_256(new TextEncoder().encode(label)); // Uint8Array(32)

const txHash = await c.send("set", nameKey, "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq9");
const receipt = await c.wait(txHash);

const resolved = await c.call("get", nameKey);
console.log("resolved:", resolved);

4) Remove a mapping

c.send_and_wait("remove", name_key)
assert c.call("has", name_key) is False


⸻

Local simulation (no node needed)

Use the VM runner for fast iteration:

# Read
python -m vm_py.cli.run \
  --manifest contracts/examples/registry/manifest.json \
  --call has \
  --args '{"name":"0xaaaaaaaa…"}'

# Write (simulate) — no state persistence, but validates gas & ABI
python -m vm_py.cli.run \
  --manifest contracts/examples/registry/manifest.json \
  --call set \
  --args '{"name":"0xbbbb…", "addr":"anim1…"}'

For richer local checks, see tests_local.py in this directory.

⸻

Events & indexing

Listen for NameSet / NameRemoved to maintain a mirror index:
	•	Topic0 (event signature) is stable.
	•	Payload encoding is canonical and matches the ABI schema.
	•	Emission order is deterministic within a transaction.

Example decoder (Python):

from omni_sdk.contracts.events import decode_event_stream

for ev in decode_event_stream(receipt["logs"], abi):
    if ev["name"] == "NameSet":
        name = ev["args"]["name"]     # bytes
        addr = ev["args"]["addr"]     # address (string)


⸻

Reverts & errors
	•	Revert("name-too-long") — if the provided name buffer exceeds 32 bytes (only in variants that accept variable input and hash internally).
	•	Revert("zero-address") — set was given a zero/empty address (if enforced).
	•	Revert("not-found") — remove on a missing key (strict variant).
	•	Revert("forbidden") — if an owner/roles gate is turned on and the caller lacks permission.

Your manifest enumerates the exact error strings used by the compiled contract.

⸻

Gas notes
	•	get / has are cheap reads.
	•	set / remove cost scales with storage touched (initial write vs overwrite vs clear).
	•	Optional reverse mapping roughly doubles write cost when enabled.

See the VM gas table (vm_py/gas_table.json) for precise figures. The build_package
step generates a static upper-bound estimate; runtime consumption depends on the path taken.

⸻

Security & design
	•	Determinism first: no wall-clock, randomness, or network I/O; pure storage + hashing.
	•	Domain-separated hashing where hashing occurs on-chain (variants that accept arbitrary bytes).
	•	No implicit normalization: callers are responsible for consistent label prep (lowercasing, dots).
	•	Optional ownership/roles: gate set/remove behind Ownable or Roles from contracts/stdlib/access/.
	•	Upgrade stance: prefer immutability; if you must upgrade, pin code-hash via stdlib/upgrade/proxy.py
and publish an on-chain notice via events.

⸻

Tooling shortcuts
	•	Build
python -m contracts.tools.build_package contracts/examples/registry --out contracts/build
	•	Deploy
python -m contracts.tools.deploy --package contracts/build/registry.pkg.json --rpc-url $RPC_URL --chain-id $CHAIN_ID
	•	Call
python -m contracts.tools.call --address <anim1…> --manifest contracts/examples/registry/manifest.json --fn get --args '{"name":"0x…"}'
	•	Verify (source ↔ code hash)
python -m contracts.tools.verify --services-url https://studio-services.example --manifest contracts/examples/registry/manifest.json

⸻

Troubleshooting
	•	Invalid address format — ensure you pass a bech32m Animica address (anim1…).
	•	bad-abi-args — check argument names/types against manifest.json.
	•	No events — use the exact ABI when decoding; mismatched ABIs produce empty/misparsed logs.
	•	Result not updating on devnet — if using an explorer, wait for the next block or refresh the index cache.

⸻

License

See repository root for license terms. Third-party notices, if any, are included alongside the VM and SDK.

