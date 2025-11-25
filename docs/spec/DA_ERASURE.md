# DA_ERASURE — Erasure Coding Layout & Sampling Guarantees

This document specifies Animica’s **Data Availability (DA)** erasure-coding scheme and the **probabilistic guarantees** achieved by Data Availability Sampling (DAS). It complements:
- `docs/spec/MERKLE_NMT.md` (NMT commitments & proofs)
- `da/erasure/*` (parameters, encoder/decoder, layout)
- `da/sampling/*` (samplers, probability math, light-client verify)
- Schemas: `da/schemas/{blob.cddl,nmt.cddl,availability_proof.cddl}`

Authoritative implementation:
- `da/erasure/params.py` — (k, n) profiles, shard size, padding rules  
- `da/erasure/partitioner.py` — split blob → shares  
- `da/erasure/reedsolomon.py` — RS(k, n) reference encoder/decoder  
- `da/erasure/encoder.py` — pipeline: chunk → erasure → namespaced leaves  
- `da/erasure/layout.py` — row/col layout for extended matrices  
- `da/sampling/{sampler.py,probability.py,light_client.py}` — DAS logic

Test vectors: `da/test_vectors/{erasure.json,availability.json}`

---

## 1) Goals & threat model

**Goals**
- Increase **retrievability** of large blobs using Reed–Solomon (RS) codes.
- Allow **light clients** to probabilistically detect data withholding with **few random samples**, verifying each sample against the **NMT root** committed in the block header (`daRoot`).
- Keep construction **deterministic** and **streaming-friendly**.

**Adversary model**
- An adversary may withhold an **arbitrary subset** of encoded shares (from single cells to whole rows/columns/regions).
- Honest verifiers can query random samples (shares) over the network, each accompanied by a **NMT inclusion proof**.
- Hash function is collision-resistant (SHA3-256), and RS decoding assumptions hold for stated parameters.

---

## 2) Parameters

Unless network params override:

- **Code**: Reed–Solomon over GF(2^8)  
- **Axis-wise coding**: 2-D extension (rows **and** columns)  
- **Per-axis dimensions**:  
  - `k_axis` data symbols → `n_axis` total symbols (systematic RS), with **extension factor** `λ = n_axis / k_axis ≥ 2`.  
  - **Default**: `λ = 2` (e.g., `k_axis = K`, `n_axis = 2K`).  
- **Matrix sizes**:  
  - Original data grid: `k_rows × k_cols`  
  - Extended matrix: `n_rows × n_cols` (`n_rows = λ·k_rows`, `n_cols = λ·k_cols`)
- **Share (shard) size**: `S` bytes (fixed; see `da/erasure/params.py`)  
- **Padding**: Zero padding to fill the last chunk; padding is **committed** (see §4.3).

> The exact `(k_rows, k_cols, n_rows, n_cols, S)` come from the chain’s `ChainParams` and DA config. All choices are exercised by vectors.

---

## 3) Blob → shares → matrix

### 3.1 Partitioning
A blob is split into **shares** of size `S` bytes:

shares = chunk(blob, S)           # last chunk padded with zeros if needed

Each share is labeled with a **Namespace Id** `ns` (see NMT spec). For single-blob posting, all shares carry the same `ns`. For multi-blob packs, shares are **namespace-sorted** before encoding (see §4).

### 3.2 Arrange into data grid
The `shares` are filled row-major into a `k_rows × k_cols` data grid:

for r in [0..k_rows-1]:
for c in [0..k_cols-1]:
data[r][c] = next_share_or_zero_pad()

> Padding is **explicit** and included in commitments, ensuring canonical roots.

---

## 4) 2-D Reed–Solomon extension

### 4.1 Row extension
For each row `r`, apply **systematic RS(k_cols → n_cols)** to obtain parity:

row_ext[r] = RS_encode_row( data[r][0..k_cols-1] )  # length n_cols

Concatenate to form a `k_rows × n_cols` rectangle.

