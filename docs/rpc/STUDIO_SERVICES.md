# Studio Services API
_Read-only + helper endpoints for **deploy**, **verify**, **faucet**, **artifacts**, and **offline simulation**.
Backed by FastAPI; never holds user keys._

- **Base URL (examples):**
  - Local dev: `http://127.0.0.1:8787`
  - Prod example: `https://services.animica.org`
- Export once:
  ```bash
  export SERVICES_URL=${SERVICES_URL:-http://127.0.0.1:8787}

	•	Auth: API key (optional per deployment)
	•	Header: Authorization: Bearer <API_KEY>
	•	or query: ?key=<API_KEY>
	•	Content-Type: application/json (request+response)
	•	Errors: RFC7807 application/problem+json

{ "type": "about:blank", "title": "Bad Request", "status": 400, "detail": "why", "code": "VerifyFail" }


	•	Rate Limits: token-bucket (per IP/key/route). Headers may include X-RateLimit-Remaining, Retry-After.

⸻

Health & meta

GET /healthz • GET /readyz • GET /version

curl -s "$SERVICES_URL/healthz"
curl -s "$SERVICES_URL/readyz"
curl -s "$SERVICES_URL/version" | jq


⸻

Deploy

Accepts a signed CBOR transaction (hex string) and relays it to the node RPC. No server-side signing.

POST /deploy

Request (DeployRequest)

{
  "chainId": 1,
  "rawTx": "0xa1b2c3...cbor-hex",
  "labels": { "project":"demo","env":"dev" }
}

	•	chainId (int) — validated against service config (optional if single-chain).
	•	rawTx (string, hex) — signed CBOR tx bytes (as produced by wallet/SDK).
	•	labels (object) — optional audit metadata.

Response (DeployResponse)

{
  "txHash": "0x7f...aa",
  "submittedAt": "2025-01-01T12:34:56Z",
  "chainId": 1
}

Example

RAW_TX="0xdeadbeef..."
curl -s "$SERVICES_URL/deploy" \
  -H 'content-type: application/json' \
  -H "authorization: Bearer $API_KEY" \
  --data "{\"chainId\":1,\"rawTx\":\"$RAW_TX\"}" | jq


⸻

Preflight (chain-aware dry-run)

Runs a deterministic VM simulation of a signed tx (no state write). Useful to preview gas/return/revert reason.

POST /preflight

Request (PreflightRequest)

{ "chainId": 1, "rawTx": "0x..." }

Response (PreflightResult)

{
  "ok": true,
  "gasUsed": "21000",
  "logs": [],
  "ret": "0x", 
  "trace": null
}

Example

curl -s "$SERVICES_URL/preflight" \
  -H 'content-type: application/json' \
  --data "{\"chainId\":1,\"rawTx\":\"$RAW_TX\"}" | jq


⸻

Verify source (contract verification)

Recompiles source + manifest with the same toolchain and matches the generated code hash against on-chain code.

POST /verify

Request (VerifyRequest)

{
  "chainId": 1,
  "address": "anim1xyz...",
  "manifest": { /* JSON from build */ },
  "source": { "files": { "contract.py": "print('hi')" } },
  "compiler": { "vm_py": ">=0.1.0,<0.2.0" },
  "metadata": { "repo":"https://github.com/...","commit":"abc123" }
}

Response (VerifyStatus)

{
  "status": "MATCHED",
  "codeHash": "0xabc...",
  "address": "anim1xyz...",
  "chainId": 1,
  "verifiedAt": "2025-01-01T12:00:00Z",
  "artifactId": "art_01HF..."
}

Lookup
	•	GET /verify/{address}
	•	GET /verify/tx/{txHash}

curl -s "$SERVICES_URL/verify/anim1xyz..." | jq


⸻

Artifacts (code/ABI/metadata blobs)

Content-addressed, write-once storage; optionally mirrored to DA.

POST /artifacts

Request

{
  "kind": "abi|manifest|code|bundle",
  "content": { /* arbitrary JSON */ },
  "links": { "address": "anim1xyz..." }
}

Response

{ "id":"art_01HF...", "digest":"sha3-512:abcd...", "size": 1234 }

GET /artifacts/{id} • GET /address/{addr}/artifacts

curl -s "$SERVICES_URL/artifacts/art_01HF..." | jq
curl -s "$SERVICES_URL/address/anim1xyz.../artifacts" | jq


⸻

Faucet (optional; guarded)

Small, rate-limited drip for dev/test networks only.

POST /faucet/drip

Request

{ "address": "anim1xyz...", "amount": "1000000000000000000" }

	•	amount optional; service enforces per-route caps.

Response

{ "ok": true, "txHash": "0xfeed..." }

Example

curl -s "$SERVICES_URL/faucet/drip" \
  -H 'content-type: application/json' \
  -H "authorization: Bearer $API_KEY" \
  --data '{"address":"anim1xyz...","amount":"100000000000000000"}' | jq

Failure cases
	•	403 FaucetOff — faucet disabled in this environment.
	•	429 RateLimited — token bucket exhausted.

⸻

Offline simulate (no-chain compile+call)

Compiles source & manifest locally (browser-compatible VM) and simulates a single call.

POST /simulate

Request

{
  "manifest": { /* ABI + metadata */ },
  "source": { "files": { "contract.py": "..." } },
  "call": { "fn": "inc", "args": [], "gasLimit": 200000 },
  "env": { "block": { "height": 1 }, "tx": { "caller": "anim1..." } }
}

Response

{
  "ok": true,
  "gasUsed": "12345",
  "ret": "0x",
  "logs": [{ "name":"Incremented", "args":{"by":1} }]
}


⸻

Security & CORS
	•	Strict CORS allowlist (origins) and per-route auth policy.
	•	No secret keys accepted or stored; all signing must happen client-side.
	•	Verification compilers/toolchains are pinned; results are reproducible and hashed.

⸻

Common error codes

code	http	meaning
BadRequest	400	malformed body or missing field
ChainMismatch	400	request chainId != service config
VerifyFail	422	source/manifest does not match code hash
FaucetOff	403	faucet disabled
RateLimited	429	too many requests
UpstreamError	502	node RPC error while relaying
InternalError	500	unexpected server error


⸻

End-to-end examples

Deploy → await receipt (bash + jq)

RAW_TX="0x..."
TX=$(curl -s "$SERVICES_URL/deploy" -H 'content-type: application/json' \
  --data "{\"chainId\":1,\"rawTx\":\"$RAW_TX\"}" | jq -r '.txHash')

echo "txHash: $TX"
# then poll your node RPC for receipt

Verify source for a known address

curl -s "$SERVICES_URL/verify" \
  -H 'content-type: application/json' \
  --data @verify_request.json | jq


⸻

Notes
	•	These endpoints are stateless where possible; durable state lives on-chain or in content-addressed storage.
	•	For SDK wrappers, see sdk/python, sdk/typescript clients targeting these routes.
	•	See studio-services/ directory for implementation details (config, middleware, tasks).
