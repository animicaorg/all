# Canonical Error Codes — RPC, Mempool, Execution, Consensus, DA, P2P, ZK, AICF, Randomness, Capabilities

This document defines **stable, machine-readable** error identifiers used across Animica components:

- Transport surfaces: **JSON-RPC** (node), **REST** (studio-services, DA endpoints)
- Subsystems: **mempool**, **execution**, **consensus**, **proofs/zk**, **data-availability**, **p2p**, **capabilities**, **aicf**, **randomness**

It standardizes:
1) **Domain + name** (human-stable),
2) **Numeric code** (stable integer),
3) **HTTP mapping** (for REST),
4) **JSON-RPC representation** (for node RPC).

> The same canonical code appears everywhere (logs, metrics, SDK errors), making it easy to correlate failures.

---

## 0) Terminology & wire format

### 0.1 Canonical tuple

Every application error is defined by a **triple**:

- `domain`: one of  
  `rpc | mempool | exec | consensus | proofs | zk | da | p2p | caps | aicf | rand`
- `name`: stable PascalCase symbol (e.g., `FeeTooLow`)
- `numeric`: stable positive integer (see ranges below)

### 0.2 JSON-RPC error envelope (node)

For **transport-level** JSON-RPC issues, use **standard codes**; for **application** errors, use `-32000` and carry the canonical tuple in `data.anm_error`.

