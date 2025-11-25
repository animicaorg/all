# Receipts & Events — structure, topics, bloom

This document specifies the canonical **transaction receipt** format and the **event log** subsystem used by Animica. It aligns with the reference implementation in:

- `execution/state/receipts.py` — build receipts & bloom
- `execution/runtime/event_sink.py` — event capture & ordering
- `execution/receipts/logs_hash.py` — logs Merkle / bloom helpers
- `rpc/models.py` — JSON-RPC views for receipts & logs

> Design goals: deterministic encoding, stable topic hashing for efficient filtering, and light-client friendliness (per-receipt logs Merkle root).

---

## 1) Transaction Receipt

A receipt summarizes the effect of a single transaction.

### 1.1 Canonical fields (consensus)

| Field       | Type         | Meaning                                                |
|-------------|--------------|--------------------------------------------------------|
| `status`    | `u8`         | 1 = `SUCCESS`, 2 = `REVERT`, 3 = `OOG` (out of gas)   |
| `gasUsed`   | `u64`        | Gas actually consumed (incl. intrinsic & refunds)     |
| `logs`      | `LogEvent[]` | Ordered list of events emitted by the transaction     |

> Receipts are encoded as **canonical CBOR** with deterministic map ordering and length-prefix rules (see `docs/spec/ENCODING.md`). The receipt’s byte representation participates in the **ReceiptsRoot** Merkle in the block header.

### 1.2 Derived / view fields (non-consensus but normative)

Implementations additionally derive:

| Field          | Type         | How it’s computed                                           |
|----------------|--------------|-------------------------------------------------------------|
| `logsBloom`    | `bytes(256)` | 2048-bit bloom filter (bitwise-OR of the tx’s log elements)|
| `logsRoot`     | `Hash32`     | Merkle root over `logs` (leaf = `H(LogEventEnc)`)          |

- `logsBloom` and `logsRoot` are exposed by RPC and used by light/graph indexing. The block header contains an **aggregate logs bloom** (bitwise OR of all tx `logsBloom`), enabling efficient bloom prefiltering at the block level.

---

## 2) Event Logs

### 2.1 High-level VM surface

Contracts emit events via the VM stdlib:

