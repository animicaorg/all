# Python SDK (`omni_sdk`) — Usage Patterns

Production-minded recipes for building Python apps against the Animica node:
RPC/WS, PQ signing, transactions, contracts, events, DA, AICF, Randomness, and
light-client verification.

- SDK layout: `sdk/python/omni_sdk/*`
- Schemas: `spec/openrpc.json`, `spec/abi.schema.json`
- Test vectors: `sdk/common/test_vectors/*`

---

## 1) Install

```bash
# From PyPI (recommended)
pip install omni-sdk

# Or from source (repo root):
pip install -e ./sdk/python

Requires Python 3.9+. Async WS examples use websockets (installed with the SDK).

⸻

2) Quick RPC client

from omni_sdk.rpc.http import HttpClient

RPC_URL = "https://rpc.animica.dev"
CHAIN_ID = 1

rpc = HttpClient(url=RPC_URL, timeout_s=10)  # retries + backoff built-in
head = rpc.call("chain.getHead", [])
print(head["height"], head["hash"])

Options:
	•	timeout_s: request deadline per call.
	•	extra_headers: dict of headers (auth, tracing).
	•	retry knobs are sane by default for idempotent calls.

⸻

3) Wallets & PQ signers

a) Keystore (AES-GCM file) + mnemonic (PBKDF/HKDF-SHA3)

from pathlib import Path
from omni_sdk.wallet.mnemonic import mnemonic_to_seed
from omni_sdk.wallet.keystore import Keystore
from omni_sdk.wallet.signer import Signer
from omni_sdk.address import bech32_address

mnemonic = "abandon ... (24 words) ..."
seed = mnemonic_to_seed(mnemonic)  # bytes

ks_path = Path("./keystore.json")
ks = Keystore.create(ks_path, password="strong-passphrase")
ks.import_seed(seed)

signer = Signer.from_keystore(ks, alg="dilithium3")   # or "sphincs_shake_128s"
addr = bech32_address(signer.public_key(), hrp="anim")
print("Address:", addr)  # anim1...

PQ primitives are provided by the repo’s pq module; Signer wraps it with domain separation and chainId checks.

b) Ephemeral in-memory signer (tests)

from omni_sdk.wallet.signer import InMemorySigner
signer = InMemorySigner.from_seed(seed, alg="dilithium3")


⸻

4) Build & send a transfer

from omni_sdk.tx.build import build_transfer
from omni_sdk.tx.send import send_tx

sender = bech32_address(signer.public_key(), hrp="anim")
nonce  = rpc.call("state.getNonce", [sender])

tx = build_transfer(
    chain_id=CHAIN_ID,
    from_addr=sender,
    to_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqv3c9xv",
    amount="123450000",     # integer string (minimal units)
    gas_price="1000",
    gas_limit="500000",
    nonce=nonce,
)

signed = signer.sign_tx(tx)  # returns dict with signature & payload
res = send_tx(rpc, signed, await_receipt_ms=30_000)
print(res.tx_hash, res.receipt and res.receipt["status"])

Notes
	•	All sign bytes are canonical & domain-separated (SignBytes in core/encoding).
	•	send_tx throws TxError with structured fields (see §11).

⸻

5) Contracts

a) Direct client with ABI

from omni_sdk.contracts.client import ContractClient
import json

abi = json.load(open("./counter_abi.json"))
counter = ContractClient(rpc=rpc, abi=abi, address="anim1xyz...")

# Read (free):
value = counter.call("get", [])

# Write (paid): build → sign → send
tx = counter.build_tx(
    method="inc",
    args=[1],
    from_addr=sender,
    chain_id=CHAIN_ID,
    gas_price="1000",
    gas_limit="150000",
)
signed = signer.sign_tx(tx)
receipt = send_tx(rpc, signed, await_receipt_ms=20_000).receipt
print("Gas used:", receipt["gasUsed"])

b) Codegen (typed stubs)

python -m omni_sdk.contracts.codegen \
  --abi ./counter_abi.json \
  --out ./generated

from generated.counter import Counter
counter = Counter(rpc=rpc, address="anim1...")
counter.inc(signer, 1)
print(counter.get())

c) Events

from omni_sdk.contracts.events import decode_events
rcpt = rpc.call("tx.getTransactionReceipt", [res.tx_hash])
for evt in decode_events(rcpt["logs"], abi):
    print(evt.name, evt.args)


⸻

6) WebSocket subscriptions (async)

import asyncio
from omni_sdk.rpc.ws import WsClient

WS_URL = RPC_URL.replace("http", "ws") + "/ws"

async def main():
    ws = WsClient(url=WS_URL)
    await ws.connect()

    async def on_head(head):
        print("height", head["height"], "hash", head["hash"])

    sub = await ws.subscribe("newHeads", on_head)
    await asyncio.sleep(10)
    await sub.unsubscribe()
    await ws.close()

asyncio.run(main())


⸻

7) Data Availability (DA)

from omni_sdk.da.client import DAClient

da = DAClient(base_url=RPC_URL)
put = da.put_blob(namespace=24, data=b"hello")
print("commitment", put.commitment)

blob = da.get_blob(put.commitment)
proof = da.get_proof(put.commitment)
ok = da.verify_proof(proof)  # optional light-check against a header


⸻

8) AI/Quantum via AICF

from omni_sdk.aicf.client import AICFClient

aicf = AICFClient(base_url=RPC_URL)
job = aicf.enqueue_ai(
    model="tiny-demo",
    prompt="hello world",
    fee_limit="500000",
    from_addr=sender,
)

res = aicf.get_result(job.task_id)
if res and res.status == "completed":
    print(res.output)


⸻

9) Randomness beacon

from omni_sdk.randomness.client import RandomnessClient
from omni_sdk.utils.hash import sha3_256

rand = RandomnessClient(rpc=rpc)
round_info = rand.get_round()

salt = b"\x00" * 32
payload = sha3_256(b"my-entropy").hex()

rand.commit({"salt": salt, "payload": payload}, signer)   # tx
# ... later when reveal window opens:
rand.reveal({"salt": salt, "payload": payload}, signer)   # tx

beacon = rand.get_beacon()
print("beacon", beacon["output"])


⸻

10) Light client verification (header + DA samples)

from omni_sdk.light_client.verify import verify_light

header = rpc.call("chain.getBlockByNumber", [12345, False])
samples = json.load(open("./fixtures/light_samples.json"))
ok = verify_light({"header": header, "samples": samples})
print("light verify:", ok)


⸻

11) Errors & retries

The SDK raises typed exceptions:
	•	omni_sdk.errors.RpcError — network / JSON-RPC errors
	•	omni_sdk.errors.TxError — mempool/exec issues (e.g., FeeTooLow, NonceGap)
	•	omni_sdk.errors.AbiError — ABI encode/decode mismatch
	•	omni_sdk.errors.VerifyError — proof/validation failures

from omni_sdk.errors import RpcError, TxError

try:
    balance = rpc.call("state.getBalance", [sender], timeout_s=5)
except RpcError as e:
    print("rpc failed", e.code, e.message)
except TxError as e:
    print("tx failed", e.reason, e.details)

	•	HTTP calls honor timeout_s; safe methods auto-retry with backoff.
	•	Mutating calls (e.g., tx.sendRawTransaction) are not blindly retried.

⸻

12) Testing patterns (pytest)

def test_head(monkeypatch):
    from omni_sdk.rpc.http import HttpClient
    def fake_fetch(method, params, **kw):
        assert method == "chain.getHead"
        return {"height": 42, "hash": "0x00"}
    rpc = HttpClient(url="http://test")
    monkeypatch.setattr(rpc, "_call_once", lambda m,p: fake_fetch(m,p))
    head = rpc.call("chain.getHead", [])
    assert head["height"] == 42

	•	Use monkeypatch or responses to mock HTTP.
	•	For WS, test via an in-process websockets.serve echo stub.

⸻

13) Performance tips
	•	Reuse HttpClient (keep-alive).
	•	Cache the Signer (first PQ initialization is the slowest step).
	•	Prefer WS for heads/events over polling.
	•	Batch state.get* reads where possible.

⸻

14) Security notes
	•	Always set/verify chain_id on build/sign paths.
	•	Never print or persist raw seeds; use encrypted Keystore.
	•	Verify ABIs (schema-valid) before constructing calls.
	•	Validate inputs and gas bounds; handle FeeTooLow with a bump policy.

⸻

15) Minimal end-to-end example

from omni_sdk.rpc.http import HttpClient
from omni_sdk.wallet.mnemonic import mnemonic_to_seed
from omni_sdk.wallet.keystore import Keystore
from omni_sdk.wallet.signer import Signer
from omni_sdk.address import bech32_address
from omni_sdk.tx.build import build_transfer
from omni_sdk.tx.send import send_tx

rpc = HttpClient(url="https://rpc.animica.dev")
seed = mnemonic_to_seed("... 24 words ...")
ks = Keystore.create("./ks.json", password="pw"); ks.import_seed(seed)
signer = Signer.from_keystore(ks, alg="dilithium3")
sender = bech32_address(signer.public_key(), hrp="anim")

nonce = rpc.call("state.getNonce", [sender])
tx = build_transfer(
    chain_id=1, from_addr=sender,
    to_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqv3c9xv",
    amount="1000000", gas_price="1200", gas_limit="100000", nonce=nonce)
signed = signer.sign_tx(tx)
result = send_tx(rpc, signed, await_receipt_ms=20_000)
print(result.tx_hash, result.receipt and result.receipt["status"])


⸻

16) Reference
	•	omni_sdk/rpc/http.py — JSON-RPC client
	•	omni_sdk/rpc/ws.py — WS subscribe (async)
	•	omni_sdk/wallet/* — mnemonic/keystore/signer
	•	omni_sdk/tx/* — builders/encode/send
	•	omni_sdk/contracts/* — ABI client, events, codegen
	•	omni_sdk/da/client.py, aicf/client.py, randomness/client.py
	•	omni_sdk/light_client/verify.py