### 4.2 Column extension
Treat each **column** of the row-extended rectangle as a vector and apply **systematic RS(k_rows → n_rows)** to obtain the final `n_rows × n_cols` matrix:

for c in [0..n_cols-1]:
col = [ row_ext[r][c] for r in 0..k_rows-1 ]
col_ext = RS_encode_col(col)            # length n_rows
for r in [0..n_rows-1]:
matrix[r][c] = col_ext[r]

This 2-D construction provides resilience to structured erasures (e.g., whole rows/columns), not just random losses.

### 4.3 Namespaces & ordering
- Every encoded share **inherits the namespace** of the source blob.  
- For **multi-blob** blocks, blobs are **sorted by namespace**, and their matrices are concatenated **row-major** before building the NMT.  
- The NMT requires **non-decreasing namespace order** across leaves, which this process preserves.

### 4.4 Index mapping (deterministic)
The leaf index `i ∈ [0, n_rows·n_cols-1]` maps to `(r, c)`:

r = i // n_cols
c = i %  n_cols

Helpers live in `da/nmt/indices.py`. All proofs and samplers use the same mapping.

---

## 5) Commitments & proofs

### 5.1 NMT root
The `n_rows × n_cols` matrix (linearized **row-major**) is serialized into **NMT leaves** (`namespace || length || data`) and committed into a **single NMT root** (`daRoot`) placed in the block header. See `MERKLE_NMT.md`.

### 5.2 DAS proof (per sample)
For each sampled position `i`:
- The prover returns the **share bytes** and an **NMT inclusion proof** proving that the share is committed by `daRoot`.
- Optional: small metadata `(r, c)` to help stratified samplers; it can be derived from `i`.

The light client:
1) Recomputes the NMT path to `daRoot`.  
2) Accepts the sample if the proof is valid and byte payload is well-formed.

---

## 6) Availability & decoding thresholds (informative)

With RS(k→n) per axis, **any** `k_cols` symbols per row suffice to reconstruct **that row**; similarly, **any** `k_rows` symbols per column suffice to reconstruct **that column** in the row-extended half. For the **full 2-D** recovery, the usual pipeline is: recover enough columns to reduce to a `k_rows × n_cols` rectangle, then recover rows (or vice-versa). In practice, honest full nodes fetch **all** shares; light clients only sample.

---

## 7) Data Availability Sampling (DAS)

### 7.1 Uniform random sampling
Let:
- `M = n_rows · n_cols` total shares,
- attacker withholds `W` shares (arbitrary positions),
- withheld fraction `f = W / M`,
- a light client draws `s` **independent** random samples (with replacement) uniformly over `[0..M-1]`.

**Failure probability** (no withheld share hit):

p_fail = (1 - f)^s  ≤  exp(-f · s)

To achieve target `ε`:

s ≥ ln(1/ε) / f

**Example**: If an attacker withholds `f = 10%` of shares and we want `ε = 10^-9`, then  
`s ≥ ln(1e9)/0.1 ≈ 207 / 0.1 = 2070` samples.

> The implementation uses numerically stable bounds from `da/sampling/probability.py`.

### 7.2 Structured withholding (rows/columns/blocks)
For a withheld **submatrix** of size `h × w` (`W = h·w`, same `f`), the same bound applies for **uniform sampling**. However, **stratified** or **2-D** sampling improves worst-case detection (see below).

### 7.3 Stratified 2-D sampling
To counter adversaries who cluster erasures:
- Sample `s_r` random **rows** and within each, pick `t_r` random columns.
- Sample `s_c` random **columns** and within each, pick `t_c` random rows.

Total samples `s = s_r·t_r + s_c·t_c`. This increases the chance of intersecting **every bad row/column**. A conservative bound for missing all `h` bad rows with `s_r` row picks:

Pr[miss all bad rows] = C(n_rows - h, s_r) / C(n_rows, s_r)  ≤  (1 - h/n_rows)^{s_r}

Analogous for columns. The implementation offers **uniform** and **stratified** modes (`da/sampling/queries.py`).

