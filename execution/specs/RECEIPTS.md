# RECEIPTS.md — Receipt fields, hashing, and bloom

This document specifies the canonical **transaction receipt** format, its **hashing**, and the **event bloom** for Animica. These rules are consensus-critical: any change that alters receipt bytes, receipt hash, logs bloom, or the receipts Merkle root is a hard fork.

Relevant code:
- `execution/types/{status.py,events.py,result.py}`
- `execution/receipts/{builder.py,encoding.py,logs_hash.py}`
- `execution/runtime/{event_sink.py,fees.py,executor.py}`
- `core/types/receipt.py` (wire type)
- `core/utils/{hash.py,merkle.py,serialization.py}`

---

## 1) Receipt fields

A receipt is produced for each transaction included in a block, in **transaction index order**.

Fields:

1. **status** — `uint8`  
   - `0` = `OOG` (Out Of Gas before any state-changing effect)  
   - `1` = `SUCCESS`  
   - `2` = `REVERT` (explicit `revert()` from VM/runtime)
2. **gasUsed** — `uint64`  
   Total gas charged for this tx (intrinsic + execution − final refunds), exact as metered by the runtime.
3. **logsBloom** — `bytes[256]` (2048-bit bloom)  
   Deterministically built from the tx’s **logs** (see §4).
4. **logs** — array of **LogEvent** in emission order  
   Each `LogEvent` has:
   - `address` — `bytes[32]` contract/account identifier (left-padded if shorter internally)
   - `topics` — array of `bytes[32]` (domain-separated event selectors & indexed args)
   - `data` — `bytes` (opaque, canonical bytes per ABI)

> Notes
> - Addresses on the wire are 32 bytes to keep hashing uniform and future-proof across identity schemes.
> - A tx that `REVERT`s may still emit **no** logs; `logsBloom` is then all zeros.

---

## 2) Canonical encoding (CBOR)

Receipts are encoded as a **CBOR map with fixed small integer keys** to guarantee stable ordering and compactness. Exact shape:

; Receipt (version 1)
receipt = {
1: status,                ; uint
2: gasUsed,               ; uint
3: logsBloom: bstr .size 256,
4: [* logEvent]           ; array of logEvent
}

logEvent = [
address: bstr .size 32,
topics:  [* bstr .size 32],
data:    bstr
]

Encoding rules:
- Keys **must** appear in ascending order `1..4`.  
- No extra keys. No CBOR tags. Integers are **major type 0** with **minimal encoding**.  
- Empty arrays are allowed (no logs / no topics).

> The CDDL above is mirrored in `execution/receipts/encoding.py` and validated by `execution/tests/test_receipts_hash.py`.

---

## 3) Receipt hash

The **receipt hash** is used when constructing the `receiptsRoot` (Merkle) for the block.

receiptBytes = CBOR(receipt)                          ; per §2
domain = “ANM-RECEIPT-V1”                             ; ASCII (no NUL)
receiptHash = SHA3-256( domain || 0x00 || receiptBytes )

- Domain separation prevents cross-type collisions with headers/tx/proofs.  
- The `0x00` byte is a separator to avoid accidental concatenation ambiguity.

---

## 4) Event logs bloom (2048-bit)

Animica uses a **2048-bit** bloom (256 bytes) to accelerate log filtering. The bloom is computed over:
- the `address` of each log, and
- every `topic` in that log.

### 4.1 Hash functions

We derive **three** bit positions per input (`address` or `topic`) from a single SHA3-256 digest:

digest = SHA3-256( b”ANM-LOG-BLOOM-V1” || 0x00 || tag || 0x00 || payload )
; tag = 0x01 for address, 0x02 for topic
; payload = 32-byte address OR 32-byte topic

; interpret digest as big-endian bits d[0..255]
i0 = (u16_from(d[0..15]))   mod 2048
i1 = (u16_from(d[16..31]))  mod 2048
i2 = (u16_from(d[32..47]))  mod 2048

Where `u16_from(bits[k..k+15])` is the unsigned 16-bit integer formed by those 16 bits.

### 4.2 Setting bits

- Start from a 256-byte zero array.  
- For each `input ∈ {address} ∪ topics`, set bits `i0`, `i1`, `i2` to `1`.  
- Bits are numbered `[0..2047]`, **bit 0** is the MSB of `byte 0`. (Big-endian bit order within a byte.)

This construction is deterministic, streamable, and avoids keccak/legacy coupling while remaining compatible with fast predicate checks.

---

## 5) Receipts root (Merkle)

Blocks commit to all receipts via a canonical binary Merkle tree:

- **Leaf**: `leaf = receiptHash` (32 bytes from §3)  
- **Node hash**: `H(a,b) = SHA3-256( 0x01 || a || b )` for ordered children `a,b`  
- **Leaf hash preimage tag**: `0x00` is **not** used at leaves (already domain-separated in §3)  
- **Odd count**: duplicate the last hash at each level (“pair with self”)  
- **Empty set**: `receiptsRoot = SHA3-256( "ANM-MERKLE-EMPTY" )`

> The same Merkle rules are used for other roots unless otherwise specified.

---

## 6) Determinism & indexing

- Logs are emitted **during** tx execution but are buffered by `runtime/event_sink` and committed to the receipt **in tx index order**.  
- `logsBloom` is computed from exactly those buffered logs; re-execution must reproduce byte-for-byte equality.  
- The **final block** `receiptsRoot` is the Merkle root over the **per-tx** `receiptHash` list ordered by transaction index.

---

## 7) Validation checklist (node must enforce)

1. CBOR map has exactly keys `{1,2,3,4}` in ascending order.  
2. `status ∈ {0,1,2}`; `gasUsed` minimally encoded uint.  
3. `logsBloom` length is exactly 256 bytes.  
4. Every `logEvent` is a 3-tuple `[address(32), topics(array of 32-byte), data(bytes)]`.  
5. Recompute bloom per §4 and compare to encoded `logsBloom`.  
6. Recompute `receiptHash` per §3 and include in the block receipts Merkle per §5.

---

## 8) Forward compatibility

- Future versions (V2+) will use a **new domain string** and may add fields under **new numeric keys**; old keys’ meaning stays identical.  
- Nodes must reject receipts that claim V1 format but fail any rule above.

---

## 9) Test vectors

- Golden vectors live in `execution/tests/test_receipts_hash.py` and `spec/test_vectors/txs.json`.  
- Each vector contains:
  - tx index, `status`, `gasUsed`, compacted `logs`, expected `logsBloom` (hex), and `receiptHash` (hex).  
  - A small multi-tx block vector also includes the expected `receiptsRoot`.

*End of spec.*
