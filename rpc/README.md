# Animica RPC

FastAPI-based JSON-RPC/WS server that exposes chain/state/tx/miner/DA endpoints.
Backed by `core/` (DB, types, genesis), `pq/` (post-quantum sigs), `mempool/`, `da/`, and the event hub.

## Goals

- **Public node API** for wallets, explorers, SDKs (Python/TS/Rust), and the Studio web IDE.
- **Deterministic wire**: canonical JSON-RPC 2.0 over HTTP; JSON-RPC over WebSocket for subscriptions.
- **Security first**: strict CORS allowlist, per-method rate limits, request size caps, PQ signature prechecks.
- **Observability**: Prometheus `/metrics`, structured logs, `/healthz` & `/readyz`.
- **Spec-driven**: OpenRPC served at `/openrpc.json` (see `spec/openrpc.json`).

## Features (v0)

- Chain metadata: `chain.getParams`, `chain.getChainId`, `chain.getHead`
- Blocks & receipts: `chain.getBlockByNumber`, `chain.getBlockByHash`, `tx.getTransactionReceipt`
- Transactions: `tx.sendRawTransaction`, `tx.getTransactionByHash`
- State reads: `state.getBalance`, `state.getNonce`
- Mempool stream: WS subscription `pendingTxs`
- Head stream: WS subscription `newHeads`
- DA helpers mounted by `da/adapters/rpc_mount.py` (if `da/` is enabled)
- Miner bridge (optional): WS getwork (mounted by `mining/bridge_rpc.py`)

## Requirements

