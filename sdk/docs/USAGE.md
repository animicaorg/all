# Animica SDK — Usage Guide

This guide shows how to use the Animica SDKs (Python, TypeScript, Rust) to:

- Manage PQ wallets and addresses
- Build, sign, and send transactions
- Deploy and call contracts
- Decode events
- Use DA (Data Availability), AICF (AI/Quantum compute), and Randomness clients
- Verify headers/DA with the light client
- Run E2E harnesses and CLI tools

The examples assume you have a local devnet node running with JSON-RPC at `http://127.0.0.1:8545` and WS at `ws://127.0.0.1:8545/ws` (chainId `1337`). Adjust values as needed.

---

## Install

### Python
```bash
# from repo root (editable dev install)
python -m pip install -U pip
python -m pip install -e ./sdk/python

# runtime deps often used in examples
python -m pip install anyio httpx websockets msgspec cbor2

TypeScript / Node

cd sdk/typescript
npm ci
npm run build
# consume via workspace import or pack:
npm pack
# then in your app:
# npm i ../path/to/@animica-sdk-*.tgz

Rust

cd sdk/rust
cargo build


⸻

Environment

Common environment variables:
	•	RPC_URL (default http://127.0.0.1:8545)
	•	WS_URL  (default ws://127.0.0.1:8545/ws)
	•	CHAIN_ID (e.g., 1337)
	•	ALG_ID (dilithium3 or sphincs_shake_128s)

⸻

Quickstarts

Python — Deploy & Call Counter

from omni_sdk.tx import build, encode, send
from omni_sdk.wallet.signer import Signer
from omni_sdk import address as addr
import json, pathlib, asyncio

RPC = "http://127.0.0.1:8545"
CHAIN_ID = 1337
ALG = "dilithium3"

async def main():
    # Create signer from mnemonic (or use keystore)
    mnemonic = "abandon " * 11 + "about"
    signer = await Signer.from_mnemonic(mnemonic=mnemonic, alg_id=ALG, chain_id=CHAIN_ID)
    sender_payload = await signer.address_payload()
    sender_address = addr.encode_address(signer.alg_id, sender_payload)
    print("Sender:", sender_address)

    # Load sample contract
    root = pathlib.Path(__file__).resolve().parents[2] / "test-harness" / "contracts" / "counter"
    manifest = json.loads((root / "manifest.json").read_text())
    code = (root / "contract.py").read_bytes()

    # Build deploy tx
    tx_obj = await build.build_deploy(
        chain_id=CHAIN_ID,
        sender_pubkey=await signer.public_key_bytes(),
        alg_id=signer.alg_id,
        manifest=manifest,
        code=code
    )

    sig = await signer.sign(await encode.sign_bytes_for_tx(tx_obj), domain="tx")
    raw = await build.attach_signature(tx_obj, sig)

    tx_hash = await send.send_raw_transaction(RPC, raw)
    receipt = await send.wait_for_receipt(RPC, tx_hash, poll_interval_ms=500, timeout_ms=60000)
    print("Deploy receipt:", receipt)

    contract_addr = receipt.get("contractAddress", addr.encode_address(signer.alg_id, sender_payload))

    # Build call tx: inc(2)
    call_tx = await build.build_call(
        chain_id=CHAIN_ID,
        sender_pubkey=await signer.public_key_bytes(),
        alg_id=signer.alg_id,
        to=contract_addr,
        function="inc",
        args=[2]
    )
    sig2 = await signer.sign(await encode.sign_bytes_for_tx(call_tx), "tx")
    raw2 = await build.attach_signature(call_tx, sig2)

    call_hash = await send.send_raw_transaction(RPC, raw2)
    call_rcpt = await send.wait_for_receipt(RPC, call_hash, 500, 60000)
    print("inc(2) receipt:", call_rcpt)

asyncio.run(main())

TypeScript — Deploy & Call Counter (Node)

import { wallet, tx, address, rpc } from "@animica/sdk";
import * as fs from "node:fs/promises";
import * as path from "node:path";

const RPC = process.env.RPC_URL ?? "http://127.0.0.1:8545";
const CHAIN = Number(process.env.CHAIN_ID ?? 1337);
const ALG = process.env.ALG_ID ?? "dilithium3";

async function main() {
  const http = new rpc.http.HttpClient(RPC);

  const { mnemonic } = await wallet.mnemonic.createMnemonic();
  const seed = await wallet.mnemonic.mnemonicToSeed(mnemonic);
  const signer = await wallet.signer.fromSeed({ seed, algId: ALG, accountIndex: 0, chainId: CHAIN });

  const sender = address.encodeAddress(signer.algId, await signer.addressPayload());
  console.log("Sender:", sender);

  const root = path.resolve(__dirname, "../../test-harness/contracts/counter");
  const manifest = JSON.parse(await fs.readFile(path.join(root, "manifest.json"), "utf8"));
  const code = await fs.readFile(path.join(root, "contract.py"));

  const built = await tx.build.buildDeploy({
    chainId: CHAIN,
    senderPubkey: await signer.publicKeyBytes(),
    algId: signer.algId,
    manifest,
    code
  });
  const sig = await signer.sign(await tx.encode.signBytesForTx(built), "tx");
  const raw = await tx.build.attachSignature(built, sig);

  const txHash = await tx.send.sendRawTransaction(http, raw);
  const rcpt = await tx.send.waitForReceipt(http, txHash, { pollIntervalMs: 500, timeoutMs: 60_000 });
  console.log("Deploy receipt:", rcpt);
}

main().catch(console.error);

Rust — Deploy & Call (library snippet)

use animica_sdk::{
  rpc::http::HttpClient,
  wallet::{mnemonic, signer::Signer},
  tx::{build, encode, send},
  address,
};
use std::fs;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let rpc = HttpClient::new("http://127.0.0.1:8545".into());
    let chain_id = 1337u64;

    let m = mnemonic::create_mnemonic()?;
    let seed = mnemonic::mnemonic_to_seed(&m)?;
    let signer = Signer::from_seed(&seed, "dilithium3", 0, chain_id).await?;
    let sender = address::encode_address(signer.alg_id(), &signer.address_payload().await?);

    let manifest = fs::read_to_string("sdk/test-harness/contracts/counter/manifest.json")?;
    let code = fs::read("sdk/test-harness/contracts/counter/contract.py")?;

    let built = build::build_deploy(chain_id, &signer.public_key_bytes().await?, signer.alg_id(), &manifest, &code).await?;
    let sign_bytes = encode::sign_bytes_for_tx(&built).await?;
    let sig = signer.sign(&sign_bytes, "tx").await?;
    let raw = build::attach_signature(&built, &sig).await?;

    let tx_hash = send::send_raw_transaction(&rpc, &raw).await?;
    let rcpt = send::wait_for_receipt(&rpc, &tx_hash, 500, 60_000).await?;
    println!("deploy receipt: {:?}", rcpt);
    Ok(())
}


⸻

Wallets & Addresses

Python

from omni_sdk.wallet.signer import Signer
from omni_sdk import address

signer = await Signer.from_mnemonic("abandon ... about", alg_id="dilithium3", chain_id=1337)
payload = await signer.address_payload()
addr_b32 = address.encode_address(signer.alg_id, payload)
assert address.is_valid(addr_b32)

TypeScript

import { wallet, address } from "@animica/sdk";

const { mnemonic } = await wallet.mnemonic.createMnemonic();
const seed = await wallet.mnemonic.mnemonicToSeed(mnemonic);
const s = await wallet.signer.fromSeed({ seed, algId: "dilithium3", accountIndex: 0, chainId: 1337 });
const addrStr = address.encodeAddress(s.algId, await s.addressPayload());

Rust

use animica_sdk::{wallet::mnemonic, wallet::signer::Signer, address};

let seed = mnemonic::mnemonic_to_seed("abandon ... about")?;
let s = Signer::from_seed(&seed, "dilithium3", 0, 1337).await?;
let addr_str = address::encode_address(s.alg_id(), &s.address_payload().await?);


⸻

Transactions

Transfer

Python:

tx_obj = await build.build_transfer(
    chain_id=1337,
    sender_pubkey=await signer.public_key_bytes(),
    alg_id=signer.alg_id,
    to="anim1…",  # bech32m
    amount=123456,  # smallest unit
)
sig = await signer.sign(await encode.sign_bytes_for_tx(tx_obj), "tx")
raw = await build.attach_signature(tx_obj, sig)
tx_hash = await send.send_raw_transaction(RPC, raw)

TypeScript:

const built = await tx.build.buildTransfer({
  chainId: 1337,
  senderPubkey: await signer.publicKeyBytes(),
  algId: signer.algId,
  to: "anim1…",
  amount: 123456
});
const sig = await signer.sign(await tx.encode.signBytesForTx(built), "tx");
const raw = await tx.build.attachSignature(built, sig);
const txHash = await tx.send.sendRawTransaction(http, raw);

Rust (API mirrors Python/TS):

let built = build::build_transfer(1337, &signer.public_key_bytes().await?, signer.alg_id(), "anim1...", 123456).await?;
let sign_bytes = encode::sign_bytes_for_tx(&built).await?;
let sig = signer.sign(&sign_bytes, "tx").await?;
let raw = build::attach_signature(&built, &sig).await?;
let tx_hash = send::send_raw_transaction(&rpc, &raw).await?;


⸻

Contracts

Deploy

(see Quickstarts)

Call

Python:

call_tx = await build.build_call(
  chain_id=1337,
  sender_pubkey=await signer.public_key_bytes(),
  alg_id=signer.alg_id,
  to=contract_addr,
  function="inc",
  args=[1]
)

Events

Python:

from omni_sdk.contracts.events import decode_receipt_events
decoded = decode_receipt_events(manifest["abi"], receipt)
for ev in decoded:
    print(ev["name"], ev["args"])

TypeScript:

import { contracts } from "@animica/sdk";
const decoded = contracts.events.decodeReceiptEvents(manifest.abi, receipt);
decoded.forEach(ev => console.log(ev.name, ev.args));

Rust:

use animica_sdk::contracts::events;
let decoded = events::decode_receipt_events(&abi_json, &receipt)?;


⸻

Data Availability (DA)

Python:

from omni_sdk.da.client import DAClient
da = DAClient(RPC)
commitment, receipt = await da.post_blob(namespace=24, data=b"hello DA")
blob = await da.get_blob(commitment)
proof = await da.get_proof(commitment)
assert blob == b"hello DA"

TypeScript:

import { da } from "@animica/sdk";
const client = new da.client.DAClient(http);
const { commitment, receipt } = await client.postBlob(24, new Uint8Array([1,2,3]));
const data = await client.getBlob(commitment);
const proof = await client.getProof(commitment);

Rust:

use animica_sdk::da::client::DAClient;
let client = DAClient::new(rpc.clone());
let (commitment, receipt) = client.post_blob(24, b"hello").await?;
let data = client.get_blob(&commitment).await?;


⸻

AICF (AI/Quantum)

Python:

from omni_sdk.aicf.client import AICFClient
aicf = AICFClient(RPC)
job = await aicf.enqueue_ai(model="tiny", prompt="hello")
result = await aicf.get_result(job["taskId"], wait=True, timeout_s=30)
print(result)

TypeScript:

import { aicf } from "@animica/sdk";
const client = new aicf.client.AICFClient(http);
const job = await client.enqueueAI({ model: "tiny", prompt: "hello" });
const result = await client.getResult(job.taskId, { wait: true, timeoutMs: 30_000 });

Rust:

use animica_sdk::aicf::client::AICFClient;
let client = AICFClient::new(rpc.clone());
let job = client.enqueue_ai("tiny", "hello").await?;
let res = client.get_result(&job.task_id, true, 30_000).await?;


⸻

Randomness (Commit–Reveal & Beacon)

Python:

from omni_sdk.randomness.client import RandomnessClient
rand = RandomnessClient(RPC)
params = await rand.get_params()
round_info = await rand.get_round()
commit = await rand.commit(salt=b"\x00"*32, payload=b"hi")
reveal = await rand.reveal(salt=b"\x00"*32, payload=b"hi")
beacon = await rand.get_beacon()

TypeScript:

import { randomness } from "@animica/sdk";
const rc = new randomness.client.RandomnessClient(http);
const round = await rc.getRound();


⸻

Light Client (Headers + DA light proofs)

Python:

from omni_sdk.light_client.verify import verify_light_block
ok = await verify_light_block(
  rpc_url=RPC,
  header=header_obj,
  da_light_proof=proof_obj
)
assert ok

TypeScript:

import { light_client } from "@animica/sdk";
const ok = await light_client.verify.verifyLightBlock(http, header, proof);

Rust:

use animica_sdk::light_client::verify;
let ok = verify::verify_light_block(&rpc, &header, &proof).await?;


⸻

Proof Tools (dev)

Python:

from omni_sdk.proofs.hashshare import build_local_hashshare, verify_hashshare
proof = await build_local_hashshare(header_tmpl, nonce=123)
assert await verify_hashshare(proof)


⸻

CLI

We ship a Python CLI named omni-sdk:

# Help
omni-sdk --help

# Deploy (reads manifest & code)
omni-sdk deploy --rpc $RPC_URL --chain $CHAIN_ID \
  --manifest path/to/manifest.json --code path/to/contract.py \
  --alg dilithium3 --mnemonic "abandon ... about"

# Call a function (write)
omni-sdk call --rpc $RPC_URL --chain $CHAIN_ID \
  --to anim1... --function inc --arg 1 \
  --alg dilithium3 --mnemonic "abandon ... about"

# Subscribe to new heads
omni-sdk subscribe --ws $WS_URL newHeads


⸻

E2E Harness
	•	Python: python sdk/test-harness/run_e2e_py.py --rpc $RPC_URL --chain $CHAIN_ID
	•	TypeScript: node sdk/test-harness/run_e2e_ts.mjs --rpc $RPC_URL --chain $CHAIN_ID
	•	Rust: bash sdk/test-harness/run_e2e_rs.sh --rpc $RPC_URL --chain $CHAIN_ID

A GitHub Actions example is provided in sdk/test-harness/ci_matrix.yml.

⸻

Tips
	•	ChainId: Prefer discovering via chain.getChainId.
	•	Gas: Builders include intrinsic estimation; use conservative headroom for complex calls.
	•	PQ Algorithms: Default is dilithium3. sphincs_shake_128s is supported; signatures are larger.
	•	Bech32m Addresses: Always validate before sending (helpers are provided in each language).
	•	Determinism: Contract gas usage and logs are deterministic; simulations in-browser use studio-wasm.

⸻

If you spot any mismatches between these docs and the SDK APIs, open an issue in the repo. Happy hacking!
