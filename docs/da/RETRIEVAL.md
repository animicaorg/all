# Data Retrieval API & Light-Client Expectations

This document specifies the **Data Availability Retrieval API** implemented by `da/retrieval/api.py` and the minimum **verification duties** for light clients that consume it.

> Related specs & code  
> • Schemas: `da/schemas/retrieval_api.schema.json`  
> • Retrieval service: `da/retrieval/{api.py,service.py,handlers.py,rate_limit.py,auth.py}`  
> • NMT proofs: `da/nmt/{verify.py,proofs.py}`, `da/specs/NMT.md`  
> • Erasure layout: `da/erasure/{params.py,layout.py}`, `docs/da/ERASURE_LAYOUT.md`  
> • DAS algorithm: `da/sampling/{sampler.py,probability.py}`, `docs/da/SAMPLING.md`

---

## 1) Objects & identifiers

- **Commitment**: `0x`-prefixed hex of the **NMT root** (32 bytes). This is the content address for blobs and proof lookups.  
  Example: `0x8d…ce` (64 hex chars).
- **Namespace ID (ns)**: 32-bit unsigned integer (documented in `da/nmt/namespace.py` and `da/specs/NMT.md`).  
- **Share index**: Zero-based leaf index in the finalized **extended** (data+parity) matrix. Mapping helpers live in `da/nmt/indices.py`.

---

## 2) Endpoints (canonical)

### 2.1 POST `/da/blob`
Upload a blob, receive its content-addressed **commitment** and a **receipt**.

**Request (multipart or raw)**

- `Content-Type: application/octet-stream` (preferred)  
  Headers:  
  - `X-Animica-Namespace: <u32>` — required (namespace id)  
  - `X-Animica-Mime: <string>` — optional, e.g. `application/cbor`  
- Alternatively: `multipart/form-data` with fields:
  - `file` (binary), `namespace` (u32), optional `mime`

