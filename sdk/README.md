# Animica SDKs — Python · TypeScript · Rust

Multi-language client libraries to talk to an Animica node and related services.

- **Core RPC**: JSON-RPC/WS for blocks, txs, receipts, events, subscriptions.
- **Tx helpers**: build/encode (deterministic CBOR), sign (PQ), send & await.
- **Contracts**: ABI validation, typed clients, event decoding, code-gen.
- **Capabilities**: Data Availability (DA), AICF (AI/Quantum), Randomness beacon.
- **Light verify**: minimal header/DA proof checks for light clients.
- **Examples & tests**: end-to-end demos and a cross-language test harness.

> Packages:
> - Python: `omni-sdk` (module: `omni_sdk`)
> - TypeScript: `@animica/sdk`
> - Rust: `animica-sdk`

---

## Install

### Python
```bash
# From PyPI (preferred)
pip install omni-sdk

# Or from this repo (editable dev)
pip install -e sdk/python

TypeScript / JavaScript

# Browser or Node
npm i @animica/sdk
# or
pnpm add @animica/sdk

Rust

In Cargo.toml:

[dependencies]
animica-sdk = "0.1"


## Minimal example (Python)

```python
from omni_sdk.address import to_address
from omni_sdk.rpc.http import RpcClient
from omni_sdk.tx.build import suggest_gas_limit, transfer
from omni_sdk.tx.encode import pack_signed, sign_bytes
from omni_sdk.tx.send import submit_and_wait
from omni_sdk.wallet.mnemonic import from_mnemonic
from omni_sdk.wallet.signer import Dilithium3Signer

rpc = RpcClient("http://127.0.0.1:8545")

# Fetch the latest block
head = rpc.request("chain_getHead")
print("Latest height:", head["height"])

# Build and send a simple self-transfer
mnemonic = "... 24 words ..."  # supply your dev/test mnemonic
signer = Dilithium3Signer(from_mnemonic(mnemonic))
sender = to_address(signer.public_key())

tx = transfer(
    from_addr=sender,
    to_addr=sender,
    amount=1,
    nonce=0,
    chain_id=1,
    max_fee=50_000,
    gas_limit=suggest_gas_limit("transfer"),
)

sig = signer.sign(sign_bytes(tx))
raw = pack_signed(
    tx, signature=sig, alg_id=signer.alg_id, public_key=signer.public_key()
)