```python
# vm_py/stdlib/events.py
emit(name: bytes, args: dict, indexed: list[str] | None = None) -> None

	•	name: symbolic event name (arbitrary bytes, recommended ASCII)
	•	args: map of field name → ABI-encodable value (see vm_py/abi/*)
	•	indexed: optional list of arg names to lift into topics (max TOPICS_MAX-1, default heuristic below)

The runtime transforms this into a low-level LogEvent.

2.2 Low-level LogEvent (consensus)

Field	Type	Meaning
address	Address	Emitter contract (32-byte payload in-consensus; bech32 at the edge)
topics	Hash32[]	Up to TOPICS_MAX (default 4). topics[0] is the event id.
data	bytes	Canonical ABI encoding of {name, args} (see §2.4).

LogEvent is encoded as canonical CBOR (array form for compactness) as:

[ address: bstr(32)
, topics: [ bstr(32) * N ], N ≤ TOPICS_MAX
, data: bstr
]

2.3 Topic derivation
	•	topics[0] = keccak256(name). (Name is used as provided by the contract.)
	•	Additional topics are taken from indexed args in key order (stable, ascending by UTF-8 bytes).
For each indexed value v:
	•	If v is a scalar ≤32 bytes (e.g., u256, bytes<=32, address), the 32-byte right-padded ABI form is used (no hashing).
	•	Otherwise (e.g., long bytes, strings, lists, maps), use keccak256(ABI(v)).
	•	If indexed is omitted, the host indexes up to 3 scalar args (deterministically selected by ascending key) and hashes non-scalars as above, until the topic budget is exhausted.

Constants: TOPICS_MAX = 4 (configurable via spec/params.yaml).

2.4 data payload

data = ABI.encode({ "name": name, "args": args }) where:
	•	name is the original bytes.
	•	args is a sorted map by key (UTF-8 ascending). Values use the canonical Animica ABI (length-prefixed for dynamic types). See vm_py/abi/encoding.py and docs/spec/ENCODING.md.

This ensures:
	•	Deterministic decoding across languages (SDKs).
	•	Full event contents are available even when only a subset is indexed into topics.

2.5 Ordering guarantees
	•	Logs are recorded in the order they are emitted within a transaction.
	•	Cross-transaction ordering is (tx_index, log_index) within the block.
	•	These indices are stable in RPC responses and used by indexers.

⸻

3) Bloom Filter

We use a 2048-bit (256-byte) bloom compatible with the common EVM-style triple-hash scheme.

3.1 Elements inserted

For each LogEvent:
	•	The emitter address
	•	Each topic value (the final 32-byte topic words)

data bytes are not included in the bloom.

3.2 Bit positions

For an element x, compute k=3 positions by taking keccak256(x) and slicing three disjoint 11-bit indices:

h = keccak256(x)  // 32 bytes
i0 = h[0..1] & 0x07FF
i1 = h[2..3] & 0x07FF
i2 = h[4..5] & 0x07FF

Set those bits in the 2048-bit vector (little-endian within bytes).
	•	Per-receipt bloom: OR of the event elements within the tx.
	•	Per-block bloom: OR of all per-receipt blooms.

Bloom is used as a probabilistic prefilter; indexers must confirm matches by decoding logs.

⸻

4) Merkle roots

4.1 logsRoot (per receipt)
	•	Leaf: leaf_i = H( LogEventEnc_i ) using sha3_256.
	•	Tree: canonical binary Merkle with left||right concatenation and sha3_256 at internal nodes.
	•	logsRoot = MerkleRoot( leaf_0..leaf_{n-1} ), or H(0x00) for empty logs.

4.2 ReceiptsRoot (per block)
	•	Leaf: H( ReceiptEnc_i ) (the consensus subset: {status, gasUsed, logs}).
	•	The block header’s receiptsRoot commits to all receipts.
	•	Light clients can verify:
	1.	A receipt inclusion via ReceiptsRoot, then
	2.	A specific log via the receipt’s logsRoot.

⸻

5) JSON-RPC representations

RPC returns views with friendly hex/JSON:
	•	status → number (1/2/3) and statusText
	•	gasUsed → decimal string
	•	logsBloom → hex 0x… (256 bytes)
	•	logsRoot → hex 0x…32
	•	logs[] items:
	•	address → bech32m (anim1…) and raw hex payload
	•	topics[] → 0x…32 each
	•	data → 0x…

See rpc/models.py for exact shapes. SDKs (Python/TS/Rust) mirror these types.

⸻

6) Limits & costs
	•	Max logs per tx: network param, typical default 1024.
	•	Max topics per log: TOPICS_MAX = 4.
	•	Max data per log: bounded by tx gas; soft cap in policy may apply.

Gas model (subject to network constants):
	•	Per-log base: G_log
	•	Per-topic: G_topic
	•	Per-byte of data: G_log_data

Refer to execution/specs/GAS.md and spec/params.yaml for constants.

⸻

7) Examples

7.1 Counter contract

# Contract
events.emit(b"Inc", {"by": 1, "new": 42}, indexed=["new"])

Topics:
	•	t0 = keccak256("Inc")
	•	t1 = abi(u256(42))  # right-padded 32 bytes

Data encodes:

{name: b"Inc", args: {"by": 1, "new": 42}}

7.2 Transfer

events.emit(b"Transfer", {"from": A, "to": B, "value": 1000}, indexed=["from", "to"])

Topics:
	•	t0 = keccak256("Transfer")
	•	t1 = abi(address(A))
	•	t2 = abi(address(B))
Data contains all fields including value.

⸻

8) Test vectors & conformance
	•	execution/tests/test_events_logs.py — order, bloom, topics
	•	execution/tests/test_receipts_hash.py — encoding & logsRoot
	•	SDK fixtures under sdk/common/test_vectors/abi_examples.json

Compliance checklist:
	•	Deterministic CBOR encoding for receipts and logs
	•	Topics derivation rules (t0 = name hash; indexed mapping)
	•	2048-bit bloom, k=3, address + topics only
	•	logsRoot computation matches reference
	•	RPC view round-trips and SDK types align

⸻

9) Backwards/forwards compatibility
	•	New fields MUST be appended and/or guarded by versioned encoding tags.
	•	Indexers MUST ignore unknown log data keys.
	•	Topic rules are stable; changing hash function or TOPICS_MAX is a hard fork.

⸻

10) Security notes
	•	Avoid leaking secrets in data; logs are public.
	•	Event names should be collision-resistant (use clear, unique names).
	•	Contracts should index addresses and primary keys to benefit from bloom prefiltering.

