# Explorer API (Read-Only)
_Thin, cached, read-only HTTP/WS layer used by the Explorer UI. Designed for **fast queries and pagination**, not for consensus-critical logic._

- **Base URL (examples):**
  - Local dev: `http://127.0.0.1:8788`
  - Prod: `https://explorer.api.animica.org`
- **Versioning:** `v1` prefix when stability matters (e.g., `/v1/blocks`). Unversioned routes are best-effort stable.
- **Auth:** none by default (may be fronted by a CDN/WAF with rate-limits).
- **Content-Type:** JSON (`application/json`).
- **Errors:** RFC7807 `application/problem+json`.

## Pagination & Caching
- **Cursor pagination**: responses may include `next_cursor`. Pass `?cursor=<token>` to fetch the next page.
- **Limit**: `?limit=N` (default 20, max 100).
- **Caching**: `Cache-Control` and `ETag` provided on most collection endpoints. Prefer `If-None-Match`.

---

## Health & Meta
### `GET /healthz` • `GET /readyz` • `GET /version`
Basic liveness/readiness and build/version info.

```bash
curl -s $EXPLORER_API/healthz
curl -s $EXPLORER_API/version | jq

GET /chain

Chain metadata summary for the active network (id, name, genesis hash, RPC URLs, explorer config).

{
  "chainId": 1,
  "name": "Animica Mainnet",
  "genesisHash": "0xabc...",
  "rpc": ["https://rpc.animica.org"],
  "explorerVersion": "v1.3.0"
}


⸻

Heads & Blocks

GET /head

Current head summary (height, hash, time, tx count, Σψ/Θ snapshot if available).

{ "height": 123456, "hash": "0x..", "time": "2025-01-01T12:34:56Z", "txs": 42 }

GET /blocks?limit=20&cursor=...

List recent blocks (newest first). Item fields are sized for listings.

{
  "items": [
    { "height":123456, "hash":"0x..", "time":"2025-01-01T12:34:56Z", "txs":42, "miner":"anim1..." }
  ],
  "next_cursor":"eyJoZWlnaHQiOjEyMzQ1NX0="
}

GET /block/{height}  or  GET /block/by-hash/{hash}

Full block view. Optional toggles:
	•	?include=txs (default: true)
	•	?include=receipts (default: false)
	•	?include=proofs (default: false)

{
  "height":123456,
  "hash":"0x..",
  "parent":"0x..",
  "time":"2025-01-01T12:34:56Z",
  "miner":"anim1..",
  "gasUsed":"1234567",
  "roots": { "state":"0x..","txs":"0x..","receipts":"0x.." },
  "txs": [{ "hash":"0x..","from":"anim1..","to":"anim1..","value":"0","status":"SUCCESS" }]
}


⸻

Transactions

GET /tx/{hash}

Transaction + receipt if available.

{
  "hash":"0x..",
  "block": { "height":123456, "hash":"0x.." },
  "from":"anim1..","to":"anim1..",
  "value":"0","gasUsed":"21000",
  "status":"SUCCESS","logs":[]
}

GET /txs?address=anim1...&role=any|from|to&limit=20&cursor=...

List transactions involving an address.

GET /pending-txs?limit=50&cursor=...

Recent pending/mempool transactions observed by the indexer (best effort).

⸻

Accounts & Contracts

GET /address/{address}

Account/contract summary (balance, nonce, code presence, verification link).

{
  "address":"anim1...",
  "balance":"1234500000000000000",
  "nonce": 7,
  "isContract": true,
  "verified": true,
  "codeHash":"0x..",
  "artifactId":"art_01HF.."
}

GET /address/{address}/txs?limit=20&cursor=...

Alias of /txs?address=....

GET /contract/{address}/abi

If verified, returns ABI (or 404).

{ "abi":[ /* functions/events */ ], "sourceUrl":"https://..." }


⸻

Logs & Events

GET /logs?address=anim1...&topic0=0x...&from=heightA&to=heightB&limit=100&cursor=...

Bloom-aided, indexed filter over receipts/logs. topic1..topic3 supported.

{
  "items":[
    {
      "block":123456,"tx":"0x..","idx":0,
      "address":"anim1..",
      "topics":["0xdead..","0xbeef.."],
      "data":"0x..."
    }
  ],
  "next_cursor":"..."
}


⸻

Search

GET /search?q=<string>

Heuristic search over block hash, tx hash, address, or height.

{
  "matches":[
    { "kind":"tx","hash":"0x.."},
    { "kind":"address","address":"anim1.."}
  ]
}


⸻

Stats & Diagnostics

GET /stats/overview?window=24h|7d|30d

High-level network stats for charts.

{
  "tpsAvg": 12.4,
  "gasAvg": "3500000",
  "blocks": 5400,
  "txs": 67000,
  "feesBurned":"123.45"
}

GET /stats/series?metric=tps|gas|txs&window=24h&step=5m

Time-series points for charting.

GET /peers (optional)

If the indexer tracks P2P: peer counts and RTT ranges.

⸻

WebSocket (optional; proxied or native)

GET /ws → JSON messages with a type field.

Subscribe by sending a message after connect:

{ "op":"subscribe", "topics":["newHeads","pendingTxs","logs"], "filters":{"logs":{"address":"anim1.."}} }

Message shapes

{ "type":"newHead", "height":123457, "hash":"0x..", "time":"..." }
{ "type":"pendingTx", "hash":"0x..", "from":"anim1..","to":"anim1.." }
{ "type":"log", "block":123456,"tx":"0x..","address":"anim1..","topics":["0x.."],"data":"0x.." }


⸻

Examples

Fetch latest 50 blocks

curl -s "$EXPLORER_API/blocks?limit=50" | jq '.items[].height'

Block by height with receipts

curl -s "$EXPLORER_API/block/123456?include=receipts=true" | jq

Recent logs for a contract + topic

ADDR=anim1qqqq...
TOPIC=0xdeadbeef...
curl -s "$EXPLORER_API/logs?address=$ADDR&topic0=$TOPIC&from=120000&to=123999&limit=100" | jq '.items|length'

Search any string

curl -s "$EXPLORER_API/search?q=anim1qq..." | jq


⸻

Notes & Limits
	•	Best-effort indexing: Explorer reflects the canonical chain view observed by the indexer. During reorgs, data may shift.
	•	Do not treat this API as consensus—use node RPC for critical workflows.
	•	Rate-limits and aggregate caching are enabled; prefer cursor pagination for large scans.
	•	Contract verification artifacts are sourced from Studio Services when available.

⸻

