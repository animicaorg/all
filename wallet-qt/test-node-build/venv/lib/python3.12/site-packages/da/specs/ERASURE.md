# Erasure Coding — RS Layout, Parameters & Padding Rules

This note specifies the Reed–Solomon (RS) layer used by Animica’s DA subsystem. RS increases robustness against partial withholding by allowing recovery of the original data from any `k` out of `n` fixed-size **shards**. The output shards are turned into **namespaced leaves** and committed in the NMT whose root is embedded in block headers.

> TL;DR: A blob is chunked into `k` equal-size data shards (padding with zeros deterministically), RS-encoded to `n` shards (`n ≥ k`), then laid out in a 2-D extended grid for efficient sampling. Shards are byte-identical across implementations.

---

## 1) Parameters & symbols

All parameters are network-configurable (see `da/config.py`):

- `k` — number of **data** shards (minimum required to recover)
- `n` — total **extended** shards (`n ≥ k`, parity shards = `n − k`)
- `S` — **shard size** in bytes (constant per network)
- `GF` — finite field for RS (default: GF(2^8) with a standard primitive polynomial)
- `LAYOUT` — extended matrix dimensions (see §4)
- `NS_BLOB` — namespace id used for all shards produced by a blob (data & parity)
- `PAD_BYTE` — `0x00` (only value allowed for padding)

**Constraints**

- `k`, `n`, and `S` are positive; `k ≤ n`.
- All writers MUST use the same generator matrix (see `da/erasure/reedsolomon.py`) and layout rules (§4).

---

## 2) Canonical chunking & padding (writer-side)

Given a blob of length `L` bytes:

1. **Split to data shards**  
   Compute the number of **full** data shards:

m = ceil(L / S)
assert m ≤ k, else split across multiple RS segments (see §2.4)

Fill `k` data shards `D[0..k-1]` with the blob bytes in order; if `m < k`, the trailing shards are all zeros.

2. **Shard padding**  
- The last non-empty shard is padded with `PAD_BYTE` (`0x00`) on the right to length `S`.
- All remaining data shards (if any) are entirely `0x00`.

3. **Parity shards**  
Run RS to produce parity shards `P[0..(n-k)-1]`. Each parity shard has length `S`.

4. **Multi-segment blobs (when `m > k`)**  
Large blobs MUST be processed as an ordered sequence of **segments**, each segment independently encoded with the same `(k,n,S)`:

segments = ceil(m / k)
for seg in range(segments):
take next k*S bytes (pad if needed) → RS → n shards

Segment boundaries are reflected in leaf positions via the **layout** (see §4) and in the **leaf length** inside the NMT leaf encoding (the original unpadded length for the segment’s last data shard; parity shards always report `len = S`).

**Determinism:** Padding is the *only* allowed source of trailing zeros. Writers MUST NOT compress or otherwise transform shard payloads.

---

## 3) RS codec details

We use a systematic RS code over GF(2^8):

- **Systematic**: `E[0..k-1] = D[0..k-1]` (data shards appear verbatim), and `E[k..n-1] = P[0..n-k-1]`.
- **Generator matrix**: Vandermonde form with a fixed primitive element and field polynomial, locked in `reedsolomon.py`.
- **Decoding**: Any set of `k` distinct shards (data or parity) is sufficient to reconstruct `D[0..k-1]`. We expose reference `encode()` and `decode()` methods; decoding rejects if the provided set is < `k` or if a syndrome check fails.

**Security note:** RS protects *availability*, not integrity. Integrity is provided by the NMT root; RS decoding MUST be paired with NMT range/inclusion proof checks when recovering selectively (§6).

---

## 4) Extended layout & sampling

To support efficient **Data Availability Sampling (DAS)**, we place encoded shards into a 2-D matrix:

- Let `R × C = n` be the matrix shape. Networks typically choose a near-square grid (e.g., `R = C = 128` for `n = 16384`). The exact `(R, C)` is fixed by network params and exported by `da/erasure/layout.py`.
- **Row-major index**: the `j`-th shard (`0 ≤ j < n`) maps to `(r, c)`:

r = j // C
c = j %  C