```jsonc
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32000,                        // JSON-RPC server error
    "message": "mempool/FeeTooLow",
    "data": {
      "anm_error": {
        "domain": "mempool",
        "name": "FeeTooLow",
        "numeric": 3001
      },
      "hint": "Raise maxFeePerGas to >= 12 gwei",
      "context": { "floor": "12gwei", "tip": "1gwei" }
    }
  }
}

Standard JSON-RPC codes (transport only):

JSON-RPC code	Meaning
-32700	Parse error
-32600	Invalid request
-32601	Method not found
-32602	Invalid params
-32603	Internal error

Do not overload standard codes for domain errors; keep application errors at -32000 with anm_error populated.

0.3 REST/HTTP mapping (studio-services, DA, etc.)

REST endpoints MUST return an object with anm_error and an HTTP status:

{
  "error": "mempool/FeeTooLow",
  "anm_error": { "domain": "mempool", "name": "FeeTooLow", "numeric": 3001 },
  "hint": "Raise maxFeePerGas to >= 12 gwei"
}


⸻

1) Code space & ranges

Range	Domain	Notes
1000–1099	rpc	JSON-RPC application-layer (not transport)
3000–3099	mempool	Admission / replacement / fee market
4000–4099	exec	Execution VM/state errors
5000–5099	consensus	Scoring/Θ/Γ/policy/nullifiers
6000–6099	proofs	AI/Quantum/Storage/VDF envelopes
6100–6199	zk	Groth16/PLONK/STARK verification
7000–7099	da	Data availability / NMT / erasure
8000–8099	p2p	Handshake / rate limit / protocol
9000–9099	caps	Contract syscalls (blob/compute/zk/random)
9100–9199	aicf	Provider registry/staking/settlement
9200–9299	rand	Commit–reveal/VDF/round lifecycle

When adding, pick the lowest unused number in the correct range and update this spec + tests.

⸻

2) Canonical catalog

2.1 rpc (1000–1099)

Numeric	Name	HTTP	When
1000	ChainIdMismatch	400	Submitted tx chainId != node chainId
1001	InvalidTx	400	CBOR/shape/signature invalid (pre-check)
1002	RateLimited	429	Method/IP exceeded token bucket
1003	CorsRejected	403	Origin not allowed
1004	TxTooLarge	413	Raw tx exceeds size limits
1005	Unavailable	503	Backend store temporarily unavailable

2.2 mempool (3000–3099)

Numeric	Name	HTTP	When
3000	AdmissionError	400	Generic admission failure
3001	FeeTooLow	400	Below dynamic floor / watermark
3002	NonceGap	409	Missing earlier nonces for sender
3003	ReplacementError	409	RBF threshold not met (tip/fee delta too small)
3004	Oversize	413	Gas/bytes exceed configured maxima
3005	DoSError	429	Per-peer/global ingress limit hit
3006	InsufficientBalance	400	Cannot afford max cost
3007	DuplicateTx	409	Already in pool or chain

2.3 exec (4000–4099)

Note: Reverts/OOG during execution result in Receipt.status (SUCCESS/REVERT/OOG) and are not returned as JSON-RPC transport errors. Use these codes for pre-exec validation or REST simulation endpoints.

Numeric	Name	HTTP	When
4000	ExecError	500	Unhandled execution engine failure
4001	OOG	200*	Receipt.status = OOG (view-only mapping)
4002	Revert	200*	Receipt.status = REVERT (view-only)
4003	InvalidAccess	400	Access list / capability violation
4004	StateConflict	409	Optimistic scheduler conflict

* For REST simulate endpoints you MAY respond 400 with exec/Revert if a non-200 is desired for pipelines.

2.4 consensus (5000–5099)

Numeric	Name	HTTP	When
5000	ConsensusError	500	Generic validation failure
5001	PolicyError	400	PoIES policy root/caps mismatch
5002	ThetaScheduleError	400	Difficulty/Θ schedule invalid
5003	NullifierError	409	Proof nullifier reuse
5004	HeaderInvalid	400	Header linkage/roots invalid

2.5 proofs (6000–6099)

Numeric	Name	HTTP	When
6000	ProofError	400	Malformed/failed proof verification
6001	AttestationError	400	TEE/QPU attestation invalid
6002	SchemaError	400	Envelope schema/cddl mismatch
6003	NullifierReuseError	409	Duplicate nullifier

2.6 zk (6100–6199)

Numeric	Name	HTTP	When
6100	SchemeUnsupported	400	VK/proof scheme not supported
6101	VkNotFound	404	Circuit id not present in VK cache
6102	VkHashMismatch	409	VK hash doesn’t match pinned registry
6103	VerifyFailed	400	Groth16/PLONK/STARK verification failed
6104	FormatError	400	snarkjs/plonkjs/FRI JSON shape invalid

2.7 da (7000–7099)

Numeric	Name	HTTP	When
7000	DAError	500	Generic DA failure
7001	NotFound	404	Blob/commitment missing
7002	InvalidProof	400	NMT/availability proof invalid
7003	NamespaceRangeError	400	Namespace range invalid
7004	PayloadTooLarge	413	Blob exceeds configured limits

2.8 p2p (8000–8099)

Numeric	Name	HTTP	When
8000	P2PError	500	Generic peer failure
8001	HandshakeError	403	Kyber handshake / identity mismatch
8002	RateLimitError	429	Per-peer/topic token bucket
8003	ProtocolError	400	Frame/schema violation

2.9 caps (9000–9099)

Numeric	Name	HTTP	When
9000	CapError	400	Syscall envelope invalid
9001	NotDeterministic	400	Input length/shape violates determinism rules
9002	LimitExceeded	413	Bytes/units exceed configured caps
9003	NoResultYet	409	Result not available until next block
9004	AttestationError	400	Off-chain result attestation invalid

2.10 aicf (9100–9199)

Numeric	Name	HTTP	When
9100	AICFError	500	Generic AICF failure
9101	RegistryError	400	Provider registration/attestation invalid
9102	InsufficientStake	403	Provider below minimum stake
9103	JobExpired	410	Job TTL elapsed
9104	LeaseLost	409	Provider lost lease
9105	SlashEvent	403	Provider slashed

2.11 rand (9200–9299)

Numeric	Name	HTTP	When
9200	RandError	500	Generic randomness failure
9201	CommitTooLate	400	Commit outside window
9202	RevealTooEarly	400	Reveal before window
9203	BadReveal	400	Reveal doesn’t match commitment
9204	VDFInvalid	400	VDF proof invalid


⸻

3) Decision matrix — error vs. receipt failure
	•	Transport/Admission failures (e.g., malformed tx, fee too low): JSON-RPC error with -32000 and canonical anm_error.
	•	Execution result failures (REVERT, OOG): successful RPC result with Receipt.status set; do not return JSON-RPC error.
	•	Lookup misses (tx/receipt not found): rpc/NotFound (use HTTP 404, numeric 1006 if needed).
	•	Rate limiting: rpc/RateLimited (429) or p2p/RateLimitError as applicable.

⸻

4) SDK mapping (recommended)

SDKs SHOULD surface a typed error object:

type AnmError = {
  domain: 'mempool'|'exec'|'consensus'|'zk'|'da'|'p2p'|'caps'|'aicf'|'rand'|'rpc';
  name: string;
  numeric: number;
  message?: string;
  hint?: string;
  context?: Record<string, unknown>;
}

For JSON-RPC responses with error.code === -32000, parse error.data.anm_error into AnmError.

⸻

5) Extending the catalog
	1.	Pick a domain and allocate the next free numeric within its range.
	2.	Add the row to this document.
	3.	Update:
	•	rpc/errors.py (message map, HTTP mapping if served via REST)
	•	Domain error enums/classes (e.g., mempool/errors.py)
	•	SDKs (optional constants)
	•	Tests that assert numeric and domain/name stability
	4.	Include a migration note in docs/CHANGELOG.md.

Do not repurpose or renumber existing codes. If semantics change, add a new code.

⸻

6) Examples

6.1 Mempool fee too low (JSON-RPC)

error.code     = -32000
error.message  = "mempool/FeeTooLow"
data.anm_error = { domain: "mempool", name: "FeeTooLow", numeric: 3001 }

6.2 Tx revert (successful call with failure status)

result.receipt.status = 2   // REVERT
result.receipt.logs    = [...]

6.3 DA blob not found (REST)

HTTP 404, body:

{
  "error": "da/NotFound",
  "anm_error": { "domain": "da", "name": "NotFound", "numeric": 7001 }
}


⸻

7) Reserved / future
	•	rpc/NotFound = 1006 (optional), HTTP 404, for generic “object not found” on lookup methods.
	•	Additional zk/* for multi-open KZG, batching, recursive SNARKs.
	•	Additional consensus/* for alpha tuner / fairness windows.

⸻

