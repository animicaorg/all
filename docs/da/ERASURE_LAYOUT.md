# Erasure Layout & Coding Rates

This document specifies how blobs are partitioned into fixed-size **shares**, grouped into **stripes**, extended with **Reed–Solomon (RS)** erasure coding, and then committed into the Namespaced Merkle Tree (NMT). It also provides recommended **coding rates** and explains the index math used by DAS and light clients.

> Reference implementation:  
> - Partitioner: `da/erasure/partitioner.py`  
> - RS codec: `da/erasure/reedsolomon.py`  
> - Layout helpers: `da/erasure/layout.py`  
> - Index math (leaf ↔ share): `da/nmt/indices.py`  
> - NMT build/verify: `da/nmt/*`  

---

## 1. Terms

- **Share**: A fixed-size chunk of blob bytes (`SHARE_SIZE`), the atomic unit for coding and sampling.  
- **Stripe**: A group of `k` **data shares** (padded as needed) that is extended to `n` **coded shares** via RS(k, n).  
- **Systematic code**: The first `k` coded shares equal the original data shares (identity), the last `n−k` are parity.  
- **Namespace**: An integer tag attached to every share/leaf; all shares for a blob carry the blob’s namespace.  
- **Extended shares**: The `n` coded shares (data + parity) per stripe that are inserted as NMT leaves.

---

## 2. Parameters & Limits

All values are **chain-configurable** (see `da/constants.py`, `da/erasure/params.py`):

- `SHARE_SIZE` (bytes): MUST be a power-of-two for alignment (e.g., 512, 1024, 2048).  
- `k` (data shares per stripe): 4–64 typical.  
- `n` (coded shares per stripe): MUST satisfy `n > k`.  
- `RATE = k/n` (coding rate): 0.4–0.8 typical; lower rate ⇒ more redundancy.

Implementations MUST expose these parameters via DA `getParams` or spec files so clients can validate proofs.

---

## 3. Canonical Partitioning

Given a blob `B` of `len(B)` bytes and namespace `ns`:

1. **Slice to data shares**  

data_shares = chunk(B, SHARE_SIZE)                 # last chunk may be partial
if len(data_shares) % k != 0:
pad_count = k - (len(data_shares) % k)
append pad_count zero-filled shares (length SHARE_SIZE)

The **original byte length** MUST be carried in the blob envelope to allow unambiguous trim on decode.

2. **Group into stripes**  

stripes = [ data_shares[i : i+k] for i in range(0, len(data_shares), k) ]

3. **RS extend each stripe (systematic)**  
For each stripe `S` of length `k`, compute `parity = RS_Encode(S, n-k)` to produce a length-`n` vector  
`coded = S || parity`.

4. **Serialize to NMT leaves (namespace-carrying)**  
Each coded share becomes a **leaf**:

leaf = encode_namespace(ns) || encode_varlen(SHARE_SIZE) || share_bytes

(Exact CBOR rules are defined in `da/nmt/codec.py`.)

5. **Append leaves in row-major stripe order**  
Stripes are appended in order; within a stripe, leaves are appended in **increasing column index** (0..n−1).

> **Determinism:** Zero-padding bytes MUST be literal zeros. No other padding markers are allowed.

---

## 4. 2D Intuition (Row/Col)

Although each blob is processed stripe-by-stripe, it is convenient to visualize a **row-major matrix**:

- Rows = stripes `r = 0..(R−1)` where `R = ceil(len(data_shares)/k)`.  
- Columns = coded share positions `c = 0..(n−1)` (0..k−1 data, k..n−1 parity).  

### Linear index mapping
Let `L` be the zero-based linear index of an **extended share** for this blob (before global namespace merge):

row(L)    = L // n
col(L)    = L %  n
linear(r,c) = r * n + c

Implementations MUST use these identities verbatim (see `da/nmt/indices.py`) so DAS clients and servers agree.

---

## 5. Global Ordering (Multi-blob Blocks)

Blocks may contain many blobs, possibly interleaved by distinct namespaces. The **global NMT leaf order** is:

1. For each blob, compute its extended leaves as above (row-major within the blob).  
2. **Sort** all blob-leaf sequences by `(namespace, blob_local_index)` and **concatenate**.  

This preserves namespace intervals, enabling efficient **namespace-range proofs**. A verifier MUST reject trees that violate the sorted-by-namespace invariant.

---

## 6. Decoding & Recovery

To recover a blob:

1. Collect any `k` coded shares *from each stripe* (they need not be contiguous).  
2. Verify each share’s **inclusion proof** against `da_root`.  
3. For blobs with dedicated namespace, optionally verify a **namespace-range proof** to assert completeness.  
4. Perform RS **decode** per stripe to reconstruct the original `k` data shares.  
5. Concatenate data shares (strip trailing zero padding) and truncate to the original byte length recorded in the envelope.