- Python 3.11+
- Repo root deps installed (FastAPI, Uvicorn, Prometheus client):
  ```bash
  cd ~/animica
  python -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt

	•	Initialized database (run once):

python -m core.boot --genesis core/genesis/genesis.json --db sqlite:///animica.db



Run

Dev

# HTTP :8547, WS :8547/ws
python -m rpc.server \
  --db sqlite:///animica.db \
  --host 0.0.0.0 --port 8547 \
  --cors-allow http://localhost:5173 \
  --rate.default.rps 20 --rate.burst 40 \
  --log info

Or with uvicorn:

uvicorn rpc.server:app_factory --factory --host 0.0.0.0 --port 8547

Health & metrics

curl -s http://localhost:8547/healthz
curl -s http://localhost:8547/readyz
curl -s http://localhost:8547/metrics | head

OpenRPC (machine-readable)

curl -s http://localhost:8547/openrpc.json | jq .

JSON-RPC HTTP examples

All requests are POST /rpc with Content-Type: application/json.

1) Get chain params

curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"chain.getParams","params":[]
}' | jq .

2) Get head (hash, height, time)

curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":2,"method":"chain.getHead","params":[]
}' | jq .

3) Get block by number (include txs/receipts)

curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":3,"method":"chain.getBlockByNumber",
  "params":[42, {"includeTxs": true, "includeReceipts": false}]
}' | jq .

4) Submit a signed transaction

tx.sendRawTransaction expects the CBOR-encoded transaction envelope as a hex string (0x-prefixed).
(Use the SDK or wallet to build/sign.)

RAW=$(python - <<'PY'
# tiny helper: produce a fake 0x hex string for illustration only
print("0x" + "a1b2"*64)
PY
)
curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d "{
  \"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tx.sendRawTransaction\",\"params\":[\"$RAW\"]
}" | jq .

Response (example):

{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "txHash": "0x7c3f…",
    "accepted": true,
    "reason": null
  }
}

5) Lookup tx & receipt

# by hash
curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":5,"method":"tx.getTransactionByHash",
  "params":["0x7c3f..."]
}' | jq .

# receipt (may be null if still pending)
curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":6,"method":"tx.getTransactionReceipt",
  "params":["0x7c3f..."]
}' | jq .

6) State reads

ADDR="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqy4mlg"  # bech32m (example)
curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d "{
  \"jsonrpc\":\"2.0\",\"id\":7,\"method\":\"state.getBalance\",
  \"params\":[\"$ADDR\", {\"block\":\"latest\"}]
}" | jq .

curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d "{
  \"jsonrpc\":\"2.0\",\"id\":8,\"method\":\"state.getNonce\",
  \"params\":[\"$ADDR\", {\"block\":\"latest\"}]
}" | jq .

WebSocket subscriptions

Server endpoint: ws://localhost:8547/ws (JSON-RPC over WS).
Use wscat or websocat:

# install one of them if needed:
# npm i -g wscat
# cargo install websocat

wscat -c ws://localhost:8547/ws

Subscribe to newHeads

Send:

{ "jsonrpc":"2.0", "id": 1, "method":"subscribe", "params":["newHeads"] }

Receive stream:

{
  "jsonrpc":"2.0",
  "method":"subscription",
  "params":{
    "subscription":"sub-1",
    "result":{
      "height": 12345,
      "hash": "0xabc…",
      "time": 1728501123,
      "gasUsed": "0x1e240",
      "poies": { "theta": "0.000123", "psiSum": "0.000131", "mix": { "hash": 0.72, "ai": 0.20, "quantum": 0.06, "storage": 0.02 } }
    }
  }
}

Subscribe to pendingTxs

{ "jsonrpc":"2.0", "id": 2, "method":"subscribe", "params":["pendingTxs"] }

Each event:

{
  "jsonrpc":"2.0",
  "method":"subscription",
  "params":{
    "subscription":"sub-2",
    "result":{
      "txHash":"0x7c3f…",
      "from":"anim1…",
      "to":"anim1…",
      "gasPrice":"0x3b9aca00",
      "tip":"0x00",
      "size": 192
    }
  }
}

Unsubscribe:

{ "jsonrpc":"2.0", "id": 3, "method":"unsubscribe", "params":["sub-2"] }

CORS & Rate limits
	•	CORS: configure allowed origins via --cors-allow (repeatable) or env RPC_CORS_ALLOW.
	•	Rate limits: token-bucket per IP & per method. Defaults (dev): 20 rps / 40 burst.
Tune with flags --rate.default.rps, --rate.default.burst, or per-method in rpc/config.py.

Error model

Structured JSON-RPC errors with codes:
	•	-32600 Invalid Request
	•	-32601 Method not found
	•	-32602 Invalid params
	•	-32000 Server error (generic)
	•	App-specific (examples):
	•	-32010 InvalidTx
	•	-32011 ChainIdMismatch
	•	-32012 FeeTooLow
	•	-32013 RateLimited

Example:

{
  "jsonrpc":"2.0",
  "id":4,
  "error": { "code": -32010, "message": "InvalidTx: bad signature", "data": {"hint":"check PQ alg_id/domain"} }
}

Environment variables (optional)
	•	RPC_DB_URI (default sqlite:///animica.db)
	•	RPC_HOST (default 127.0.0.1)
	•	RPC_PORT (default 8547)
	•	RPC_CHAIN_ID (validated vs DB/genesis)
	•	RPC_LOG_LEVEL (DEBUG|INFO|WARN|ERROR)
	•	RPC_CORS_ALLOW (comma-sep origins)
	•	RPC_RATE_DEFAULT_RPS, RPC_RATE_DEFAULT_BURST

Test

pytest -q rpc/tests

Security notes
	•	No server-side private keys. All transactions must be fully signed client-side (PQ).
	•	Size caps & schema validation on every method; CBOR decoding is bounded.
	•	PQ: address & signature verification follow spec/pq_policy.yaml; bech32m for addresses.

Useful links
	•	Spec (OpenRPC): spec/openrpc.json
	•	Types & CBOR schemas: spec/tx_format.cddl, spec/header_format.cddl
	•	SDK Quickstarts: sdk/README.md
	•	Wallet extension provider: wallet-extension/src/provider

⸻

