# Data Availability Sampling (DAS) — Protocol, Math, Light-Client Flow

This document specifies Animica’s **Data Availability Sampling** (DAS) protocol: how block producers commit to data, how light clients sample and verify, and how networks size the number of samples to achieve a target detection probability. It ties together the **erasure layer** (RS), the **Namespaced Merkle Tree (NMT)**, and the **retrieval service**.

> TL;DR: Producers commit an NMT root over namespaced, RS-encoded shards; light clients query random shard indices, verify NMT proofs against the header’s DA root, and apply simple probability bounds to decide availability with high confidence.

---

## 0) Notation & pointers

- `k, n, S` — RS parameters (data shards, total shards, shard size). See `da/specs/ERASURE.md`.
- **NMT** — Namespaced Merkle Tree. See `da/specs/NMT.md`.
- **DA root** — the NMT root committed in the block header.
- **Blob** — an application payload turned into one or more RS segments → shards → NMT leaves.
- **Namespace** — `uint32` id; all shards of a blob share the same `ns`.
- **Availability proof** — a bundle of inclusion/range proofs for a sample set (schema in `da/schemas/availability_proof.cddl`).

Related modules:
- Encoding & layout: `da/erasure/*`, `da/nmt/*`
- Retrieval API: `da/retrieval/api.py`
- Sampler & math: `da/sampling/sampler.py`, `da/sampling/probability.py`, `da/sampling/verifier.py`, `da/sampling/light_client.py`

---

## 1) Producer → Header: commitment

1. **Chunk & encode**: The producer partitions each blob into RS segments and encodes to `n` shards/segment (`k` data + `n-k` parity). Shards are placed in a deterministic row-major layout (see `da/erasure/layout.py`).
2. **Leafify**: Each shard becomes one NMT leaf with `(ns, len, data)` (no serialized zero padding).
3. **Commit**: Build the NMT over all leaves (across all blobs in the block), sort by `(ns, stable_index)`, and left-carry odd nodes. The resulting root is the **DA root**.
4. **Bind**: The header includes the DA root in its committed fields. Any alteration to blob bytes, shard sizing, layout, or order changes the root and invalidates the block.

**Security**: Integrity is by the NMT hash; availability is evidenced probabilistically by sampling.

---

## 2) Retrieval API surface (read-only)

Animica exposes a minimal REST for light clients and auditors (see `da/schemas/retrieval_api.schema.json` and `da/retrieval/api.py`):

- `GET /da/blob/{commitment}` → stream shard payloads by index (supports range or index query).
- `GET /da/proof?commitment=…&indices=i0,i1,…` → returns an **availability proof** containing:
  - For each requested index: the leaf body and an NMT inclusion branch (siblings + `(ns_min, ns_max)` annotations).
  - Optionally, namespace-range branches when a client asks “prove **no** leaf exists for `ns = X`”.
- `POST /da/blob` (write path for dev/test) → accept a blob, return its commitment & receipt. *Not needed by light clients.*

Nodes MAY serve the same data over P2P topics (`da/adapters/p2p_*`), but the verification rules are identical.

---

## 3) Sampling plans

A **sampling plan** is a multiset of distinct shard indices to query. Plans should be:

- **Uniform** over the extended set `n` (default).
- **Stratified** across segments/rows/cols to reduce variance on large blobs.
- **Bounded** by network policy (max queries per block/window) to control DoS.

### 3.1 Uniform plan (default)
For a header with DA root `R`, choose `s` indices `I = {i_1, …, i_s}` uniformly without replacement in `[0, n)`. For multi-blob blocks, clients may sample **per blob** (using the blob’s leaf range; exposed by indices in proofs) or over the entire leafset.

### 3.2 Row/column stratified plan (recommended for large `n`)
Let `n = R × C`. Choose `s_r` random rows and `s_c` random columns, then pick one or more indices from each chosen row/column (mapping rules in `da/erasure/layout.py`). This catches clustered withholding patterns at similar overall cost.

The library provides:
- `build_uniform(n, s)` in `da/sampling/queries.py`
- `build_stratified(n, R, C, s_r, s_c)` in `da/sampling/queries.py`

---

## 4) Probability of failure to detect withholding

Let `p` be the (unknown) fraction of **missing** shards in the population a client intends to recover (e.g., a segment). A client takes `s` independent uniform samples.

The probability that **none** of the samples hit a missing shard is:

P_fail = (1 - p)^s

Solve for `s` to meet target `P_fail ≤ τ`:

s ≥ ceil( ln(τ) / ln(1 - p) )

Examples:
- If the network is unsafe when ≥ 10% shards are missing (`p = 0.10`) and the client targets `τ = 2^-30`, then
  `s ≥ ceil( ln(2^-30)/ln(0.9) ) ≈ 658`.

