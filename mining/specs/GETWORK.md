# Animica GetWork over WebSocket

This document specifies the **Animica GetWork** WebSocket protocol implemented by
`mining/ws_getwork.py` (and mounted by `mining/bridge_rpc.py`). It delivers live
**header templates** and accepts **share submissions** with optional **AI/Quantum/Storage/VDF**
attachments. It complements the TCP Stratum spec (`STRATUM.md`) and reuses the
same concepts (Θ in µ-nats, HashShare D_ratio, policy roots, nullifiers).

> Endpoint: `ws://<host>:<port>/ws/getwork`  
> (If the node runs behind TLS or a reverse proxy: `wss://…/ws/getwork`)

---

## 1) Transport & Envelope

- Frames are **single JSON objects** (text frames). No JSON-RPC wrapper.
- Each frame has an `"op"` field (operation) and optional `"id"`, `"seq"`, `"params"`.

```jsonc
{
  "op": "work",            // operation (hello | subscribe | work | submitShare | ok | error | ping | pong)
  "id": 7,                 // optional correlator for request→response (client-sent)
  "seq": 184,              // server-side monotonically increasing sequence (work/ping/keepalive)
  "params": { … }          // payload
}

Servers never reuse seq. Clients may store the last seen seq to resume after reconnects.

⸻

2) Session Boot

2.1 Server Hello (unsolicited)

On connect, server sends a hello:

{
  "op": "hello",
  "seq": 1,
  "params": {
    "protocol": "animica/getwork/1",
    "nodeVersion": "animica-rpc/0.1.0",
    "network": { "chainId": 1, "name": "animica:mainnet" },
    "thetaMicro": 5400000,
    "policyRoots": {
      "poies": "0x…",
      "algPolicy": "0x…"
    },
    "extranonceBytes": 8,
    "features": ["hashshare","attachments","ai","quantum","storage","vdf"],
    "sessionId": "sess_3b27a7…",
    "resumeCap": true
  }
}

2.2 Client Subscribe

Client identifies and (optionally) resumes:

{
  "op": "subscribe",
  "id": 1,
  "params": {
    "agent": "example-miner/0.3.1",
    "features": ["hashshare","attachments"],
    "address": "anim1qq…",               // optional payout address (PQ bech32m)
    "resume": {
      "sessionId": "sess_3b27a7…",       // echo from hello if reconnecting
      "lastSeenSeq": 183                 // highest seq client fully processed
    }
  }
}

Response:

{
  "op": "ok",
  "id": 1,
  "params": {
    "accepted": true,
    "extranonce1": "b64:V9sD2u2d",
    "extranonce2Size": 8,
    "targetHint": { "thetaMicro": 5400000, "shareRatio": 0.0005 }
  }
}

If resume is present and valid, the server may replay the latest work (but not historical).

⸻

3) Receiving Work

Server pushes work when heads/params change or at keep-alive intervals:

{
  "op": "work",
  "seq": 184,
  "params": {
    "jobId": "job_7f3b…",
    "headerTemplate": {
      "parentHash": "0x…",
      "number": 123456,
      "mixSeed": "0x…",
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
      "attachments": true,
      "txSelect": false
    },
    "ttlSec": 30
  }
}

Clients should abandon older jobIds when a fresh work arrives.

⸻

4) Submitting Shares

4.1 HashShare-only

{
  "op": "submitShare",
  "id": 42,
  "params": {
    "jobId": "job_7f3b…",
    "extranonce2": "b64:AAAAAAAB",      // miner-chosen, length = extranonce2Size
    "nonce": "0x00000000a9f2…",
    "hashshare": {
      "envelope": {
        "type_id": 0,                   // HashShare
        "body_cbor": "b64:…",           // proofs/schemas/hashshare.cddl encoded body
        "nullifier": "0x…"              // computed per proofs/nullifiers.py
      },
      "metrics": { "d_ratio": 0.00123 } // optional hint; server recomputes
    }
  }
}

Server responds:

{
  "op": "ok",
  "id": 42,
  "params": {
    "accepted": true,
    "d_ratio": 0.00123,
    "asBlock": false
  }
}

Possible errors (see §7):

{
  "op": "error",
  "id": 42,
  "params": {
    "code": "BelowTarget",
    "message": "share below configured ratio",
    "data": { "d_ratio": 0.00021, "required": 0.00050 }
  }
}

4.2 With Attachments (AI / Quantum / Storage / VDF)

Add attachments[] beside hashshare:

{
  "op": "submitShare",
  "id": 77,
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
  }
}

Response contains per-attachment verdicts:

{
  "op": "ok",
  "id": 77,
  "params": {
    "accepted": true,
    "d_ratio": 0.00201,
    "attachmentResults": [
      { "type": "AI", "ok": true, "psi_input": { "ai_units": 120, "qos": 0.98 } },
      { "type": "QUANTUM", "ok": false, "error": "AttestationError" },
      { "type": "VDF", "ok": true, "psi_input": { "vdf_seconds": 2.3 } }
    ],
    "asBlock": false
  }
}

4.3 Optional Full Block Submission

Some clients submit full candidates:

{
  "op": "submitBlock",
  "id": 88,
  "params": {
    "jobId": "job_7f3b…",
    "block_cbor": "b64:…",             // core/types/Block via core/encoding/cbor.py
    "proofs_envelopes": [ … ]          // optional redundancy
  }
}

Success:

{
  "op": "ok",
  "id": 88,
  "params": { "accepted": true, "asBlock": true, "hash": "0x…" }
}


⸻

5) Heartbeats, Liveness & Reconnect
	•	Server emits ping every ~15s:

{ "op": "ping", "seq": 200, "params": { "ts": 1731091245 } }

	•	Client should promptly reply:

{ "op": "pong", "id": null, "params": { "ts": 1731091245 } }

	•	If no client traffic is seen within idleTimeoutSec (default 120s), server closes.
	•	On reconnect, client re-sends subscribe with resume (see §2.2).
The server replays the current work (if still valid); expired jobs must be discarded.

⸻

6) Difficulty / Target Updates

Server may push target updates (micro-target share ratio and Θ):

{
  "op": "target",
  "seq": 193,
  "params": {
    "thetaMicro": 5400000,
    "shareRatio": 0.0005,
    "ttlSec": 30
  }
}

Clients should adjust acceptance thresholds immediately.

⸻

7) Error Model

error frames always include a machine-readable code and optional data.

Code	Meaning	Typical data
InvalidRequest	Malformed JSON or missing fields	
InvalidJob	Unknown/expired jobId	
StaleShare	Template rolled over / TTL exceeded	{"expiredAt": 1731091200}
BelowTarget	d_ratio < shareRatio	{"d_ratio": 0.0002, "required":0.0005}
BadHashshare	Envelope/schema invalid; nullifier reuse	
AttachmentInvalid	Attachment failed verification	{"type":"AI","reason":"AttestationError"}
RateLimited	Token bucket exceeded	{"retryAfterMs": 500}
ServerBusy	Temporary overload	

Example:

{
  "op": "error",
  "id": 42,
  "params": {
    "code": "BadHashshare",
    "message": "schema mismatch: body_cbor",
    "data": { "hint": "proofs/schemas/hashshare.cddl" }
  }
}


⸻

8) Domains, Nullifiers & Replay Safety
	•	Nonces mix extranonce1, extranonce2, and nonceDomainTag (see mining/nonce_domain.py).
	•	All proofs use domain-separated nullifiers (see proofs/nullifiers.py).
	•	Duplicate (jobId, extranonce2, nonce) are quietly dropped (idempotent).

⸻

9) Security & DoS
	•	Node enforces per-connection and per-IP token buckets for submitShare.
	•	Attachments are size/time bounded; failures do not taint the HashShare unless policy requires.
	•	CORS is irrelevant for bare WS sockets, but the HTTP upgrader enforces origin allowlists when configured.
	•	Prefer wss:// on untrusted networks.

⸻

10) Worked Transcript

S → { "op":"hello", "seq":1, "params":{…,"sessionId":"sess…"} }
C → { "op":"subscribe", "id":1, "params":{"agent":"cpu-miner/1.0","features":["hashshare"]} }
S → { "op":"ok", "id":1, "params":{"extranonce1":"b64:V9sD2u2d","extranonce2Size":8,"targetHint":{"thetaMicro":5400000,"shareRatio":0.0005}} }
S → { "op":"work", "seq":184, "params":{"jobId":"job_7f3b…", "headerTemplate":{…}} }
C → { "op":"submitShare", "id":42, "params":{"jobId":"job_7f3b…","extranonce2":"b64:AAAAAAAB","nonce":"0x0000…","hashshare":{"envelope":{"type_id":0,"body_cbor":"b64:…","nullifier":"0x…"}}}}
S → { "op":"ok", "id":42, "params":{"accepted":true,"d_ratio":0.00123,"asBlock":false}}
S → { "op":"ping", "seq":185, "params":{"ts":1731091245} }
C → { "op":"pong", "params":{"ts":1731091245} }


⸻

11) Implementation Pointers
	•	Server ref: mining/ws_getwork.py
	•	Nonce/mix domain: mining/nonce_domain.py
	•	Share math: mining/share_target.py
	•	CPU scan loop: mining/hash_search.py
	•	Proof verifiers: proofs/*
	•	Policy roots / Θ: consensus/*