### 7.4 Practical sizing
A simple policy:
- Estimate **lower-bound f_min** for economically rational attacks (e.g., ≥ 1/8 withholding).  
- Choose `ε` (e.g., `2^-40`) and compute `s = ceil( ln(1/ε) / f_min )`.  
- Distribute samples across stratified row/column queries for robustness.  
- Cache accepted samples for the same `daRoot` to amortize cost.

---

## 8) Light client verification flow

1) Read `daRoot` from header.  
2) Build a random **query plan** (uniform or stratified).  
3) For each sample index `i`:
   - Request `(share_bytes, proof)` from peers/DA service.
   - Verify NMT proof against `daRoot`.  
   - If any proof fails or data malformed → **reject** availability.
4) If at least one sample hits a withheld region → **reject** (unavailable).  
5) Otherwise, after `s` successful samples, accept with residual risk `p_fail`.

### 8.1 Batching & parallelism
Samplers issue batched queries with timeouts/retries (see `da/sampling/sampler.py`), and can **mix providers** to reduce correlated failures.

---

## 9) Encoding/decoding details

- **Systematic** RS is used; data symbols appear verbatim in the first `k_axis` positions per axis.  
- **Generator points** and field params are pinned in `da/erasure/params.py`; changing them requires a new network version.  
- **Zero padding** is deterministic and committed.  
- **Integrity** is ensured by the **NMT**; RS only adds recoverability.

---

## 10) Interop & vectors

- `da/test_vectors/erasure.json`: small matrices with explicit (k, n, S), encoded outputs, and recovery cases.  
- `da/test_vectors/availability.json`: sample plans, success/failure cases, and computed bounds (`p_fail`, `s`).

Implementations must exactly reproduce vector roots and sampling decisions given the same RNG seed.

---

## 11) Security considerations

- **Malleability**: The NMT binds share positions (via deterministic order) and namespaces; adversaries cannot swap shares without changing `daRoot`.  
- **Selective serving**: Providers may try to stonewall specific indices; samplers randomize queries and peers, with timeouts and retry budgets.  
- **Parameter drift**: Clients must validate that DA parameters used for a block match the chain’s **active params**, or reject.  
- **Under-sampling**: Operators should not reduce `s` below policy thresholds; dashboards should surface effective `ε`.

---

## 12) Notation summary

- `k_axis, n_axis`: RS parameters per axis; `λ = n_axis / k_axis`.  
- `k_rows × k_cols` → `n_rows × n_cols`: matrix extension.  
- `S`: share size (bytes).  
- `M = n_rows·n_cols`, `W` withheld, `f = W/M`.  
- `s`: total samples; `p_fail ≤ (1 - f)^s ≤ e^{-f s}`.

---

## 13) Pseudocode (informative)

```text
encode_2d(blob, k_rows, k_cols, n_rows, n_cols, S):
  shares = chunk_and_pad(blob, S)
  data = fill_row_major(shares, k_rows, k_cols)

  # Row RS
  row_ext = [ RS_row_encode(data[r], k_cols, n_cols) for r in 0..k_rows-1 ]

  # Column RS to reach n_rows
  matrix = zeros(n_rows, n_cols)
  for c in 0..n_cols-1:
    col = [ row_ext[r][c] for r in 0..k_rows-1 ]
    col_ext = RS_col_encode(col, k_rows, n_rows)
    for r in 0..n_rows-1:
      matrix[r][c] = col_ext[r]

  leaves = linearize_row_major(matrix)  # each leaf is namespace||len||share
  daRoot = NMT_build(leaves).root_hash
  return (matrix, daRoot)

Sampler:

sample_until(daRoot, plan):   # plan defines s indices
  ok = 0
  for idx in plan.indices():
    (share, proof) = fetch_sample(idx)
    if !NMT_verify(daRoot, idx, share, proof): return Unavailable
    ok += 1
  return AvailableWithRisk(p_fail(plan))


⸻

14) Versioning

This is DA_ERASURE v1 tied to NMT v1 and the RS parameterization in da/erasure/params.py. Any change to field parameters, generator points, or layout must increment the version in chain params and DA specs.