**Finite-population correction** (sampling w/o replacement) slightly improves the bound:

P_fail = C(n - m, s) / C(n, s)       where m = ⌈p n⌉

The helper in `da/sampling/probability.py` computes both the exact hypergeometric tail and the simpler exponential bound; networks can tune policy using either.

> Note: DAS only detects **global** unavailability. A single node’s transient network failure is indistinguishable from withholding; the client should retry and/or query diverse endpoints.

---

## 5) Light-client verification flow

Given a block header `H` with DA root `R`:

1. **Plan**: Build `I = {i_1, …, i_s}` via `queries.py` for the desired `s`.
2. **Fetch**: Request `GET /da/proof?commitment=<R>&indices=<I>` from one or more servers (or via P2P).
3. **Verify NMT** (for each `i ∈ I`):
   - Recompute `H_leaf(ns, len, data)` and fold the inclusion branch.
   - At each internal step, verify `(ns_min, ns_max)` propagation (prevents branch splicing).
   - Check the final hash equals `R`. Reject on any mismatch.
4. **Decide**: If all samples verify, declare the blobset **available with confidence** `1 - P_fail`. Otherwise, declare **unavailable** (a single verified failure suffices).
5. **(Optional) Reconstruct**: If a client wants the actual payload, it fetches any `k` distinct shards for a target segment, verifies each leaf, zero-pads to size `S`, then RS-decodes. See `da/erasure/decoder.py`.

Pseudocode sketch:
```python
from da.sampling.queries import build_uniform
from da.sampling.verifier import verify_leaf_proof
from da.sampling.probability import samples_for

s = samples_for(p=0.10, tau=2**-30)     # sizing helper
I = build_uniform(n, s)

proofs = http_get_proofs(R, I)          # leaf+branch per index
ok = all(verify_leaf_proof(R, prf) for prf in proofs)

decision = "AVAILABLE" if ok else "UNAVAILABLE"
confidence = 1 - (1 - p)**s             # report using assumed p (or show (s, τ))


⸻

6) Namespace-range queries

To assert that no data exists for namespace ns (or to prove completeness of all leaves with ns), a client requests a range proof. The server returns left/right boundary branches covering the maximal interval not equal to ns. The verifier:
	•	Folds both branches to R, checking namespace bounds at each step.
	•	Confirms the interval between them excludes all ns leaves (absence) or contains only ns (completeness).

API: GET /da/proof?commitment=…&ns=…&mode=range. Verification logic is implemented in da/nmt/verify.py.

⸻

7) Policy & scheduling

Nodes and wallets SHOULD:
	•	Use diverse servers or the P2P path to reduce correlated failures.
	•	Throttle queries per header to bounded rates (da/retrieval/rate_limit.py), and respect HTTP 429.
	•	Cache verified leaves and branches by (R, index) with an LRU (da/retrieval/cache.py).
	•	Stagger sampling across time to avoid load spikes right after block broadcast (da/sampling/scheduler.py).

Network parameters (from da/config.py):
	•	(k, n, S) — RS profile
	•	(R, C) — grid shape
	•	SAMPLES_BASE, SAMPLES_BURST — default and max per header
	•	Timeouts & retries — per-request budget

⸻

8) Failure handling & UX
	•	Proof missing: Treat as a network error; retry elsewhere. Do not count as cryptographic failure.
	•	Proof invalid (hash/namespace mismatch): Mark the serving endpoint untrustworthy and surface a hard failure.
	•	Partial success: If some samples verify and others time out, the client may decide using only verified samples or retry until a min quorum is reached.

⸻

9) Security considerations
	•	Binding: Proofs are only meaningful against the exact header DA root; include (height, blockHash) in requests and caches.
	•	Replay: Servers MUST ensure the commitment in responses matches the query and MUST NOT mix shards across roots.
	•	Privacy: Sampling reveals which indices a light client queries. Clients can pad queries with decoys or batch across headers to reduce linkability.
	•	DoS: Rate-limit heavy or adversarial query patterns; proofs are logarithmic in the total leaf count, but endpoints must defend against shard flooding.

⸻

10) Interop & test vectors
	•	Conformance: da/test_vectors/{nmt.json, erasure.json, availability.json}
	•	Unit tests: da/tests/test_{nmt_*, erasure_*, retrieval_api, light_client_verify, integration_post_get_verify}.py
	•	Wire schemas: da/schemas/{nmt.cddl, blob.cddl, availability_proof.cddl, retrieval_api.schema.json}

Implementations passing these vectors MUST produce identical roots and accept/reject the same proofs.

⸻

11) Summary
	•	Producers: RS → NMT → DA root in header.
	•	Light clients: sample → verify NMT branches → decide with bound on P_fail.
	•	The protocol is simple, scalable, and compatible with namespace-addressable data, enabling selective sync and application-specific availability checks.

