# RPC Examples (curl / httpx)

Practical snippets for common JSON-RPC calls to an Animica node over HTTP.

> **Base URL**
>
> - Public example: `https://node.animica.org/rpc`
> - Local dev: `http://127.0.0.1:8545`
>
> Export once:
>
> ```bash
> export RPC_URL=${RPC_URL:-http://127.0.0.1:8545}
> ```

---

## Conventions

- JSON-RPC always uses `POST` with `Content-Type: application/json`.
- We’ll use `jq` to pretty-print responses (install from your package manager).
- Request shape:
  ```json
  { "jsonrpc": "2.0", "id": 1, "method": "method.name", "params": [ /* ... */ ] }


⸻

Quick sanity: get chain head

curl

curl -s "$RPC_URL" \
  -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | jq

Python (httpx, sync)

# pip install httpx
import httpx, os
RPC_URL = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
r = httpx.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}, timeout=10.0)
print(r.json())


⸻

Chain parameters & chainId

curl

curl -s "$RPC_URL" -H 'content-type: application/json' --data '{
  "jsonrpc":"2.0","id":1,"method":"chain.getParams","params":[]
}' | jq

curl -s "$RPC_URL" -H 'content-type: application/json' --data '{
  "jsonrpc":"2.0","id":1,"method":"chain.getChainId","params":[]
}' | jq '.result'


⸻

Fetch a block (by number / by hash)

curl

# By height (no tx bodies)
curl -s "$RPC_URL" -H 'content-type: application/json' --data '{
  "jsonrpc":"2.0","id":1,"method":"chain.getBlockByNumber","params":[12345,false,false]
}' | jq

# By hash (include tx bodies & receipts)
BLOCK_HASH="0x0123...abcd"
curl -s "$RPC_URL" -H 'content-type: application/json' --data "{
  \"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"chain.getBlockByHash\",
  \"params\":[\"$BLOCK_HASH\", true, true]
}" | jq

Python

import httpx, os
RPC_URL = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
payload = {"jsonrpc":"2.0","id":1,"method":"chain.getBlockByNumber","params":[12345, False, False]}
print(httpx.post(RPC_URL, json=payload).json())


⸻

Account state (balance & nonce)

curl

ADDR="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqy8u6y"
# Balance (decimal string)
curl -s "$RPC_URL" -H 'content-type: application/json' --data "{
  \"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"state.getBalance\",\"params\":[\"$ADDR\"]
}" | jq '.result'

# Nonce (next sequence)
curl -s "$RPC_URL" -H 'content-type: application/json' --data "{
  \"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"state.getNonce\",\"params\":[\"$ADDR\"]
}" | jq '.result'

Python

import httpx, os
RPC_URL = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
addr = "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqy8u6y"
for m in ("state.getBalance","state.getNonce"):
    print(m, httpx.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":m,"params":[addr]}).json()["result"])


⸻

Submit a signed transaction (raw CBOR hex)

The node expects a signed CBOR-encoded tx payload as hex (0x…).

curl

RAW_TX="0xa1a2a3deadbeef..."  # replace with real signed hex
curl -s "$RPC_URL" -H 'content-type: application/json' --data "{
  \"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tx.sendRawTransaction\",\"params\":[\"$RAW_TX\"]
}" | jq '.result'  # => tx hash

Query tx & receipt

TX="0x7f...aa"
curl -s "$RPC_URL" -H 'content-type: application/json' --data "{
  \"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tx.getTransactionByHash\",\"params\":[\"$TX\"]
}" | jq

curl -s "$RPC_URL" -H 'content-type: application/json' --data "{
  \"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tx.getTransactionReceipt\",\"params\":[\"$TX\"]
}" | jq


⸻

Data Availability (blobs)

Upload a blob (namespace + base64 data)

NAMESPACE=1
# Example data → base64
DATA_B64=$(printf 'hello animica\n' | base64 -w0 2>/dev/null || printf 'aGVsbG8gYW5pbWljYQo=')

curl -s "$RPC_URL" -H 'content-type: application/json' --data "{
  \"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"da.putBlob\",
  \"params\":[ $NAMESPACE, \"$DATA_B64\" ]
}" | jq

Fetch by commitment

COMMITMENT="0xabc123..."
curl -s "$RPC_URL" -H 'content-type: application/json' --data "{
  \"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"da.getBlob\",\"params\":[\"$COMMITMENT\"]
}" | jq -r '.result' | base64 --decode

Get availability proof

curl -s "$RPC_URL" -H 'content-type: application/json' --data "{
  \"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"da.getProof\",\"params\":[\"$COMMITMENT\", 32]
}" | jq

Python (upload)

import httpx, os, base64
RPC_URL = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
data_b64 = base64.b64encode(b"hello animica\n").decode()
resp = httpx.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":"da.putBlob","params":[1, data_b64]})
print(resp.json())


⸻

Randomness Beacon (commit-reveal + VDF)

Get params & current round

curl -s "$RPC_URL" -H 'content-type: application/json' --data '{
  "jsonrpc":"2.0","id":1,"method":"rand.getParams","params":[]
}' | jq

curl -s "$RPC_URL" -H 'content-type: application/json' --data '{
  "jsonrpc":"2.0","id":1,"method":"rand.getRound","params":[]
}' | jq

Commit (demo values)

SALT="0x$(openssl rand -hex 32)"
PAYLOAD="0x$(printf 'my-secret' | sha256sum | cut -d' ' -f1)"

curl -s "$RPC_URL" -H 'content-type: application/json' --data "{
  \"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"rand.commit\",
  \"params\":[\"$SALT\",\"$PAYLOAD\"]
}" | jq

Reveal

curl -s "$RPC_URL" -H 'content-type: application/json' --data "{
  \"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"rand.reveal\",
  \"params\":[\"$SALT\",\"$PAYLOAD\"]
}" | jq

Get beacon output (latest)

curl -s "$RPC_URL" -H 'content-type: application/json' --data '{
  "jsonrpc":"2.0","id":1,"method":"rand.getBeacon","params":[]
}' | jq


⸻

AICF registry (providers & jobs)

List providers (paged)

curl -s "$RPC_URL" -H 'content-type: application/json' --data '{
  "jsonrpc":"2.0","id":1,"method":"aicf.listProviders","params":[0, 50]
}' | jq

Get a provider by id

PID="provider-001"
curl -s "$RPC_URL" -H 'content-type: application/json' --data "{
  \"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"aicf.getProvider\",\"params\":[\"$PID\"]
}" | jq

List jobs (filter by status)

curl -s "$RPC_URL" -H 'content-type: application/json' --data '{
  "jsonrpc":"2.0","id":1,"method":"aicf.listJobs","params":["COMPLETED",0,20]
}' | jq


⸻

Capabilities jobs (deterministic tasks)

TASK="cap-xyz-123"
curl -s "$RPC_URL" -H 'content-type: application/json' --data "{
  \"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"cap.getResult\",\"params\":[\"$TASK\"]
}" | jq


⸻

Batch multiple calls

JSON-RPC batch requests are an array of call objects.

curl -s "$RPC_URL" -H 'content-type: application/json' --data '[
  { "jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[] },
  { "jsonrpc":"2.0","id":2,"method":"chain.getChainId","params":[] }
]' | jq


⸻

Error handling example

If you call a method with invalid params:

curl -s "$RPC_URL" -H 'content-type: application/json' --data '{
  "jsonrpc":"2.0","id":1,"method":"chain.getBlockByNumber","params":[-1]
}' | jq

Typical error shape:

{
  "jsonrpc": "2.0",
  "id": 1,
  "error": { "code": -32602, "message": "invalid params: number must be >= 0" }
}


⸻

Timeouts & retries (curl + httpx)

curl with connect + overall timeout

curl --connect-timeout 3 --max-time 10 -s "$RPC_URL" \
  -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | jq

Python httpx with retries

import httpx, os, time

RPC_URL = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
payload = {"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}

retries = 3
for i in range(retries):
    try:
        r = httpx.post(RPC_URL, json=payload, timeout=httpx.Timeout(5.0, read=10.0))
        r.raise_for_status()
        print(r.json()); break
    except Exception as e:
        if i == retries - 1: raise
        time.sleep(0.5 * (2 ** i))


⸻

Tips
	•	Prefer HTTPS in production; pin certificates when possible.
	•	Treat all result numeric strings as big-ints (do not parse into floats).
	•	For WebSocket subscriptions, see docs/rpc/WEBSOCKETS.md.
	•	The canonical schema lives in docs/rpc/OPENRPC.json.

