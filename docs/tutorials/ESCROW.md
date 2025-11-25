# Escrow — Disputes & Events

This tutorial shows an end-to-end escrow flow on Animica's Python-VM:
- open an escrow between a **buyer** and **seller**
- deposit funds, release or refund
- raise a **dispute** and resolve it
- read **events** (logs) from receipts or via subscriptions

It uses the sample contract at `vm_py/examples/escrow/contract.py` with its
matching `vm_py/examples/escrow/manifest.json`.

> You can also simulate the escrow locally in the browser using **studio-wasm**,
> then deploy to a devnet the same way you did for the Counter tutorial.

---

## 0) Prerequisites

- Follow **Hello, Counter** to get a local devnet running:
  - `python -m rpc.server --port 8545`
  - `python -m mining.cli.miner --threads 2`
- Have the VM and SDKs installed (Python and/or TypeScript).
- Compile the escrow example to IR:

```bash
python -m vm_py.cli.compile \
  vm_py/examples/escrow/contract.py \
  --out /tmp/escrow.ir


⸻

1) Contract Interface (ABI Summary)

The example escrow exposes a small ABI (names may differ slightly across versions):
	•	open(order_id: bytes, seller: address, amount: u128) → creates an escrow; buyer is msg.sender.
	•	Event: EscrowOpened(order_id, buyer, seller, amount)
	•	deposit(order_id: bytes) → buyer deposits funds (must match amount).
	•	Event: Deposited(order_id, from, amount)
	•	release(order_id: bytes) → buyer releases to seller.
	•	Event: Released(order_id, buyer, seller, amount)
	•	refund(order_id: bytes) → seller refunds to buyer (if agreed / not shipped).
	•	Event: Refunded(order_id, buyer, seller, amount)
	•	open_dispute(order_id: bytes, reason: bytes) → buyer or seller opens a dispute.
	•	Event: DisputeOpened(order_id, who, reason)
	•	resolve_dispute(order_id: bytes, buyer_share: u128, seller_share: u128) → arbitrator resolves.
	•	Event: DisputeResolved(order_id, buyer_share, seller_share)

The exact events & topics are defined in the example manifest’s ABI. You can
inspect them directly or use SDK helpers to decode logs.

⸻

2) Deploy & Happy-Path Flow (Python SDK)

# file: escrow_happy_path.py
from pathlib import Path
import json
from omni_sdk.config import Config
from omni_sdk.rpc.http import HttpRpc
from omni_sdk.wallet.mnemonic import new_mnemonic
from omni_sdk.wallet.signer import Dilithium3Signer
from omni_sdk.address import address_from_pubkey
from omni_sdk.tx.build import build_deploy_tx, build_call_tx
from omni_sdk.tx.send import send_and_await_receipt
from omni_sdk.contracts.client import ContractClient

RPC_URL = "http://127.0.0.1:8545"
CHAIN_ID = 1

cfg = Config(rpc_url=RPC_URL, chain_id=CHAIN_ID)
rpc = HttpRpc(cfg)

# --- Buyer & Seller wallets (demo keys) ---
mn_buyer = new_mnemonic()
mn_seller = new_mnemonic()
buyer = Dilithium3Signer.from_mnemonic(mn_buyer)
seller = Dilithium3Signer.from_mnemonic(mn_seller)

addr_buyer = address_from_pubkey("dilithium3", buyer.public_key())
addr_seller = address_from_pubkey("dilithium3", seller.public_key())
print("Buyer:", addr_buyer)
print("Seller:", addr_seller)

# --- Load IR & manifest ---
ir_bytes = Path("/tmp/escrow.ir").read_bytes()
manifest = json.loads(Path("vm_py/examples/escrow/manifest.json").read_text())

# --- Deploy (by buyer for simplicity) ---
deploy_tx = build_deploy_tx(
    from_address=addr_buyer,
    manifest=manifest,
    code=ir_bytes,
    gas_price=1,
    gas_limit=300_000,
    nonce=0
)
signed_deploy = buyer.sign_tx(deploy_tx, chain_id=CHAIN_ID)
rcpt_deploy = send_and_await_receipt(rpc, signed_deploy, timeout_s=30)
assert rcpt_deploy["status"] == "SUCCESS", rcpt_deploy
contract = rcpt_deploy["contractAddress"]
print("Escrow contract:", contract)

client = ContractClient(rpc=rpc, address=contract, abi=manifest["abi"])

# --- Open escrow: buyer indicates seller & amount ---
order_id = bytes.fromhex("aabbcc")  # short demo id
amount = 1_000_000  # 1e6 (demo units)
tx_open = build_call_tx(
    from_address=addr_buyer,
    to_address=contract,
    abi=manifest["abi"],
    function="open",
    args=[order_id, addr_seller, amount],
    gas_price=1,
    gas_limit=120_000,
    nonce=1
)
rcpt_open = send_and_await_receipt(rpc, buyer.sign_tx(tx_open, CHAIN_ID), 30)
print("open() ->", rcpt_open["status"])

# --- Buyer deposits exact amount ---
tx_dep = build_call_tx(
    from_address=addr_buyer,
    to_address=contract,
    abi=manifest["abi"],
    function="deposit",
    args=[order_id],
    gas_price=1,
    gas_limit=120_000,
    nonce=2
)
rcpt_dep = send_and_await_receipt(rpc, buyer.sign_tx(tx_dep, CHAIN_ID), 30)
print("deposit() ->", rcpt_dep["status"])

# --- Buyer releases to seller (happy path) ---
tx_rel = build_call_tx(
    from_address=addr_buyer,
    to_address=contract,
    abi=manifest["abi"],
    function="release",
    args=[order_id],
    gas_price=1,
    gas_limit=120_000,
    nonce=3
)
rcpt_rel = send_and_await_receipt(rpc, buyer.sign_tx(tx_rel, CHAIN_ID), 30)
print("release() ->", rcpt_rel["status"])

# --- Decode events from the release receipt (optional) ---
for log in rcpt_rel.get("logs", []):
    name, data = client.decode_event(log)
    print("Event:", name, data)

Run:

python escrow_happy_path.py

You should see SUCCESS for each call and Event: Released(...) in the last step.

⸻

3) Dispute Flow & Resolution (TypeScript SDK)

// file: escrow_dispute.ts
import fs from "node:fs";
import { Config, HttpRpc, Wallet, Signer, Tx, Contracts } from "@animica/sdk";

const RPC_URL = "http://127.0.0.1:8545";
const CHAIN_ID = 1;

async function main() {
  const cfg = new Config({ rpcUrl: RPC_URL, chainId: CHAIN_ID });
  const rpc = new HttpRpc(cfg);

  // Parties
  const buyerMnemonic = Wallet.newMnemonic();
  const sellerMnemonic = Wallet.newMnemonic();
  const buyer = await Signer.dilithium3FromMnemonic(buyerMnemonic);
  const seller = await Signer.dilithium3FromMnemonic(sellerMnemonic);

  const addrBuyer = Wallet.addressFromPubkey("dilithium3", await buyer.publicKey());
  const addrSeller = Wallet.addressFromPubkey("dilithium3", await seller.publicKey());
  console.log({ addrBuyer, addrSeller });

  // Load IR + manifest
  const ir = fs.readFileSync("/tmp/escrow.ir");
  const manifest = JSON.parse(fs.readFileSync("vm_py/examples/escrow/manifest.json", "utf8"));

  // Deploy (buyer)
  const deploy = Tx.buildDeploy({
    from: addrBuyer, manifest, code: ir,
    gasPrice: 1n, gasLimit: 300_000n, nonce: 0n
  });
  const rcptDeploy = await Tx.sendAndAwaitReceipt(rpc, await buyer.signTx(deploy, CHAIN_ID));
  if (rcptDeploy.status !== "SUCCESS") throw new Error("deploy failed");
  const contract = rcptDeploy.contractAddress!;
  const client = new Contracts.Client(rpc, contract, manifest.abi);

  const orderId = Buffer.from("a1b2c3", "hex");
  const amount = 1_000_000n;

  // open
  const openTx = Tx.buildCall({
    from: addrBuyer, to: contract, abi: manifest.abi,
    function: "open", args: [orderId, addrSeller, amount],
    gasPrice: 1n, gasLimit: 120_000n, nonce: 1n
  });
  await Tx.sendAndAwaitReceipt(rpc, await buyer.signTx(openTx, CHAIN_ID));

  // deposit
  const depTx = Tx.buildCall({
    from: addrBuyer, to: contract, abi: manifest.abi,
    function: "deposit", args: [orderId],
    gasPrice: 1n, gasLimit: 120_000n, nonce: 2n
  });
  await Tx.sendAndAwaitReceipt(rpc, await buyer.signTx(depTx, CHAIN_ID));

  // dispute (seller claims not paid or item mismatch; either party may open)
  const reason = Buffer.from("item_mismatch");
  const openDisputeTx = Tx.buildCall({
    from: addrSeller, to: contract, abi: manifest.abi,
    function: "open_dispute", args: [orderId, reason],
    gasPrice: 1n, gasLimit: 120_000n, nonce: 0n // seller's first tx
  });
  const rcptDispute = await Tx.sendAndAwaitReceipt(rpc, await seller.signTx(openDisputeTx, CHAIN_ID));
  console.log("open_dispute status:", rcptDispute.status);

  // resolve (arbitrator account — in the demo, the contract may accept the deployer as arbitrator)
  // For demo we reuse buyer as "arbitrator" if the example allows; in real setups use a distinct key.
  const buyerShare = 400_000n;
  const sellerShare = 600_000n;
  const resolveTx = Tx.buildCall({
    from: addrBuyer, to: contract, abi: manifest.abi,
    function: "resolve_dispute", args: [orderId, buyerShare, sellerShare],
    gasPrice: 1n, gasLimit: 150_000n, nonce: 3n
  });
  const rcptResolve = await Tx.sendAndAwaitReceipt(rpc, await buyer.signTx(resolveTx, CHAIN_ID));
  console.log("resolve_dispute status:", rcptResolve.status);

  // Decode logs from the resolution
  for (const log of rcptResolve.logs ?? []) {
    const [evtName, evtData] = client.decodeEvent(log);
    console.log("Event:", evtName, evtData);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });

Run:

pnpm tsx escrow_dispute.ts

You should see DisputeOpened then DisputeResolved with the split.

⸻

4) Reading Events

From Receipt (synchronous)

In both SDKs, receipts include logs. The SDK ContractClient decodes logs against the ABI:
	•	Python: name, data = client.decode_event(log)
	•	TypeScript: [name, data] = client.decodeEvent(log)

The decoded data holds named fields exactly as in the ABI.

Via WebSocket (subscribe)

You can subscribe to newHeads and fetch receipts for included txs, or use a contract-specific events stream if your indexer provides one.

TypeScript WS example:

import { WsRpc } from "@animica/sdk";
const ws = new WsRpc({ wsUrl: "ws://127.0.0.1:8546" });
await ws.connect();

const sub = await ws.subscribe("newHeads", []);
for await (const head of sub) {
  console.log("New head:", head.number, head.hash);
  // optionally: query recent txs by block hash, pull receipts, decode events
}


⸻

5) Common Pitfalls
	•	Nonce mismatch: keep separate nonces per account (buyer, seller, arbitrator).
	•	Insufficient balance: ensure the buyer has enough to cover amount + gas.
	•	Gas limits: disputes/resolution can emit multiple events — give some headroom.
	•	Arbitrator role: the demo contract may accept the deployer as arbitrator; in production you’d set/rotate an explicit arbitrator address or a committee.

⸻

6) Extending the Escrow
	•	Time-locks: allow auto-release after T blocks if no dispute is opened.
	•	Multi-sig arbitrator: require M-of-N signatures to resolve.
	•	Partial fills: allow multiple deposits/shipments with per-milestone events.
	•	Evidence blobs: pin documents via DA (blob_pin) and reference commitments in disputes.

⸻

7) Event Reference (example)

Event	Fields	Notes
EscrowOpened	order_id, buyer, seller, amount	Emitted by open
Deposited	order_id, from, amount	Emitted by deposit
Released	order_id, buyer, seller, amount	Emitted by release
Refunded	order_id, buyer, seller, amount	Emitted by refund
DisputeOpened	order_id, who, reason	Emitted by open_dispute
DisputeResolved	order_id, buyer_share, seller_share	Emitted by resolve_dispute

Treat this as illustrative; always defer to the actual manifest.json ABI for field names and types. The SDK will enforce/validate them during encoding/decoding.

⸻

8) Next Steps
	•	Wire this into Studio Web to simulate before deploying.
	•	Add capabilities: attach evidence via DA, or verify a small zk receipt in resolve_dispute.
	•	Build a tiny indexer to surface escrow events in a dashboard.

Happy shipping & safe trades! 🔐