> **Note:** Decoding MUST be independent per stripe; no cross-stripe mixing is allowed.

---

## 7. Coding Rates (Guidance)

Let `RATE = k/n`, **redundancy** `ρ = 1 − RATE = (n − k)/n`, and **overhead factor** `1/RATE`.

| Profile     | Example (k/n) | Redundancy ρ | Overhead | Notes                                                  |
|-------------|----------------|--------------|----------|--------------------------------------------------------|
| Minimal     | 3/4            | 25%          | 1.33×    | Lower bandwidth, weaker availability                   |
| Balanced    | 8/12           | 33%          | 1.50×    | Common default; good trade-off                         |
| Robust      | 8/16           | 50%          | 2.00×    | Higher sampling success; costlier                      |
| High-robust | 16/32          | 50%          | 2.00×    | For harsh networks or large-blobs emphasis             |

**Choosing k/n:**  
- Larger `k` reduces per-stripe RS overhead but increases **stripe size = k * SHARE_SIZE** (affects latency).  
- Higher redundancy (lower RATE) raises availability at the cost of bandwidth and storage.

The **sampling module** (`da/sampling/probability.py`) provides helper functions to hit a target failure probability `p_fail` given chosen `(k,n)` and sample counts.

---

## 8. Merkle / NMT Interaction

- The **unit of commitment** is the *extended share leaf* (namespaced).  
- Inclusion proofs are computed over the NMT with node hashes:

H = SHA3-256( left.digest || right.digest || left.min_ns || right.max_ns )

(Formal definition in `da/specs/NMT.md` and `da/nmt/node.py`.)

- **Namespace-range proofs** cover the contiguous region of leaves for a namespace, using boundary nodes whose `(min_ns,max_ns)` brackets prove absence outside the range.

---

## 9. Serialization Details (Normative)

- `namespace` is encoded as fixed-width unsigned (width is network-defined; 32 or 64 bits).  
- `share length` field MUST equal `SHARE_SIZE` for all extended shares; partial data is encoded in the share body with zero tail padding.  
- All multi-byte scalars are **big-endian**.  
- Leaves MUST be CBOR-encoded as per `da/schemas/nmt.cddl` with canonical map ordering (if maps are present).

Verifiers MUST reject any deviation from these rules.

---

## 10. Worked Example

**Parameters:** `SHARE_SIZE=1024`, `k=8`, `n=12` (RATE=2/3).  
**Blob:** `len=10_000 bytes`, `ns=24`.

1. `data_shares = ceil(10000/1024) = 10` shares  
2. Pad to multiple of `k=8` ⇒ `10 → 16` data shares (add 6 zero shares)  
3. Stripes: `R = 16/8 = 2` (each 8 shares)  
4. RS extend each stripe to 12 shares ⇒ total extended shares = `R * n = 24` leaves  
5. Append leaves: stripe 0 col 0..11, then stripe 1 col 0..11  
6. Merge with other blobs by `(namespace, blob_local_index)` and compute `da_root`.

A light client sampling random indices among the 24 leaves for this blob needs any **8 per stripe** to reconstruct.

---

## 11. Security Considerations

- **Systematic RS**: Using systematic codes simplifies auditability (data shares are visible), but parity-only subtrees remain indistinguishable commitment-wise.  
- **Padding**: Padding MUST be zero bytes and MUST NOT be omitted in leaf serialization.  
- **Namespace abuse**: Incorrect namespace assignment breaks range proofs; validators MUST enforce namespace ranges at admission time.  
- **Consistency**: Nodes MUST reject blocks where `(k,n,SHARE_SIZE)` differ from declared network parameters for the committed `da_root`.

---

## 12. Compliance Checklist

An implementation is conformant if it:

- [ ] Uses fixed `SHARE_SIZE` and constant `(k,n)` from chain params.  
- [ ] Zero-pads the final data stripe to a multiple of `k`.  
- [ ] Uses **systematic** RS to produce `n−k` parity shares.  
- [ ] Emits extended shares as **namespaced NMT leaves** in strict row-major order.  
- [ ] Globally orders all leaves by **namespace** (stable within blob-local order).  
- [ ] Provides correct index math for `(row,col) ↔ linear`.  
- [ ] Verifies inclusion and namespace-range proofs against the **DA root**.  
- [ ] Records original byte length in the blob envelope for safe trim on decode.

---

## 13. References

- `da/specs/ERASURE.md` — RS layout & parameters (formal)  
- `da/specs/NMT.md` — NMT node/leaf encodings and proof forms  
- `da/sampling/*` — sampling strategies and probability math  
- `da/retrieval/api.py` — REST interface for posting/fetching blobs and proofs

