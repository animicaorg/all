# WebSockets — Subscriptions (`/ws`)

Real-time updates are delivered over a **WebSocket** endpoint that speaks JSON-RPC 2.0 messages.
This doc covers the subscription lifecycle, message shapes, heartbeats, reconnection, and the two
core topics:

- `newHeads` — push the canonical head as the chain advances
- `pendingTxs` — push hashes (and light metadata) of transactions admitted to the pending pool

> The HTTP JSON-RPC reference is in `docs/rpc/JSONRPC.md`. This file focuses on **push**.

---

## 1) Endpoint & Transport

- **URL:** `wss://<host>/ws` (or `ws://localhost:PORT/ws` for local)
- **Framing:** text frames, each containing a single JSON value
- **Protocol:** JSON-RPC 2.0 (`"jsonrpc": "2.0"`)
- **Compression:** permessage-deflate may be enabled
- **Auth:** if enabled by ops, standard headers (e.g. `Authorization: Bearer …`) are validated

Server sends:
- **Push notifications** as JSON-RPC **requests** (`method` + `params`, **no** `id`)
- **Acks** to your subscribe/unsubscribe commands as JSON-RPC **responses** (`result|error`, **with** `id`)

---

## 2) Subscription API

### 2.1 Subscribe

Send a JSON-RPC request:

