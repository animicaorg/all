# Animica Omni SDK (Python)

High-level client for Animica nodes:
- JSON-RPC + WebSocket subscriptions
- Wallet utilities (mnemonics, keystore, PQ signers)
- Tx builders (transfer/call/deploy), send & await receipt
- Contract clients & event decoding
- AICF (AI/Quantum) enqueue/read helpers
- Randomness beacon (commit/reveal/getBeacon)
- DA (Data Availability) convenience
- Light-client verification helpers

> Tested on Python 3.9+.

---

## Install

```bash
pip install omni-sdk
# Optional CLI extras
pip install "omni-sdk[cli]"


⸻

Quickstart: connect & read head

from omni_sdk.rpc.http import RpcClient

rpc = RpcClient("http://localhost:8545")
head = rpc.call("chain.getHead", {})
print(head["height"], head["hash"])

WebSocket stream of new heads:

import asyncio
from omni_sdk.rpc.ws import WsClient

async def main():
    async with WsClient("ws://localhost:8546") as ws:
        async for evt in ws.subscribe("newHeads"):
            print("new head", evt["number"], evt["hash"])

asyncio.run(main())


⸻

Wallet: mnemonic, keystore, address

from omni_sdk.wallet.mnemonic import create_mnemonic, mnemonic_to_seed
from omni_sdk.wallet.keystore import Keystore
from omni_sdk.wallet.signer import Dilithium3Signer  # or SphincsShake128s
from omni_sdk.address import bech32_encode, bech32_decode

mnemonic = create_mnemonic(strength=128)   # 12 words
seed = mnemonic_to_seed(mnemonic)

signer = Dilithium3Signer.from_seed(seed, account=0)  # deterministic subkey
pub = signer.public_key_bytes()
addr = bech32_encode("anim", pub)  # bech32m anim1...

# Persist encrypted
ks = Keystore(path=".keystore")
ks.save("default", signer.export_private_bytes(), password="changeit")
# Later:
priv = ks.load("default", password="changeit")
signer2 = Dilithium3Signer.from_private_bytes(priv)
assert signer2.public_key_bytes() == pub


⸻

Send a transfer

from omni_sdk.tx.build import build_transfer
from omni_sdk.tx.encode import build_sign_bytes
from omni_sdk.tx.send import send_and_wait
from omni_sdk.utils.bytes import hex_to_bytes
from omni_sdk.config import Config
from omni_sdk.errors import TxError

rpc_url = "http://localhost:8545"
cfg = Config(rpc_url=rpc_url, chain_id=1337)

# 1) Build tx
tx = build_transfer(
    from_addr=addr,
    to_addr="anim1qrl9u...",  # recipient
    amount=12345,             # in minimal units
    nonce=0,                  # fetch via rpc if unknown
    gas_limit=100_000,
    gas_price=1,
    memo=b"",
)

# 2) Domain-separated sign bytes
sign_bytes = build_sign_bytes(tx, chain_id=cfg.chain_id)

# 3) Sign with PQ signer
sig = signer.sign(sign_bytes)

# 4) Broadcast & wait for receipt
receipt = send_and_wait(rpc_url, tx, sig, timeout_s=30)
print("status:", receipt["status"], "gas_used:", receipt["gasUsed"])

Tip: send_and_wait polls or uses WS if available; inspect receipt["logs"] for events.

⸻

Call a contract (read-only) & send a method (write)

from omni_sdk.contracts.client import ContractClient
from omni_sdk.contracts.events import EventDecoder
from omni_sdk.utils.bytes import to_hex

abi = {
  "name": "Counter",
  "methods": [
    {"name": "get", "inputs": [], "outputs": [{"type":"uint64"}], "mutability":"view"},
    {"name": "inc", "inputs": [{"name":"delta","type":"uint64"}], "outputs": []}
  ],
  "events": [{"name":"Incremented","fields":[{"name":"by","type":"uint64"}]}]
}

client = ContractClient(rpc_url, address="anim1xyz...", abi=abi)

# Read
(value,) = client.call_view("get")
print("counter =", value)

# Write
tx = client.build_tx("inc", [5], from_addr=addr, gas_limit=120_000, gas_price=1, nonce=1)
sig = signer.sign(build_sign_bytes(tx, chain_id=1337))
rcpt = client.send_and_wait(tx, sig)
print("ok, gas:", rcpt["gasUsed"])

# Decode events
decoder = EventDecoder(abi)
for log in rcpt["logs"]:
    evt = decoder.try_decode(log)
    if evt and evt.name == "Incremented":
        print("incremented by", evt.args["by"])


⸻

Deploy a package (manifest + code)

from omni_sdk.contracts.deployer import deploy_package
from omni_sdk.utils.bytes import hex_to_bytes

with open("counter.manifest.json","rb") as f: manifest = f.read()
with open("counter.code.hex","r") as f: code = hex_to_bytes(f.read().strip())

tx = deploy_package(manifest, code, from_addr=addr, gas_limit=2_000_000, gas_price=1, nonce=0)
sig = signer.sign(build_sign_bytes(tx, chain_id=1337))
rcpt = send_and_wait(rpc_url, tx, sig)
print("deployed at", rcpt["contractAddress"])


⸻

AICF: enqueue AI / Quantum job

from omni_sdk.aicf.client import AICFClient

aicf = AICFClient(rpc_url)
task = aicf.enqueue_ai(
    caller=addr,
    model="text-mini",
    prompt="Summarize Animica VM determinism in 2 lines.",
    max_tokens=128,
)
print("task_id:", task["task_id"])

# Next block (or via resolver), read result:
res = aicf.read_result(task["task_id"])
print(res["status"], res.get("output"))

Quantum example is similar: enqueue_quantum(circuit, shots, traps=...).

⸻

Randomness beacon: commit / reveal / get

from omni_sdk.randomness.client import RandomnessClient

rand = RandomnessClient(rpc_url)
round_info = rand.get_round()
salt = b"\x00"*32
payload = b"any-bound-input"
commit_tx = rand.commit(addr, salt, payload, gas_limit=80_000, gas_price=1, nonce=0)
sig = signer.sign(build_sign_bytes(commit_tx, chain_id=1337))
rand.send(commit_tx, sig)

# ... after window opens:
reveal_tx = rand.reveal(addr, salt, payload, gas_limit=80_000, gas_price=1, nonce=1)
sig2 = signer.sign(build_sign_bytes(reveal_tx, chain_id=1337))
rand.send(reveal_tx, sig2)

beacon = rand.get_beacon()
print("beacon:", beacon["output"])


⸻

Data Availability convenience

from omni_sdk.da.client import DAClient

da = DAClient(rpc_url)
cid = da.post(namespace=b"pricefeeds", data=b"...large bytes...")
print("commitment:", cid)

blob = da.get(cid)
proof_ok = da.verify(cid, blob)


⸻

Light client helpers

from omni_sdk.light_client.verify import verify_light_proof

ok = verify_light_proof(
    header=/* dict or dataclass */,
    light_proof=/* compact proof from RPC */,
)
print("verified:", ok)


⸻

Codegen: generate contract stubs from ABI

from omni_sdk.contracts.codegen import generate_client
import json, pathlib

abi = json.load(open("counter_abi.json"))
out_dir = pathlib.Path("gen")
generate_client(abi, out_dir)  # emits gen/counter_client.py

The generated client exposes strongly-typed methods and event decoders.

⸻

CLI (optional)

# After installing omni-sdk[cli]
omni-sdk --help
omni-sdk call --rpc http://localhost:8545 --to anim1xyz... --method get


⸻

Errors

from omni_sdk.errors import RpcError, TxError, AbiError, VerifyError
try:
    ...
except (RpcError, TxError) as e:
    print("RPC/Tx failed:", e)


⸻

Examples & tests
	•	sdk/python/examples/* — runnable demos (deploy counter, AICF, randomness).
	•	sdk/python/tests/* — RPC round-trip, wallet/sign, events decode.

⸻

Notes
	•	PQ signatures: Dilithium3 (default) and SPHINCS+ are supported; both apply strict domain separation over SignBytes(chainId, tx).
	•	Determinism: CBOR encoding matches node rules; avoid third-party CBOR libs for tx/signing unless you validate canonical output.
	•	Chain IDs: Always sign with the intended chain_id; mismatches are rejected by nodes.

