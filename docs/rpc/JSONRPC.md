# Animica JSON-RPC API

This document describes the **JSON-RPC 2.0** interface exposed by the Animica node.
It covers transports, common types, core methods, optional modules (DA, AICF,
Capabilities, Randomness), WebSocket subscriptions, batching, and error codes.

- **HTTP endpoint:** `POST /rpc`
- **WebSocket endpoint:** `GET /ws` (JSON-RPC messages over WS)
- **OpenRPC schema:** `GET /openrpc.json` (served verbatim)

> Modules map to repository packages:
> - `rpc/` (core JSON-RPC dispatcher, middleware, WS hub)
> - `da/adapters/rpc_mount.py` (Data Availability bindings)
> - `capabilities/rpc/` (deterministic syscall visibility)
> - `aicf/rpc/` (AI Compute Fund registry/settlement views)
> - `randomness/rpc/` (beacon methods)

---

## 1) Transport & Envelope

All requests use **JSON-RPC 2.0**:

```jsonc
// Request
{
  "jsonrpc": "2.0",
  "id": 1,                          // number | string | null
  "method": "chain.getHead",
  "params": []                      // array or object
}

// Response (success)
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": { "height": 1234, "hash": "0x…", "time": "2025-01-05T12:34:56Z" }
}

// Response (error)
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": { "code": -32000, "message": "Server error", "data": { "reason": "…" } }
}

1.1 Batching

Send an array of request objects; the response is an array of results in any order.

1.2 Rate-Limits & CORS

Limits and CORS allow-list are controlled by rpc/config.py. Exceeding a bucket returns
RateLimited (code: -32001) with retry hints in error.data.

⸻

2) Conventions & Types
	•	Hex strings: prefixed 0x… for hashes, commitments, byte blobs.
	•	Addresses: bech32m anim1… (derived from PQ pubkey digest); see docs/spec/ADDRESSES.md.
	•	Big integers: decimal strings unless otherwise noted.
	•	Blocks: refer by number (height) or hash.
	•	CBOR payloads: raw tx bytes are CBOR-encoded per spec/tx_format.cddl.

Common shapes (abbreviated):

type Head = { height: number; hash: string; time: string; };
type BlockView = { header: any; txs?: string[]; receipts?: any[]; proofs?: any[]; };
type TxView = { hash: string; from: string; to?: string; nonce: number; gas: string; value?: string; status?: "pending"|"mined"; };
type ReceiptView = { txHash: string; status: "SUCCESS"|"REVERT"|"OOG"; gasUsed: string; logs: any[]; blockHash: string; blockNumber: number; };


⸻

3) Core Methods

3.1 Chain

chain.getParams() -> object

Returns canonical chain parameters (subset of spec/params.yaml): Θ, Γ, gas tables, limits.

chain.getChainId() -> number

Numeric chain ID.

chain.getHead() -> Head

Current canonical head (height/hash/time).

Example

{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}


⸻

3.2 Blocks

chain.getBlockByNumber(number, opts?) -> BlockView|null
	•	number: 0..N or "finalized"|"safe"|"latest"
	•	opts: { txs?: boolean, receipts?: boolean, proofs?: boolean }

chain.getBlockByHash(hash, opts?) -> BlockView|null

⸻

3.3 Transactions

tx.sendRawTransaction(rawCborTx) -> string
	•	rawCborTx: 0x… CBOR bytes of the signed tx (domain-separated per spec)
	•	Returns tx hash (0x…)

Errors: InvalidTx, ChainIdMismatch, SignatureInvalid, FeeTooLow, NonceGap, Oversize, Duplicate.

Example

{
  "jsonrpc":"2.0","id":"0xabc",
  "method":"tx.sendRawTransaction",
  "params":["0xa2…cb"] // CBOR
}

tx.getTransactionByHash(hash) -> TxView|null

⸻

3.4 State

state.getBalance(address) -> string

Balance in base units (decimal string).

state.getNonce(address) -> number

⸻

3.5 Receipts

tx.getTransactionReceipt(txHash) -> ReceiptView|null

⸻

4) WebSocket Subscriptions

Connect to /ws, then send subscribe messages.

4.1 Topics
	•	newHeads — stream of Head
	•	pendingTxs — tx hashes admitted to pending pool
	•	(if DA mounted) da.committed — new blob commitments
	•	(if Capabilities mounted) cap.jobCompleted
	•	(if AICF mounted) aicf.jobAssigned, aicf.jobCompleted
	•	(randomness) rand.beaconFinalized

Subscribe

{"jsonrpc":"2.0","id":1,"method":"subscribe","params":{"topic":"newHeads"}}

Ack

{"jsonrpc":"2.0","id":1,"result":{"subscriptionId":"sub-7b2a"}}

Push

{"jsonrpc":"2.0","method":"newHeads","params":{"subscriptionId":"sub-7b2a","data":{"height":1235,"hash":"0x…","time":"…"}}}

Unsubscribe:

{"jsonrpc":"2.0","id":2,"method":"unsubscribe","params":{"subscriptionId":"sub-7b2a"}}


⸻

5) Data Availability (optional module)

If da/adapters/rpc_mount.py is enabled:

da.putBlob(namespace, dataHex) -> {commitment: string, size: number, namespace: number}
	•	Validates envelope, computes NMT root, persists to local store.

da.getBlob(commitment) -> {namespace:number, size:number, data:string}
	•	Returns the full blob (may be range-limited on large objects).

da.getProof(commitment, samples?) -> {proof: object, params: object}
	•	Returns a DAS proof compatible with da/sampling/light_client.py.

Errors: DANotFound, DAInvalidProof, InvalidParams.

⸻

6) Randomness (optional module)

From randomness/rpc/methods.py:

rand.getParams() -> object

VDF params, round lengths, QRNG status.

rand.getRound() -> {id:number, opensAt:string, closesAt:string, status:string}

rand.commit(payloadHex, saltHex) -> {roundId:number, commitment:string}

Window checks enforced.

rand.reveal(commitment, payloadHex, saltHex) -> {accepted:boolean}

Fails with CommitTooLate, RevealTooEarly, BadReveal.

rand.getBeacon(roundId|\"latest\") -> {roundId:number, output:string, lightProof:object}

rand.getHistory(offset, limit) -> {items: object[], next?: number}

⸻

7) Capabilities (optional read-only)

From capabilities/rpc/methods.py:

cap.getJob(taskId) -> Result

cap.listJobs(filter?) -> {items: Result[], next?: string}

cap.getResult(taskId) -> Result

Errors: NoResultYet, LimitExceeded, NotDeterministic (if misuse).

⸻

8) AICF — AI Compute Fund (optional)

From aicf/rpc/methods.py:

aicf.listProviders(filter?) -> {items: Provider[], next?: string}

aicf.getProvider(providerId) -> Provider|null

aicf.listJobs(filter?) -> {items: Job[], next?: string}

aicf.getJob(jobId) -> Job|null

aicf.claimPayout(providerId, epoch) -> {accepted:boolean, payout:string}

aicf.getBalance(providerId) -> {available:string, locked:string}

Errors: NotFound, NotEligible, InsufficientStake.

⸻

9) Examples

9.1 Get head (HTTP)

curl -sS -X POST "$RPC_URL/rpc" \
  -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}'

9.2 Send a signed CBOR tx

curl -sS -X POST "$RPC_URL/rpc" -H 'content-type: application/json' --data @- <<'JSON'
{"jsonrpc":"2.0","id":"send-1","method":"tx.sendRawTransaction","params":["0xa2…cb"]}
JSON

9.3 Batch

[
  {"jsonrpc":"2.0","id":"h","method":"chain.getHead","params":[]},
  {"jsonrpc":"2.0","id":"b","method":"chain.getBlockByNumber","params":[ "latest", {"txs":false} ]}
]


⸻

10) Error Codes

Code	Name	When
-32600	InvalidRequest	Bad JSON-RPC envelope
-32601	MethodNotFound	Unknown method
-32602	InvalidParams	Param shape/validation error
-32603	InternalError	Unhandled server error
-32000	ServerError	Generic server failure
-32001	RateLimited	Per-IP or per-method token bucket exhausted
-32010	InvalidTx	CBOR decode/stateless validation failed
-32011	ChainIdMismatch	Tx chainId differs from node
-32012	SignatureInvalid	PQ signature invalid
-32013	FeeTooLow	Below dynamic floor/min-gas
-32014	NonceGap	Nonce gap against account state
-32015	Oversize	Tx exceeds byte/gas limits
-32016	Duplicate	Already known
-32017	NotFound	Generic “not found”
-32018	HeaderNotFound	Header lookup failed
-32019	BlockNotFound	Block lookup failed
-32020	TxNotFound	Transaction/receipt missing
-32030	DAInvalidProof	Malformed/invalid DA proof
-32031	DANotFound	Commitment or blob not found
-32041	NotEligible	AICF payout/job claim not eligible
-32042	InsufficientStake	AICF stake too low
-32051	CommitTooLate	Randomness commit outside window
-32052	RevealTooEarly	Randomness reveal before window
-32053	BadReveal	Reveal doesn’t match prior commit
-32061	NotDeterministic	Capabilities misuse; non-deterministic input
-32062	LimitExceeded	Input/result size exceeds policy
-32063	NoResultYet	Result not yet available

Error payload

{
  "code": -32014,
  "message": "NonceGap",
  "data": { "expected": 7, "got": 5, "address": "anim1…" }
}


⸻

11) Object Notes
	•	Tx bytes: must be canonical CBOR, with deterministic map ordering and proper sign-domain per core/encoding/canonical.py.
	•	Receipts: stable encoding per execution/receipts/encoding.py; blooms and logs root match spec/RECEIPTS_EVENTS.md.
	•	Proofs: PoIES proofs are verified in proofs/ and summarized into inclusion metrics; not all internals are exposed via RPC.

⸻

12) Security Considerations
	•	Enforce strict CORS and rate-limit on public nodes.
	•	Avoid exposing privileged endpoints; this API is read/write for tx submission only (no server-side signing).
	•	Prefer WS subscriptions to reduce polling load.
	•	Validate bech32m addresses & 0x hex strings rigorously.

⸻

13) Changelog Snippets
	•	v0.5 — Added DA RPC bridge (da.*) and Randomness methods (rand.*).
	•	v0.4 — Added WS topics pendingTxs, newHeads; standardized error data.
	•	v0.3 — Introduced chain.getParams; receipts toggles on block lookups.
	•	v0.2 — Initial core methods (chain.*, tx.*, state.*).
	•	v0.1 — Draft.

