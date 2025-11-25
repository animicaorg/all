# omni_sdk — Python examples

This folder contains small, **end-to-end** examples showing how to use the Python SDK to
connect to a node, deploy a contract, call functions, subscribe to heads, enqueue AI/Quantum
jobs, and interact with DA and the randomness beacon.

> These examples assume the node’s JSON-RPC and WS endpoints are reachable and that you’re
> on a dev/test network. **Never use test seeds on mainnet.**

---

## Quick setup

### 1) Python & virtualenv

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip

2) Install the SDK (editable)

From the repo root:

python -m pip install -e ./sdk/python

3) Configure environment

Set your RPC URL, chain id, and (optionally) default timeout:

export OMNI_SDK_RPC_URL="http://127.0.0.1:8545"
export OMNI_CHAIN_ID=1           # or your dev/test chain id
export OMNI_SDK_HTTP_TIMEOUT=20  # seconds

For dev/test signing, you can also set a seed hex used by CLI subcommands:

# ⚠️ Dev/test only. Do not reuse outside local testing.
export OMNI_SDK_SEED_HEX="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


⸻

Sanity checks

Print node head (HTTP)

python - <<'PY'
from omni_sdk.rpc.http import RpcClient
import os
c = RpcClient(os.environ["OMNI_SDK_RPC_URL"], timeout=float(os.getenv("OMNI_SDK_HTTP_TIMEOUT", "20")))
print(c.call("chain.getHead", []))
PY

Subscribe to newHeads (WS)

omni-sdk subscribe heads


⸻

Contract: Counter example

The canonical Counter example lives in:
	•	sdk/python/examples/deploy_counter.py — deploys the Counter contract and prints the address.
	•	vm_py/examples/counter/manifest.json — ABI + metadata.
	•	vm_py/examples/counter/contract.py — contract source.

Deploy via CLI

omni-sdk deploy package \
  --manifest vm_py/examples/counter/manifest.json \
  --code vm_py/examples/counter/contract.py \
  --alg dilithium3 \
  --wait

The output includes txHash and the contractAddress on success.

Read / write via CLI

# Read current count
omni-sdk call read \
  --address <contractAddress> \
  --abi vm_py/examples/counter/manifest.json \
  --func get

# Increment (signed tx)
omni-sdk call write \
  --address <contractAddress> \
  --abi vm_py/examples/counter/manifest.json \
  --func inc \
  --alg dilithium3 \
  --wait

Deploy via Python

python sdk/python/examples/deploy_counter.py


⸻

Events & subscriptions

Print decoded events for the Counter at <addr>:

omni-sdk subscribe events \
  --address <addr> \
  --abi vm_py/examples/counter/manifest.json

(Use --event <Name> to filter by event name.)

⸻

Data Availability (DA)

Minimal DA round-trip using the SDK:

python - <<'PY'
from omni_sdk.rpc.http import RpcClient
from omni_sdk.da.client import DAClient
import os
rpc = RpcClient(os.environ["OMNI_SDK_RPC_URL"])
dac = DAClient(rpc)
commitment, receipt = dac.post_blob(b"hello da", namespace=24)
print("commitment:", commitment)
out = dac.get_blob(commitment)
print("roundtrip ok:", out == b"hello da")
PY


⸻

Randomness beacon

Fetch latest beacon:

python - <<'PY'
from omni_sdk.rpc.http import RpcClient
from omni_sdk.randomness.client import RandomClient
import os
rpc = RpcClient(os.environ["OMNI_SDK_RPC_URL"])
rc = RandomClient(rpc)
print(rc.get_beacon())
PY


⸻

AICF (AI / Quantum) examples

End-to-end demos:
	•	sdk/python/examples/ai_enqueue_then_consume.py
	•	sdk/python/examples/quantum_enqueue_then_consume.py

Run one (requires a running AICF-enabled node/devnet):

python sdk/python/examples/ai_enqueue_then_consume.py


⸻

Troubleshooting
	•	Connection refused / timeouts: check OMNI_SDK_RPC_URL and that your node is running.
	•	ChainId mismatch: set OMNI_CHAIN_ID to match the node’s chain.
	•	WS not available: ensure your node exposes a WebSocket endpoint (e.g., ws://.../ws).
	•	Signature errors: verify --alg and that your dev seed is correct hex.

⸻

License

These examples are provided under the repository’s SDK license. See sdk/LICENSE.