receipt = submit_and_wait(rpc, raw)
print("Included in block", receipt.get("blockHeight"))
```


⸻

Quickstart — Python

from omni_sdk.config import Config
from omni_sdk.rpc.http import HttpClient
from omni_sdk.wallet.mnemonic import new_mnemonic, from_mnemonic
from omni_sdk.wallet.signer import Dilithium3Signer
from omni_sdk.address import to_address
from omni_sdk.tx.build import build_transfer
from omni_sdk.tx.send import send_and_wait
from omni_sdk.contracts.client import ContractClient

RPC_URL   = "http://localhost:8545"
CHAIN_ID  = 1337

# 1) RPC client
cfg = Config(rpc_url=RPC_URL, chain_id=CHAIN_ID, timeout_s=20)
rpc = HttpClient(cfg)

# 2) Wallet (Dilithium3 by default; SPHINCS+ available)
mnemo = new_mnemonic()  # or read from env
sk = from_mnemonic(mnemo)
signer = Dilithium3Signer(sk)
addr = to_address(signer.public_key())

# 3) Simple transfer
tx = build_transfer(from_addr=addr, to_addr=addr, amount=1, fee=10_000)
rcpt = send_and_wait(rpc, tx, signer)
print("Transfer included at", rcpt["blockHeight"])

# 4) Call a contract (ABI-based)
counter_abi = rpc.get_abi("anim1...counter")  # or load from file
counter = ContractClient(rpc, address="anim1...counter", abi=counter_abi)
print("Current value:", counter.call("get"))
tx2 = counter.tx("inc", args=[1], from_addr=addr, fee=15_000)
rcpt2 = send_and_wait(rpc, tx2, signer)
print("New value:", counter.call("get"))

WebSockets (subscribe to heads)

import asyncio
from omni_sdk.rpc.ws import WsClient

async def main():
    ws = WsClient(RPC_URL.replace("http","ws"))
    async for head in ws.subscribe_new_heads():
        print("New head:", head["height"])

asyncio.run(main())


⸻

Quickstart — TypeScript

import { HttpClient } from '@animica/sdk/rpc/http'
import { WsClient } from '@animica/sdk/rpc/ws'
import { mnemonicToSeed, Dilithium3Signer } from '@animica/sdk/wallet/signer'
import { toAddress } from '@animica/sdk/address'
import { buildTransfer } from '@animica/sdk/tx/build'
import { sendAndWait } from '@animica/sdk/tx/send'
import { ContractClient } from '@animica/sdk/contracts/client'

const RPC_URL = 'http://localhost:8545'
const CHAIN_ID = 1337

const rpc = new HttpClient({ url: RPC_URL, chainId: CHAIN_ID })

async function main() {
  // Wallet
  const mnemonic = 'shoot island position ...' // dev only
  const seed = await mnemonicToSeed(mnemonic)
  const signer = await Dilithium3Signer.fromSeed(seed)
  const addr = toAddress(signer.publicKey)

  // Transfer
  const tx = await buildTransfer({ from: addr, to: addr, amount: 1n, fee: 10_000n })
  const rcpt = await sendAndWait(rpc, tx, signer)
  console.log('Included in', rcpt.blockHeight)

  // Contract call
  const abi = await rpc.getAbi('anim1...counter')
  const counter = new ContractClient(rpc, 'anim1...counter', abi)
  console.log('value =', await counter.call('get'))
  const tx2 = await counter.tx('inc', [1], { from: addr, fee: 15_000n })
  await sendAndWait(rpc, tx2, signer)
  console.log('value =', await counter.call('get'))

  // WS heads
  const ws = new WsClient(RPC_URL.replace('http', 'ws'))
  ws.on('newHeads', (h) => console.log('head', h.height))
  await ws.connect()
}
main().catch(console.error)


⸻

Quickstart — Rust

use animica_sdk::{
    rpc::http::HttpClient,
    wallet::{mnemonic::Mnemonic, signer::Dilithium3Signer},
    address::to_address,
    tx::{build::build_transfer, send::send_and_wait},
    contracts::client::ContractClient,
};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let rpc = HttpClient::new("http://localhost:8545", 1337)?;

    // Wallet
    let m = Mnemonic::random();
    let sk = m.to_seed()?;
    let signer = Dilithium3Signer::from_seed(&sk)?;
    let addr = to_address(&signer.public_key());

    // Transfer
    let tx = build_transfer(&addr, &addr, 1, 10_000);
    let rcpt = send_and_wait(&rpc, &signer, tx).await?;
    println!("Included at {}", rcpt.block_height);

    // Contract
    let abi = rpc.get_abi("anim1...counter").await?;
    let counter = ContractClient::new(rpc.clone(), "anim1...counter".into(), abi);
    println!("value={}", counter.call("get", ()).await?);
    let tx2 = counter.tx("inc", (1u64,), Some((&addr, 15_000)));
    send_and_wait(&rpc, &signer, tx2).await?;
    println!("value={}", counter.call("get", ()).await?);

    Ok(())
}


⸻

Capabilities

Data Availability (DA)
	•	Pin/get blobs; verify commitment and inclusion proofs.
	•	Python: omni_sdk.da.client, TS: @animica/sdk/da/client, Rust: da::client.

AICF (AI / Quantum)
	•	Enqueue jobs; poll results; verify receipts (on-chain linkable).
	•	Python: omni_sdk.aicf.client, TS: aicf/client, Rust: aicf::client.

Randomness
	•	Commit/reveal tools; get latest beacon; verify light proof.
	•	Python: randomness/client, TS: randomness/client, Rust: randomness::client.

⸻

Code Generation

Generate typed contract clients from ABI:

Python

python -m sdk.codegen.cli --lang py --abi sdk/common/examples/counter_abi.json --out ./generated/py

TypeScript

npx animica-codegen --abi sdk/common/examples/counter_abi.json --out ./generated/ts

Rust

cargo run -p sdk-codegen-rust -- --abi sdk/common/examples/counter_abi.json --out ./generated/rs


⸻

Configuration

All SDKs accept a minimal config:
	•	rpc_url / url
	•	chain_id
	•	timeout / retries (optional)

Environment variables commonly used in examples:

ANIMICA_RPC_URL=http://localhost:8545
ANIMICA_CHAIN_ID=1337


⸻

Examples & Tests

Per-language examples live under:
	•	sdk/python/examples/*
	•	sdk/typescript/examples/*
	•	sdk/rust/examples/*

Cross-language E2E harness:

make -C sdk e2e          # run Python + TS + Rust quickstarts against a devnet


⸻

PQ Signatures

By default, the SDKs expose Dilithium3 signers, with optional SPHINCS+. Domain-separated sign bytes ensure signatures are not replayable across chains or message kinds.

⸻

Support Matrix

Feature	Python	TypeScript	Rust
JSON-RPC/WS	✓	✓	✓
Tx build/encode	✓	✓	✓
PQ signers	✓	✓	✓
Contracts (ABI)	✓	✓	✓
Events decoding	✓	✓	✓
DA / AICF / Rand	✓	✓	✓
Codegen	✓	✓	✓
Light verify	✓	✓	✓


⸻

Security Notes
	•	Keys remain client-side; never send secrets to remote services.
	•	Use hardware-backed storage where available.
	•	Always check chainId to prevent cross-chain replay.
	•	Verify contract source/code hash when interacting with third-party contracts.

⸻

License

See sdk/LICENSE. Each language subpackage may carry additional notices.

