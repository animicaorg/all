# Animica Stratum (JSON-RPC over TCP)

This document specifies the **Animica Stratum** mining protocol spoken by
`mining/stratum_server.py` and `mining/stratum_client.py`. It is a
JSON-RPC 2.0 dialect transported over **length-prefixed TCP frames** with a few
Animica-specific extensions for **PoIES** (Proofs of Integrated, Efficient, and
Secure work), **HashShare** micro-targets, and **AI/Quantum proof attachments**.

It is designed to be:
- **Simple and robust** for LAN setups (miners ↔ local node) with optional TLS.
- **Backwards-aware** for bridging: the `mining/cli/stratum_proxy.py` can adapt
  external miners that only do hash shares.
- **Extensible**: arbitrary proof attachments (AI/Quantum/Storage/VDF) and policy
  roots are carried alongside header templates.

> Related specs:
> - `mining/specs/GETWORK.md` (WebSocket GetWork).
> - `consensus/` (Θ schedule, Γ caps, fairness).
> - `proofs/schemas/*.cddl` (CBOR bodies for proofs).

---

## 1) Transport & Versioning

- **Transport**: TCP with length-prefix framing (u32 big-endian). Each frame is a
  single UTF-8 JSON object representing one JSON-RPC request or response.

- **Default listen**: `0.0.0.0:4343` (configurable via `mining/config.py`).

- **TLS (optional)**: if `--tls-cert/--tls-key` are provided to the server, the
  socket is wrapped (TLS 1.2+). Otherwise plaintext on trusted LAN only.

- **Protocol ID**: `"animica/stratum/1"`. The server advertises it in `mining.hello`.

- **JSON-RPC**: `"jsonrpc": "2.0"` is mandatory; **named params** are required.

- **Limits**: server enforces per-IP token-buckets (see `rpc/middleware/rate_limit.py`)
  and per-session message rate.

---

## 2) Framing

Each outbound frame:

[ u32_be_len ][ JSON bytes… ]

A single JSON per frame, no batching inside a frame. Servers/clients may pipeline.

---

## 3) Session Lifecycle

### 3.1 Hello

Server sends `mining.hello` **upon TCP accept**:

```json
{
  "jsonrpc": "2.0",
  "method": "mining.hello",
  "params": {
    "protocol": "animica/stratum/1",
    "nodeVersion": "animica-rpc/0.1.0",
    "network": { "chainId": 1, "name": "animica:mainnet" },
    "thetaMicro": 5400000,
    "policyRoots": {
      "poies": "0x…",      // merkle root of poies_policy.yaml
      "algPolicy": "0x…"   // PQ alg-policy root
    },
    "extranonceBytes": 8,
    "features": ["hashshare", "attachments", "ai", "quantum", "storage", "vdf"]
  },
  "id": null
}

The client must reply with mining.subscribe then mining.authorize.

3.2 Subscribe

{
  "jsonrpc": "2.0",
  "method": "mining.subscribe",
  "params": {
    "agent": "example-miner/0.3.1",
    "features": ["hashshare","attachments"]
  },
  "id": 1
}

Response:

{
  "jsonrpc": "2.0",
  "result": {
    "sessionId": "sess_3b27…",
    "extranonce1": "b64:V9sD2u2d",     // server-chosen prefix (binary, base64)
    "extranonce2Size": 8,               // miner appends per-share nonce suffix
    "targetHint": { "thetaMicro": 5400000, "shareRatio": 0.0005 }
  },
  "id": 1
}

3.3 Authorize

{
  "jsonrpc": "2.0",
  "method": "mining.authorize",
  "params": {
    "address": "anim1qq…",           // payout address (PQ address format)
    "signature": "b64:…"             // optional proof of address ownership
  },
  "id": 2
}

result: true means authorized. The server may throttle unauthorized sessions.

⸻

4) Difficulty & Templates

4.1 Set Difficulty (micro-target)

Server may push:

{
  "jsonrpc": "2.0",
  "method": "mining.setTarget",
  "params": {
    "thetaMicro": 5400000,
    "shareRatio": 0.0005,           // accept shares with D_ratio >= 0.0005
    "ttlSec": 30
  },
  "id": null
}

4.2 New Work (Template)

Server pushes mining.notify whenever the head or params change:

{
  "jsonrpc": "2.0",
  "method": "mining.notify",
  "params": {
    "jobId": "job_7f3b…",
    "headerTemplate": {
      "parentHash": "0x…",
      "number": 123456,
      "mixSeed": "0x…",             // binds u-draw domain (see consensus/math.py)
      "roots": {
        "state": "0x…",
        "txs": "0x…",
        "proofs": "0x…",
        "da": "0x…"
      },
      "thetaMicro": 5400000,
      "policyRoots": { "poies": "0x…", "algPolicy": "0x…" },
      "nonceDomainTag": "ANIMICA-NONCE-V1"
    },
    "extranonce1": "b64:V9sD2u2d",
    "extranonce2Size": 8,
    "mutations": {
      "nonce": true,
      "attachments": true,       // miner may attach additional proofs
      "txSelect": false          // (server packs txs on submit by default)
    },
    "ttlSec": 30
  },
  "id": null
}

The miner should abandon previous jobIds when a fresh notify arrives.

⸻

5) Share Submission

5.1 HashShare-only

mining.submit carries the HashShare proof (CBOR body) and the bound header
fields used to compute the u-draw and D_ratio:

{
  "jsonrpc": "2.0",
  "method": "mining.submit",
  "params": {
    "jobId": "job_7f3b…",
    "extranonce2": "b64:AAAAAAAB",         // 8-byte miner suffix
    "nonce": "0x00000000a9f2…",
    "hashshare": {
      "envelope": {
        "type_id": 0,                      // HashShare
        "body_cbor": "b64:…",              // matches proofs/schemas/hashshare.cddl
        "nullifier": "0x…"                 // domain-separated nullifier
      },
      "metrics": {
        "d_ratio": 0.00123                 // optional precomputed ratio (server rechecks)
      }
    }
  },
  "id": 42
}

Success response:

{
  "jsonrpc": "2.0",
  "result": {
    "accepted": true,
    "d_ratio": 0.00123,
    "asBlock": false,
    "reason": null
  },
  "id": 42
}

Common reject codes (JSON-RPC error):
	•	InvalidJob – unknown/expired jobId.
	•	StaleShare – template rolled over.
	•	BelowTarget – D_ratio < shareRatio.
	•	BadHashshare – envelope invalid, nullifier reused, or schema mismatch.
	•	RateLimited – per-IP/session bucket exceeded.

5.2 Attachments (AI / Quantum / Storage / VDF)

Animica stratum extends mining.submit with attachments[]:

{
  "jsonrpc": "2.0",
  "method": "mining.submit",
  "params": {
    "jobId": "job_7f3b…",
    "extranonce2": "b64:AAAAAAAB",
    "nonce": "0x00000000a9f2…",
    "hashshare": { "envelope": { "type_id": 0, "body_cbor": "b64:…", "nullifier": "0x…" } },
    "attachments": [
      { "type": "AI", "envelope": { "type_id": 2, "body_cbor": "b64:…", "nullifier": "0x…" } },
      { "type": "QUANTUM", "envelope": { "type_id": 3, "body_cbor": "b64:…", "nullifier": "0x…" } },
      { "type": "VDF", "envelope": { "type_id": 5, "body_cbor": "b64:…", "nullifier": "0x…" } }
    ]
  },
  "id": 77
}

The server verifies each attachment via proofs/registry.py and maps their
metrics to ψ-inputs (caps are enforced later by block-packer).

Response includes per-attachment verdicts:

{
  "jsonrpc": "2.0",
  "result": {
    "accepted": true,
    "d_ratio": 0.00201,
    "attachmentResults": [
      { "type": "AI", "ok": true, "psi_input": { "ai_units": 120, "qos": 0.98 } },
      { "type": "QUANTUM", "ok": false, "error": "AttestationError" },
      { "type": "VDF", "ok": true, "psi_input": { "vdf_seconds": 2.3 } }
    ],
    "asBlock": false
  },
  "id": 77
}

5.3 Full Block Candidate (optional)

Large miners can form a candidate block locally and submit:

{
  "jsonrpc": "2.0",
  "method": "mining.submitBlock",
  "params": {
    "jobId": "job_7f3b…",
    "block_cbor": "b64:…",        // core/types/Block encoded as in core/encoding/cbor.py
    "proofs_envelopes": [ … ]     // optional: redundancy for server validation path
  },
  "id": 88
}

If accepted, server returns { "result": { "accepted": true, "asBlock": true, "hash": "0x…" } }
and triggers gossip/DB writes.

⸻

6) Extranonce, Nonce & Domains
	•	Server assigns extranonce1 (random per session). Miner chooses extranonce2
per share; both are mixed into the nonce domain (nonceDomainTag) and
mixSeed in the header when computing u-draw (H(u) = −ln(u) domain; see
consensus/math.py and mining/nonce_domain.py).
	•	Reusing the same tuple (jobId, extranonce2, nonce) is a duplicate.
	•	Nullifiers for all proofs are domain-separated (see proofs/nullifiers.py).

⸻

7) Error Model & JSON-RPC Codes
	•	JSON-RPC errors include:
	•	-32600 Invalid Request
	•	-32601 Method not found
	•	-32602 Invalid params
	•	-32000 Server error (with data.code among: InvalidJob, StaleShare,
BelowTarget, BadHashshare, RateLimited, AttachmentInvalid).

Example:

{
  "jsonrpc": "2.0",
  "error": {
    "code": -32000,
    "message": "BelowTarget",
    "data": { "d_ratio": 0.00021, "required": 0.00050 }
  },
  "id": 42
}


⸻

8) Keep-alive & Heartbeats
	•	Server may send mining.keepalive every 15s; client responds with a bare
{"jsonrpc":"2.0","result":true,"id":<same>} if an id is set (some heartbeats are notifications).
	•	Idle sessions are closed after idleTimeoutSec (default 120s).

⸻

9) Security & DoS Notes
	•	LAN only unless TLS is enabled. Do not expose unauth Stratum to the internet.
	•	Per-IP/session token buckets apply to submit and subscribe.
	•	Attachments are validated with strict size/time limits; invalid attachments do
not taint the share unless policy says otherwise (configurable).
	•	Server rejects templates older than ttlSec.

⸻

10) Worked Transcript

A bridged session excerpt (see mining/fixtures/stratum_session.trace.json):

S → { "method":"mining.hello", "params":{…} }
C → { "id":1, "method":"mining.subscribe", "params":{"agent":"cpu-miner/1.0","features":["hashshare"]}}
S → { "id":1, "result":{"sessionId":"sess…","extranonce1":"b64:V9sD2u2d","extranonce2Size":8,"targetHint":{"thetaMicro":5400000,"shareRatio":0.0005}}}
C → { "id":2, "method":"mining.authorize", "params":{"address":"anim1qq…"}}
S → { "id":2, "result":true }
S → { "method":"mining.notify", "params":{"jobId":"job_7f3b…", "headerTemplate":{…}, "extranonce1":"b64:V9sD2u2d","extranonce2Size":8}}
C → { "id":42, "method":"mining.submit", "params":{"jobId":"job_7f3b…","extranonce2":"b64:AAAAAAAB","nonce":"0x0000…","hashshare":{"envelope":{"type_id":0,"body_cbor":"b64:…","nullifier":"0x…"}}}}
S → { "id":42, "result":{"accepted":true,"d_ratio":0.00123,"asBlock":false}}


⸻

11) Animica Extensions Summary
	•	Theta/Γ awareness: server exposes current thetaMicro and policy roots.
	•	Attachments: AI/Quantum/Storage/VDF envelopes travel with shares.
	•	Nullifier & domain tags: explicit; see spec/domains.yaml.
	•	Micro-target ratio: d_ratio is a first-class metric.

⸻

12) Reference Implementations
	•	Server: mining/stratum_server.py
	•	Client: mining/stratum_client.py
	•	CPU scan loop: mining/hash_search.py
	•	Header/nonce domain: mining/nonce_domain.py
	•	Share target math: mining/share_target.py
	•	Proof verification: proofs/*

⸻

13) Test & Dev Tips
	•	Start server: python -m mining.cli.stratum_proxy --listen 0.0.0.0:4343
	•	Dummy client: python -m mining.cli.getwork (or use WS GetWork)
	•	Enable TLS: --tls-cert server.crt --tls-key server.key
	•	Trace I/O: set env LOG_LEVEL=DEBUG