**Response — 201 Created**
```json
{
  "commitment": "0x<64-hex-nmt-root>",
  "namespace": 24,
  "size_bytes": 262144,
  "shares": { "rows": 16, "cols": 32, "k": 24, "n": 32 },
  "receipt": {
    "commitment": "0x<same>",
    "namespace": 24,
    "size_bytes": 262144,
    "sha3_256": "0x<64-hex>",
    "alg_policy_root": "0x<64-hex>",
    "signature": {
      "alg_id": "dilithium3",
      "sig": "0x<...>"
    }
  }
}

Notes
	•	Upload acceptance performs envelope/schema checks but does not make the data “available” in consensus terms until a block commits its DA root.
	•	The receipt.signature binds the commitment to the service identity & policy root; it is optional but recommended.

⸻

2.2 GET /da/blob/{commitment}

Download the raw blob bytes by content id.

Request
	•	Supports Range: bytes=start-end for partial fetch.
	•	Optional Accept: defaults to application/octet-stream.

Response — 200 OK
	•	Content-Type: application/octet-stream
	•	ETag: "nmt:<root-hex>"
	•	Returns the exact byte payload used to build the commitment.

Response — 206 Partial Content
	•	Returned when Range is present; aligned to blob byte offsets (not share boundaries).

Errors
	•	404 Not Found if unknown.
	•	410 Gone if blob was GC’d (un-pinned) per retention policy.

⸻

2.3 GET /da/blob/{commitment}/proof?indices=i0,i1,…

Fetch NMT proofs for a set of share indices (comma-separated, base-10).

Response — 200 OK

{
  "commitment": "0x<64-hex-nmt-root>",
  "namespace": 24,
  "indices": [102, 4095, 8192],
  "proofs": [
    {
      "index": 102,
      "leaf": "0x<hex-share-bytes>",
      "branch": ["0x<hex>", "..."],
      "siblings_namespace_ranges": [[min,max], "..."]
    },
    { "...": "..." }
  ]
}

Notes
	•	The branch shape matches da/schemas/nmt.cddl.
	•	Proofs MUST verify against commitment and prove both inclusion and correct namespace range membership.

⸻

3) Authentication, rate limits, and CORS
	•	The service may enforce API tokens (Authorization: Bearer <token>) via da/retrieval/auth.py.
	•	Rate limits are applied per IP/token with token buckets (see da/retrieval/rate_limit.py). Expect 429 Too Many Requests with Retry-After.
	•	CORS is strict; browser clients should call via the website/studio origin allow-listed in config.

⸻

4) Light client: verification duties

A light client must never trust raw bytes. On every request it MUST:
	1.	Bind to header
	•	Obtain the block header and load header.da_root.
	•	Ensure da_root == commitment of the content being proven/fetched.
	2.	Verify proofs (for each sampled index)
	•	Recompute the NMT root from leaf + branch + namespace ranges using da/nmt/verify.py.
	•	Check equality with commitment.
	•	If any proof fails, mark the block unavailable.
	3.	Account for timeouts & partials
	•	Treat request timeouts and 5xx as missing for the purpose of DAS thresholds (see docs/da/SAMPLING.md).
	•	Apply the per-stripe > t = n − k missing rule to flag unavailability.
	4.	Record coverage
	•	Cache (stripe, column) verifications to avoid duplicate work across rounds.
	•	Invalidate cache on reorg past the audited height.
	5.	Avoid bias
	•	Derive sample indices using a CSPRNG seeded with (blockHash, clientSalt, roundCounter).

⸻

5) Error model

Code	Meaning	Client handling
400	Malformed request	Do not retry as-is
401/403	Auth failure	Fix token / scope
404	Unknown commitment	If referenced by a finalized header, treat as missing
410	Blob GC’d	Treat as missing (unless a pin exists)
413	Payload too large	Split uploads or adjust policy
415	Unsupported media type	Re-send as application/octet-stream
429	Rate limited	Exponential backoff; respect Retry-After
5xx	Server error	Retry with jitter; count as missing for DAS if persistent


⸻

6) Examples

6.1 Upload

curl -sS -X POST \
  -H 'Content-Type: application/octet-stream' \
  -H 'X-Animica-Namespace: 24' \
  --data-binary @blob.bin \
  https://node.example.org/da/blob | jq

6.2 Download (with Range)

curl -H 'Range: bytes=0-1023' \
  https://node.example.org/da/blob/0x<root> -o part.bin

6.3 Get proofs for three indices

curl https://node.example.org/da/blob/0x<root>/proof?indices=102,4095,8192 | jq

6.4 Light client pseudo-code (sampling)

root = header.da_root
idxs = plan_indices(header_hash, salt, round)
resp = GET(f"/da/blob/{root}/proof?indices={','.join(map(str,idxs))}")
for pr in resp["proofs"]:
    ok = nmt_verify(root, pr["leaf"], pr["branch"], pr["siblings_namespace_ranges"])
    record(pr["index"], ok)
if any_failed() or stripe_missing_count_exceeds_t():
    mark_unavailable(header.hash)


⸻

7) Retention, pinning, and GC
	•	The store (da/blob/store.py) supports pin/unpin. Unpinned blobs may be garbage-collected.
	•	Nodes SHOULD pin any commitment referenced by a canonical block for ≥ the reorg horizon.
	•	API returns 410 Gone for GC’d blobs; light clients still succeed via peer diversity (P2P retrieval) or fallback mirrors.

⸻

8) Security notes
	•	Proof verification uses domain-separated hashes; see da/utils/hash.py.
	•	Range requests do not affect DA guarantees; proofs operate on shares, not byte offsets.
	•	The service must avoid oracle leaks: constant-time proof checks where feasible; rate-limit abusive probes.
	•	Use TLS; set strict CORS & CSP when mounted under the main RPC app.

⸻

9) Conformance checklist (server)
	•	POST /da/blob returns commitment & (optional) signed receipt
	•	GET /da/blob/{commitment} supports Range and strong ETag
	•	GET /da/blob/{commitment}/proof returns valid NMT proofs for arbitrary indices
	•	Auth/rate limit middleware in place; metrics exported
	•	Retention policy configurable; pinned on canonical references

⸻

10) Conformance checklist (light client)
	•	Binds proofs to header.da_root
	•	Verifies inclusion and namespace-range proofs
	•	Implements stratified DAS with timeouts treated as missing
	•	Maintains cache with reorg invalidation
	•	Uses CSPRNG, replayable seeds for audits