- **Segments**: If a blob requires multiple segments (see §2.4), segments occupy consecutive **row ranges**. Segment `s` uses rows `[s*R_seg, (s+1)*R_seg)`, where `R_seg = ceil(n / segments)` (exact formula defined in `layout.py`) so that sampling covers each segment fairly.
- **Leaves**: Each shard becomes **one NMT leaf** with the same namespace `NS_BLOB`. The leaf’s data field is the shard payload (length `S`, except the final data shard of the last segment which carries the exact original tail length in its `len` field).

**Sampling** chooses random `(r, c)` pairs; for each sample, the client fetches the corresponding leaf plus an NMT **inclusion** or **namespace-range** proof and verifies against the header DA root.

---

## 5) Leaf encoding (recap)

Each shard → leaf:

leaf = ns:u32_be (NS_BLOB)
|| len:uvarint  # for data shards of the final segment: original tail length (≤ S); else S
|| data:bytes(len) followed by implicit zero padding to S (writer-side only; not stored)

The encoded bytes in the NMT leaf are exactly `len` bytes; padding bytes are **not** serialized inside the leaf, but are assumed zero when reconstructing the original shard buffer for RS (this keeps hashing stable and avoids committing useless zeros).

---

## 6) Recovery & verification (reader-side)

To recover a blob (or a segment) reliably:

1. **Select k shards**: Obtain any `k` distinct leaves for the target segment (mix of data/parity).
2. **Verify each leaf** against the header DA root using its NMT proof. Reject on any failure.
3. **Rehydrate shard buffers** to length `S` by appending zeros if `len < S`.
4. **RS decode** to reconstruct `D[0..k-1]`.
5. **Trim trailing padding** using the known original tail length from the segment’s final data shard.

**Partial reads:** For small reads, clients may request only the rows/columns covering the byte range of interest, provided enough shards exist for RS decode.

---

## 7) Probability of undetected unavailability

Let `p` be the fraction of missing shards in a segment and `s` the number of independent uniform samples across that segment. If `p > (n-k)/n`, the segment is unrecoverable; DAS aims to detect such cases with high probability.

A simple bound:

Pr[miss not hit by any sample] = (1 - p)^s

Networks set `s` (and the distribution across rows/cols) so that:

(1 - p) ^ s  ≤  p_fail_target

See `da/sampling/probability.py` for the exact model used by Animica.

---

## 8) Edge cases & invariants

- **`L = 0`**: An empty blob is **not** permitted. Writers must reject or encode as a minimal, explicit marker at application level; DA requires at least one data shard.
- **Shard size mismatch**: Writers/Readers MUST use the network’s `S`. Any other size yields a different root and must be rejected.
- **Namespace uniformity**: All shards for a single blob share `NS_BLOB`; applications that need per-part namespaces should split into multiple blobs.
- **Deterministic layout**: Given `(k,n,S)` and the blob bytes, the set and order of leaves is uniquely determined (chunk → encode → lay out row-major). This guarantees a unique NMT root.

---

## 9) Worked example (illustrative)

Given: `k=4`, `n=6`, `S=1024`, blob length `L=2500`.

1. Data shards before RS:  
   - `D0 = bytes[0..1023]`  
   - `D1 = bytes[1024..2047]`  
   - `D2 = bytes[2048..2499] + 548 × 0x00`  
   - `D3 = 1024 × 0x00`
2. RS → parity `P0, P1`.
3. Place 6 shards row-major into an `R×C` grid (e.g., `2×3`), make 6 leaves with `len = 1024` except `D2` leaf which carries `len = 452` (the real payload length).
4. NMT commit → root in header.

A reader sampling row 0, col 2 verifies the leaf via NMT; to reconstruct, they collect any 4 distinct leaves, zero-pad to `S`, decode, then trim using `len` in `D2`.

---

## 10) References

- `da/erasure/reedsolomon.py` — canonical RS codec (encode/decode)
- `da/erasure/partitioner.py` — blob → segments/shards
- `da/erasure/layout.py` — extended grid mapping and indices
- `da/erasure/decoder.py` — reconstruction with NMT checks
- `da/nmt/*` — leaf codec, tree build/verify
- `da/schemas/*.cddl` — wire encodings

