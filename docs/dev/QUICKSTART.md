# Dev Quickstart — run devnet, mine a block, deploy *Counter* (v1)

This guide gets you from **zero → a local devnet** with:
1) a node booted from genesis,  
2) a CPU miner sealing blocks, and  
3) a deployed **Counter** contract you can call.

> Target audience: developers running everything locally on macOS/Linux/WSL2.  
> You’ll use Python modules in this repo (no Docker required).

---

## 0) Prereqs

- Python 3.11+ (3.12 OK)
- `pip`, `venv`, and a recent `clang`/`gcc`
- `curl` + `jq` (for quick JSON-RPC checks)
- (Optional) `uvicorn` for running the RPC app
- (Optional) RocksDB libs — not required (SQLite is default)

```bash
# From repo root
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install --upgrade pip wheel
# All modules live side-by-side; add repo root to PYTHONPATH for -m usage:
export PYTHONPATH="$PWD"


⸻

1) Initialize a fresh devnet DB

Genesis & loader live in core/. Use the built-in CLI to create an SQLite DB
and compute the genesis header/state roots.

python -m core.boot \
  --genesis core/genesis/genesis.json \
  --db sqlite:///./animica_devnet.db

You can sanity-print chain params and current head:

python -m core.cli_demo --db sqlite:///./animica_devnet.db

Chain IDs (from spec): mainnet 1, testnet 2, devnet 1337.

⸻

2) Start the JSON-RPC node

The RPC app (FastAPI) wires into core/, mempool/, etc.

Option A — module entry (simple)

# Default: host 127.0.0.1, port 8545, db ./animica_devnet.db, chainId=1337
python -m rpc.server

Option B — uvicorn (explicit)

pip install uvicorn[standard]
uvicorn rpc.server:app --host 127.0.0.1 --port 8545 --log-level info

Verify it’s alive:

curl -s http://127.0.0.1:8545/healthz
curl -s http://127.0.0.1:8545/rpc \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getParams","params":[]}' | jq
curl -s http://127.0.0.1:8545/rpc \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"chain.getHead","params":[]}' | jq

If you used a non-default DB path earlier, pass it via RPC_DB_PATH or your config (see rpc/config.py).

⸻

3) Start a local CPU miner (HashShare + useful proof hooks)

The built-in miner lives in mining/ and talks to your RPC.

# One terminal (keep running):
python -m mining.cli.miner \
  --threads 2 \
  --device cpu \
  --rpc http://127.0.0.1:8545

You should soon see found shares and occasional block submissions.

Check the head advancing:

watch -n 2 \
'curl -s http://127.0.0.1:8545/rpc -H content-type:application/json \
 -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"chain.getHead\",\"params\":[]}" | jq .result.height'


⸻

4) Deploy the Counter contract (Python SDK)

We’ll use the Python SDK example that deploys and then calls inc/get.

# Install Python SDK in editable mode
pip install -e sdk/python

# (Optional) ensure deps for msgspec, httpx, websockets are present
pip install msgspec httpx websockets

# Run the example against your local RPC (chainId=1337 devnet)
python sdk/python/examples/deploy_counter.py \
  --rpc http://127.0.0.1:8545 \
  --chain-id 1337

Expected output (abridged):

Deploying Counter...
txHash: 0x...
Waiting for receipt...
Deployed at address: anim1...
Calling get → 0
Calling inc...
Calling get → 1

Where do funds come from? The devnet genesis (execution/fixtures/genesis_state.json) pre-funds test accounts used by examples. The miner also accrues block rewards locally.

⸻

5) Inspect via JSON-RPC

Lookup the deployment transaction and receipt:

# Replace 0x... with your tx hash
TX=0x0123abcd...
curl -s http://127.0.0.1:8545/rpc -H content-type:application/json \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tx.getTransactionByHash\",\"params\":[\"$TX\"]}" | jq

curl -s http://127.0.0.1:8545/rpc -H content-type:application/json \
  -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tx.getTransactionReceipt\",\"params\":[\"$TX\"]}" | jq


⸻

6) (Optional) WebSocket subscriptions

Subscribe to new heads:

# Quick demo using wscat
npm -g i wscat
wscat -c ws://127.0.0.1:8545/ws
# then send:
# {"jsonrpc":"2.0","id":1,"method":"subscribe","params":["newHeads"]}


⸻

7) Troubleshooting
	•	RPC won’t start: ensure PYTHONPATH="$PWD" so rpc, core, etc. import correctly.
	•	No blocks: miner not connected or share difficulty too high. Re-start miner; confirm RPC URL.
	•	RocksDB missing: it’s optional; SQLite is default.
	•	Port in use: change with --port (uvicorn) or env in rpc/config.py.
	•	Proof backends: all useful-work verifiers are gracefully optional in devnet.

⸻

8) Next steps
	•	Try the Studio Web flow to compile & simulate contracts (see studio-wasm/, studio-web/).
	•	Explore DA blobs (da/cli/put_blob.py) and the randomness beacon (randomness/cli/*).
	•	Run unit tests: pytest within each module (e.g., execution/tests/, consensus/tests/).

⸻

That’s it! You have a local chain producing blocks and a contract deployed & callable.
