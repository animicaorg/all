---
title: "Animica L2 — Data Availability"
description: "The DA blob the L2 publishes for every batch so that any independent node can rebuild the ledger from published data alone."
group: "l2"
order: 4
draft: false
---

*Source: `docs/l2/DATA_AVAILABILITY.md` — this page mirrors the repository documentation.*

`l2/da.py`. A state root alone lets nobody rebuild the ledger. For every batch
the L2 publishes a **DA blob** from which any independent node can
deterministically reconstruct the exact transaction list and therefore
re-derive `new_state_root` by re-execution. DA is the core of the trust
model ([SECURITY_ASSUMPTIONS.md](/docs/l2/security-assumptions)) and of the
forced-exit escape hatch ([FORCED_EXITS.md](/docs/l2/forced-exits)).

## Encoding pipeline

1. **Address dictionary.** Every distinct 32-byte address in the batch is
   emitted once; inside each tx, addresses are replaced by a small varint
   dictionary index. Real payment traffic reuses merchants/providers heavily,
   so this is a large win on its own.
2. **Dictionary-aware tx encoding.** Each tx is re-encoded with the same
   minimal varints as the wire codec but with dictionary indices for
   addresses. Signatures (3309 B) and pubkeys (1952 B) are kept **inline** —
   they are needed to re-verify every signature on reconstruction; deduping
   pubkeys is noted future work.
3. **zlib compression** over the concatenation. zstandard would do slightly
   better, but zlib is stdlib, so the protocol has zero external DA
   dependency; the interface is stable if the codec is ever swapped.

Framing: the uncompressed body is
`ANML2DA1 || varint(dict_len) || addresses || varint(tx_count) || length-prefixed txs`.

## The commitment

```
data_root = sha3_256(uncompressed_body)     # body starts with the ANML2DA1 magic
```

Computed over the *uncompressed* body so the commitment is independent of the
compressor. `data_root` is a field of the batch header and of the proof's
public inputs, binding the blob to the committed state transition.

## API

| Function | Contract |
|---|---|
| `encode_batch(txs, level=6) -> (blob, data_root)` | Compress; raises if the blob exceeds `MAX_BATCH_BYTES` (64 MiB) |
| `decode_batch(blob) -> [L2Tx]` | **Strict** reconstruction — any structural problem (bad magic, out-of-range dictionary index, trailing bytes) raises `CodecError`; a bad blob can never yield a partial replay |
| `data_root_of(txs) -> bytes` | Commitment without compressing |
| `verify_blob(blob, expected_root) -> bool` | The availability check anyone can run against published DA: decompresses, checks magic + hash, and fully decodes |
| `compression_ratio(txs) -> float` | raw-wire-bytes / compressed-DA-bytes, reported for observability |

## Publication

DA blobs and state-root commitments are anchored to Animica L1 as transactions
to the bridge address carrying the `ANML2C1` memo magic (deposits use
`ANML2D1`). Below L1 height `FORK_L2_ANCHOR_HEIGHT = 80_000` these are opaque
memos indexed off-consensus; at/above the fork they are consensus-interpreted
by 10.0.0 full nodes. Blobs are also served directly by the node via
`l2_getBatchData(number)` and stored durably per batch by `l2/store.py`.

## Why PQ signatures make DA the budget

An ML-DSA-65 user tx is ~5.3 KB on the wire, dominated by signature + pubkey.
The dictionary + zlib pipeline compresses batch bodies substantially
(signatures themselves are high-entropy and do not compress; structure and
addresses do), and the fee schedule charges `da_per_byte` on the encoded size
precisely because DA bytes are the scarce anchored resource — see
[FEES.md](/docs/l2/fees).
