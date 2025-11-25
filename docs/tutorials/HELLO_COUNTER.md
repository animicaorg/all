# Hello, Counter — Deploy & Call Your First Contract

This tutorial walks you end-to-end: run a local devnet, deploy the **Counter** contract (Python-VM), and call `inc()` / `get()` using the SDKs. You can do it with **Python** or **TypeScript** (both shown).

> If you prefer a GUI, you can do the same flow in **Studio Web** (simulate → deploy → verify). This guide focuses on the CLI/SDK path.

---

## 0) Prerequisites

- **Python 3.10+** and **pip**
- **Node.js 18+** and **pnpm** or **npm**
- OS packages needed for building (Rust is optional, only for native speedups)

Clone the repo and enter it:

```bash
git clone https://example.com/animica/animica.git
cd animica

(Optional) create a virtualenv:

python -m venv .venv && source .venv/bin/activate

Install local packages you’ll use in this tutorial:

# Python SDK + VM (compiler/runtime) + RPC server deps
pip install -e sdk/python -e vm_py -e rpc -e core -e mining

Or, if you prefer TypeScript SDK too:

pnpm i --filter @animica/sdk --workspace-root
# or: npm i --workspace=sdk/typescript


⸻

1) Start a Local Devnet (Node + RPC + Miner)

Initialize a fresh database and write the genesis header:

python -m core.boot \
  --genesis core/genesis/genesis.json \
  --db sqlite:///animica_dev.db

Export the DB path so the RPC server can find it:

export ANIMICA_DB=sqlite:///animica_dev.db

Run the RPC server (HTTP JSON-RPC on :8545, WS on :8546):

python -m rpc.server --host 127.0.0.1 --port 8545

In another terminal, start the built-in CPU miner so blocks get produced:

python -m mining.cli.miner --threads 2 --device cpu

You now have:
	•	a dev chain advancing,
	•	an RPC endpoint at http://127.0.0.1:8545,
	•	WS at ws://127.0.0.1:8546.

⸻

2) Compile the Counter Contract (Python-VM)

We’ll use the canonical example contract from the repo:
	•	Source: vm_py/examples/counter/contract.py
	•	Manifest (ABI & metadata): vm_py/examples/counter/manifest.json

Compile it to IR (bytecode) with the VM CLI:

# Produces IR + gas estimate; writes IR to /tmp/counter.ir
python -m vm_py.cli.compile \
  vm_py/examples/counter/contract.py \
  --out /tmp/counter.ir

Tip: You can also simulate locally (no node/state write) with studio-wasm in the browser, or with python -m vm_py.cli.run for quick checks.

⸻

3A) Deploy & Call with Python SDK

3A.1) Install & Configure

If you didn’t earlier:

pip install -e sdk/python

Create hello_counter.py:

from pathlib import Path
from omni_sdk.config import Config
from omni_sdk.wallet.mnemonic import new_mnemonic
from omni_sdk.wallet.signer import Dilithium3Signer
from omni_sdk.address import address_from_pubkey
from omni_sdk.tx.build import build_deploy_tx, build_call_tx
from omni_sdk.tx.send import send_and_await_receipt
from omni_sdk.rpc.http import HttpRpc
from omni_sdk.contracts.client import ContractClient
import json

RPC_URL = "http://127.0.0.1:8545"
CHAIN_ID = 1  # animica localnet/test chain uses 1 in core/genesis by default

cfg = Config(rpc_url=RPC_URL, chain_id=CHAIN_ID)
rpc = HttpRpc(cfg)

# 1) Create a fresh wallet (Dilithium3 signer)
mnemo = new_mnemonic()
signer = Dilithium3Signer.from_mnemonic(mnemo)
pub = signer.public_key()
addr = address_from_pubkey("dilithium3", pub)

print("Mnemonic:", mnemo)
print("Address:", addr)

# 2) Load compiled code & manifest
ir_bytes = Path("/tmp/counter.ir").read_bytes()
manifest = json.loads(Path("vm_py/examples/counter/manifest.json").read_text())

# 3) Build a deploy transaction
deploy_tx = build_deploy_tx(
    from_address=addr,
    manifest=manifest,
    code=ir_bytes,
    gas_price=1,         # tiny devnet price
    gas_limit=200_000,   # safe headroom for demo
    nonce=0
)

# 4) Sign & send
signed_deploy = signer.sign_tx(deploy_tx, chain_id=CHAIN_ID)
deploy_receipt = send_and_await_receipt(rpc, signed_deploy, timeout_s=30)
print("Deploy status:", deploy_receipt["status"])
contract_address = deploy_receipt["contractAddress"]
print("Deployed at:", contract_address)

# 5) Make a contract client (ABI-based)
client = ContractClient(rpc=rpc, address=contract_address, abi=manifest["abi"])

# 6) Call inc()
call_inc = build_call_tx(
    from_address=addr,
    to_address=contract_address,
    abi=manifest["abi"],
    function="inc",
    args=[],
    gas_price=1,
    gas_limit=80_000,
    nonce=1
)
signed_inc = signer.sign_tx(call_inc, chain_id=CHAIN_ID)
inc_receipt = send_and_await_receipt(rpc, signed_inc, timeout_s=30)
print("inc() status:", inc_receipt["status"])

# 7) Call get() (view/read – some setups do this via a read-only simulate)
result = client.call("get", [])
print("Counter value:", result)

Run it:

python hello_counter.py

You should see a successful deploy, then inc() succeeds, and get() returns 1.

⸻

3B) Deploy & Call with TypeScript SDK

3B.1) Install & Setup

# From repo root (with workspaces) or cd sdk/typescript then install
pnpm i

Create hello-counter.ts:

import { HttpRpc, Config, Wallet, Signer, Tx, Contracts } from "@animica/sdk";
import fs from "node:fs";

const RPC_URL = "http://127.0.0.1:8545";
const CHAIN_ID = 1;

async function main() {
  const cfg = new Config({ rpcUrl: RPC_URL, chainId: CHAIN_ID });
  const rpc = new HttpRpc(cfg);

  // 1) Wallet + signer (Dilithium3)
  const mnemonic = Wallet.newMnemonic();
  const signer = await Signer.dilithium3FromMnemonic(mnemonic);
  const addr = Wallet.addressFromPubkey("dilithium3", await signer.publicKey());
  console.log("Mnemonic:", mnemonic);
  console.log("Address:", addr);

  // 2) Load IR + manifest
  const ir = fs.readFileSync("/tmp/counter.ir");
  const manifest = JSON.parse(
    fs.readFileSync("vm_py/examples/counter/manifest.json", "utf8")
  );

  // 3) Build deploy tx
  const deploy = Tx.buildDeploy({
    from: addr,
    manifest,
    code: ir,
    gasPrice: 1n,
    gasLimit: 200_000n,
    nonce: 0n,
  });

  // 4) Sign & send
  const signedDeploy = await signer.signTx(deploy, CHAIN_ID);
  const deployRcpt = await Tx.sendAndAwaitReceipt(rpc, signedDeploy, { timeoutMs: 30_000 });
  if (deployRcpt.status !== "SUCCESS") throw new Error("Deploy failed");
  const contract = deployRcpt.contractAddress!;
  console.log("Deployed at:", contract);

  // 5) Contract client
  const client = new Contracts.Client(rpc, contract, manifest.abi);

  // 6) Call inc()
  const callInc = Tx.buildCall({
    from: addr,
    to: contract,
    abi: manifest.abi,
    function: "inc",
    args: [],
    gasPrice: 1n,
    gasLimit: 80_000n,
    nonce: 1n,
  });
  const signedInc = await signer.signTx(callInc, CHAIN_ID);
  const incRcpt = await Tx.sendAndAwaitReceipt(rpc, signedInc, { timeoutMs: 30_000 });
  console.log("inc() status:", incRcpt.status);

  // 7) Read get() (view call)
  const value = await client.call("get", []);
  console.log("Counter value:", value);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

Run it:

pnpm tsx hello-counter.ts
# or: npx tsx hello-counter.ts

You should see a deploy, then inc() success, then get() = 1.

⸻

4) Troubleshooting
	•	RPC unreachable: ensure python -m rpc.server is running on :8545 and your ANIMICA_DB env points to the same DB used by core.boot.
	•	No blocks: start the miner (python -m mining.cli.miner ...). Without new blocks, deploy/calls won’t get included.
	•	Gas/OOG: increase gas_limit; devnet gas costs are conservative but safe.
	•	Nonce too low/high: bump nonce manually or query it via state.getNonce and match it in your TX builder.
	•	PQ signer missing: ensure SDKs are installed and your environment meets any optional native deps. Pure-Python/WASM fallbacks exist but are slower.

⸻

5) What’s Next?
	•	Events & logs: subscribe over WS or decode with the SDK event helpers.
	•	Studio Web: open the Counter example, simulate locally (in-browser Pyodide), then deploy to your devnet.
	•	Capabilities: try blob_pin, ai_enqueue, zk.verify once you’re comfortable with deploy/call.
	•	Write your own: start from vm_py/examples/escrow or the docs in docs/vm/*.

Happy building! 🚀