```jsonc
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "subscribe",
  "params": {
    "topic": "newHeads",
    "filter": {}                 // topic-specific; optional
  }
}

Success response:

{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "subscriptionId": "sub_6yQn3QFQJ6",
    "topic": "newHeads"
  }
}

2.2 Push message shape

Every push uses the topic name as method and carries the data plus the subscriptionId:

{
  "jsonrpc": "2.0",
  "method": "newHeads",
  "params": {
    "subscriptionId": "sub_6yQn3QFQJ6",
    "data": { "height": 12346, "hash": "0x…", "time": "2025-01-05T12:35:08Z" }
  }
}

2.3 Unsubscribe

{ "jsonrpc":"2.0", "id": 2, "method":"unsubscribe", "params": { "subscriptionId":"sub_6yQn3QFQJ6" } }


⸻

3) Topics

3.1 newHeads

Emitted when the canonical head advances (finality semantics depend on network policy).

Data shape

type Head = {
  height: number;     // block number
  hash: string;       // 0x-prefixed block hash
  time: string;       // RFC 3339 / ISO 8601
};

Example push

{
  "jsonrpc": "2.0",
  "method": "newHeads",
  "params": {
    "subscriptionId": "sub_sQ3f…",
    "data": { "height": 421337, "hash": "0x9b…4a", "time": "2025-01-05T13:00:01Z" }
  }
}

Notes
	•	Reorgs: if a reorg occurs, subsequent newHeads will reflect the new canonical path.
Clients should compare the parent hash of locally cached heads (via HTTP chain.getBlockByHash)
if they track continuity.
	•	Rate: typically one event per sealed block.

⸻

3.2 pendingTxs

Emitted when a transaction passes stateless validation + policy checks and is admitted
to the pending mempool (before inclusion in a block).

Filter options (optional)

type PendingFilter = {
  from?: string;          // bech32m anim1… address
  to?: string;            // bech32m anim1…
  minTip?: string;        // decimal string, filter by min tip/priority
  includeMeta?: boolean;  // default: false — include small metadata
};

Data shape

type PendingTx = {
  hash: string;                 // 0x…
  // present only if includeMeta = true:
  from?: string;
  to?: string | null;
  nonce?: number;
  gas?: string;                 // decimal string
};

Subscribe with filter

{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "subscribe",
  "params": {
    "topic": "pendingTxs",
    "filter": { "includeMeta": true, "minTip": "1" }
  }
}

Example push

{
  "jsonrpc": "2.0",
  "method": "pendingTxs",
  "params": {
    "subscriptionId": "sub_pend…",
    "data": {
      "hash": "0x7f…aa",
      "from": "anim1qz…x4h",
      "to": "anim1pr…0jg",
      "nonce": 42,
      "gas": "21000"
    }
  }
}

Notes
	•	Not all pending txs will be mined; some may expire or be replaced (RBF).
	•	For status transitions, query HTTP tx.getTransactionReceipt or re-subscribe after reconnect.

⸻

4) Heartbeats, Liveness & Backpressure

4.1 Ping/Pong
	•	The server sends WebSocket ping frames at a fixed interval (e.g., 25–30s). Most clients reply automatically.
	•	Optionally, the server may send a JSON heartbeat if no traffic:

{ "jsonrpc":"2.0", "method":"heartbeat", "params": { "ts": "2025-01-05T13:00:30Z" } }



4.2 Backpressure
	•	Each subscription has a bounded queue (configurable). If the client cannot keep up,
the server may drop oldest events and send an overflow notice:

{ "jsonrpc":"2.0", "method":"overflow", "params": { "subscriptionId":"sub_…", "dropped": 128 } }


	•	Clients should treat an overflow as a gap and reconcile via HTTP:
	•	newHeads: call chain.getHead and walk back if necessary
	•	pendingTxs: refresh state via mempool/cli or rely on mined receipts

4.3 Rate Limits
	•	Subscriptions per connection and emitted messages/second are rate-limited.
	•	Violations yield a JSON-RPC error with code -32001 (RateLimited) or an immediate close.

⸻

5) Reconnection & Resubscribe Strategy
	1.	On socket close, retry with exponential backoff (jittered).
	2.	After reconnect:
	•	Call chain.getHead over HTTP to catch up (newHeads).
	•	Re-issue your subscribe calls.
	•	For pendingTxs, reconcile only if your app depends on mempool continuity; otherwise, rely on receipts.

Tip: keep your desired subscriptions in app state so you can re-send them after reconnect.

⸻

6) Errors

Subscribe response errors
	•	-32601 MethodNotFound — if WS pubsub is disabled
	•	-32602 InvalidParams — bad topic or filter
	•	-32001 RateLimited — too many subscriptions / messages
	•	-32017 NotFound — unknown subscriptionId on unsubscribe

Push-time errors
	•	The server does not send push errors for user code; it may send overflow notices (see 4.2).

⸻

7) Security Notes
	•	The WS server validates the Origin header; only allowed origins can connect (configurable).
	•	Prefer WSS in production; HTTP upgrade behind a TLS-terminating proxy is supported.
	•	No secrets are pushed; never send private keys or signed tx preimages over WS.

⸻

8) Client Examples

8.1 Browser (TypeScript)

const ws = new WebSocket(`${location.origin.replace(/^http/,'ws')}/ws`);

ws.addEventListener('open', () => {
  ws.send(JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'subscribe', params: { topic: 'newHeads' }}));
  ws.send(JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'subscribe', params: { topic: 'pendingTxs', filter: { includeMeta: true } }}));
});

ws.addEventListener('message', (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.method === 'newHeads') {
    const { data } = msg.params;
    console.log('Head:', data.height, data.hash);
  } else if (msg.method === 'pendingTxs') {
    console.log('Pending:', msg.params.data.hash);
  }
});

8.2 Node (ws)

import WebSocket from 'ws';

const ws = new WebSocket('ws://127.0.0.1:8547/ws');
ws.on('open', () => {
  ws.send(JSON.stringify({ jsonrpc:'2.0', id:1, method:'subscribe', params:{ topic:'newHeads' }}));
});
ws.on('message', (buf) => {
  const m = JSON.parse(buf.toString());
  if (m.method === 'newHeads') console.log(m.params.data);
});

8.3 Python (websockets)

import asyncio, json, websockets

async def main():
    async with websockets.connect('ws://127.0.0.1:8547/ws') as ws:
        await ws.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"subscribe","params":{"topic":"newHeads"}}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("method") == "newHeads":
                print("Head", m["params"]["data"]["height"])

asyncio.run(main())


⸻

9) Operational Settings (reference)

From rpc/config.py (names may vary by build):
	•	WS_MAX_SUBSCRIPTIONS_PER_CONN (default: 32)
	•	WS_QUEUE_CAPACITY_PER_SUB (default: 2048 messages)
	•	WS_PING_INTERVAL_SECS (default: 30)
	•	WS_CORS_ALLOW_ORIGINS (exact match or wildcard rules)
	•	RATE_LIMITS.ws_messages_per_minute

⸻

10) FAQ
	•	Do I get one newHeads per block? Yes, exactly one per sealed canonical block.
	•	Will pendingTxs include tx content? By default you get the hash; set includeMeta: true to receive minimal metadata (safe and small).
	•	How do I detect gaps? You may receive an overflow event or notice a long reconnect; reconcile via the HTTP RPC.
	•	Is order guaranteed? Per-topic FIFO is maintained before overflow; across topics there is no cross-topic ordering guarantee.

⸻

Last updated: 2025-01-05
