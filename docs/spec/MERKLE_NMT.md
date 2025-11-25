# MERKLE_NMT — Namespaced Merkle Trees, Commitments, Proofs

This document specifies the **Namespaced Merkle Tree (NMT)** used by Animica for **Data Availability (DA)** commitments and proofs. The NMT commits to both **content** and the **namespace ranges** of subtrees, enabling efficient inclusion and *absence-in-range* proofs.

Authoritative code lives in `da/nmt/*`:
- `da/nmt/namespace.py` — namespace id type/range checks
- `da/nmt/tree.py` — build/append/finalize NMTs
- `da/nmt/proofs.py` — inclusion & namespace-range proofs
- `da/nmt/commit.py` — compute DA (NMT) root from leaves
- `da/nmt/codec.py` — leaf serialization (namespace||len||data)
- `da/nmt/verify.py` — fast proof verification
- `da/nmt/indices.py` — index math: leaf↔share mapping

See also:
- `da/schemas/nmt.cddl` and `da/schemas/blob.cddl`
- `da/erasure/*` for (k, n) encoding and share layout
- `docs/spec/BLOCK_FORMAT.md` for `daRoot` in headers

---

## 1) Goals & threat model

- Commit to **blobs-of-shares** with per-share **namespace ids (NSIDs)**.
- Allow a light client to verify:
  1) *Inclusion*: “Share(s) with namespace `ns` and data `d` are committed in this root.”
  2) *Non-inclusion / Range*: “There are no shares with namespace in `[ns_lo, ns_hi]`.”
- Prevent adversaries from **splicing** shares across namespaces or **malleating** namespace ranges in internal nodes.
- Deterministic, streaming-friendly construction; stable across implementations.

**Non-goals**: Confidentiality; the NMT is a commitment structure only.

---

## 2) Parameters

Unless otherwise pinned by network params:

- **Hash function**: `SHA3-256`
- **Domain tags** (ASCII):
  - `b"nmt:leaf:v1"`
  - `b"nmt:node:v1"`
- **Namespace byte length**: `NS_BYTES = 8` (unsigned big-endian)
- **Namespace ordering**: unsigned lexicographic over `NS_BYTES` (total order)
- **Empty subtree hash**: `H(b"")` under node domain (used only for complete binary lift, otherwise avoided by compact tree implementation)

> Concrete values are enforced in code and exercised by vectors in `da/test_vectors/nmt.json`.

---

## 3) Namespaces & leaves

### 3.1 Namespace Id
A **Namespace Id** `ns` is a `u64` encoded as **big-endian** `NS_BYTES` (8) bytes. Reserved ranges:
- `0x00..00` — System / reserved
- `0x00..7F` — Infra-reserved (headers, manifests)
- `0x80..FFFF_FFFF_FFFF_FFFF` — **User** / application blobs

Exact policy is network-configurable; see `da/constants.py` for guardrails.

### 3.2 Leaf serialization

Each leaf (a “share”) is serialized as:

leaf_bytes = ns (8 bytes BE) || uvarint(len(data)) || data

The **leaf commitment** is:

LeafHash = SHA3_256( “nmt:leaf:v1” || ns || UVar(data_len) || data )

> The `UVar` length prevents ambiguity across concatenations and is canonicalized (no leading zeroes, shortest encoding).

### 3.3 Ordering constraint

When building a tree **for a blob**, shares are appended in **non-decreasing namespace order** (ties allowed). The NMT builder enforces:
- `ns[i] ≤ ns[i+1]`
- Proof generation relies on this ordering for compact range proofs.

---

## 4) Internal nodes & commitments

Each internal node stores two children `(L, R)` with metadata:

- `L.hash, L.ns_min, L.ns_max`
- `R.hash, R.ns_min, R.ns_max`

and commits to **both** the child hashes **and** their namespace ranges:

NodeHash = SHA3_256(
“nmt:node:v1” ||
L.ns_min || L.ns_max ||
R.ns_min || R.ns_max ||
L.hash || R.hash
)

The **namespace range** of a node is:

node.ns_min = min(L.ns_min, R.ns_min)
node.ns_max = max(L.ns_max, R.ns_max)

The **root** carries `(root.hash, root.ns_min, root.ns_max)`. Verifiers **must** check namespace range consistency along the path.

---

## 5) Root & commitment semantics

The DA **root** placed into the block header (`daRoot`) is:

daRoot = NodeHash(root)   ; 32 bytes

For light protocols that transmit `(root.hash, root.ns_min, root.ns_max)`, the `ns_min/ns_max` of the root must be consistent with the declared leaf set (e.g., across an erasure-coded matrix).

---

## 6) Proofs

We define two proof species:

1) **Inclusion proof** for one or multiple **adjacent** leaves with the **same namespace**.
2) **Namespace-range proof** for a half-open interval `[ns_lo, ns_hi]` (both inclusive for convenience in this spec), demonstrating **absence** or **completeness** of returned leaves.

### 6.1 Inclusion proof

A proof for leaves `L..R` (indices) with namespace `ns` consists of:

- The **leaf segment**: ordered list of `leaf_bytes[i]` for `i ∈ [L, R]` (at least one).
- The **audit path**: siblings along the route that reconstruct the root, with each sibling annotated by its `(ns_min, ns_max, hash)` triple in encounter order from segment to root.
- The **segment position** metadata: left/right directions or compact bitfield.

**Verification sketch**:

def verify_inclusion(root, ns, leaves, path, position):
# 1) Recompute segment hash from leaves
seg_hash = fold_segment(leaves)        # if multiple leaves, build minimal subtree
seg_min = ns
seg_max = ns

# 2) Ascend:
cur_hash, cur_min, cur_max = seg_hash, seg_min, seg_max
for sib in path:  # ordered bottom→top
    if position.next_is_left():
        L = (sib.hash, sib.ns_min, sib.ns_max)
        R = (cur_hash, cur_min, cur_max)
    else:
        L = (cur_hash, cur_min, cur_max)
        R = (sib.hash, sib.ns_min, sib.ns_max)

    # Check local range ordering
    assert L.ns_min <= L.ns_max and R.ns_min <= R.ns_max
    # Compute parent
    cur_hash = H_node(L, R)
    cur_min  = min(L.ns_min, R.ns_min)
    cur_max  = max(L.ns_max, R.ns_max)

# 3) Root match
return cur_hash == root.hash

The helper `fold_segment(leaves)` builds the smallest complete binary subtree over the given contiguous leaves (still committing via `H_leaf` and `H_node`).

### 6.2 Namespace-range proof (absence or completeness)

To prove **no leaves** exist with namespace in `[ns_lo, ns_hi]`, a prover returns a path that **covers** the interval using sibling subtrees whose **namespace ranges** are **strictly outside** the query interval. Concretely, the union of visited non-overlapping subtree ranges must **cover** `[ns_min(root), ns_max(root)]` except for the gap `[ns_lo, ns_hi]`, or provide bordering subtrees bracketing the gap tightly.

For **completeness** (when returning all leaves for a namespace), the proof includes:
- All leaves with `ns = query_ns`
- A **left border** sibling subtree whose `ns_max < query_ns`
- A **right border** sibling subtree whose `ns_min > query_ns`

**Verification** checks:
- Every included leaf has namespace equal to `query_ns`.
- Left border `ns_max < query_ns` and right border `ns_min > query_ns`.
- The reconstructed root matches and the path **covers** the surrounding ranges without overlap across the query namespace.

The exact on-wire shape is defined in `da/schemas/nmt.cddl`.

---

## 7) Erasure coding & share layout

Animica partitions blobs into **shares** via Reed–Solomon (see `da/erasure/*`). The extended matrix is laid out into rows/columns; **each share carries a namespace**:

- Application payload shares use the **blob’s namespace**.
- Parity shares inherit the same namespace (policy decision: parity is not cross-namespaced).
- The tree is built over the **linearized share list** in row-major order, which is already non-decreasing by `ns` given single-namespace blobs. Multi-blob packs are concatenated in **sorted namespace** order.

Index mapping helpers live in `da/nmt/indices.py`.

---

## 8) Canonical encoding details

- **Namespace**: 8-byte big-endian.
- **Lengths**: `uvarint` (shortest encoding).
- **CBOR envelopes**: Where proofs are CBOR-encoded, deterministic map ordering is required (`da/schemas/nmt.cddl`).
- **Hash byte order**: Big-endian bytes of `SHA3-256`.
- **Node composition**: Always exactly two children at each internal step; segment folding uses the same `H_node`.

---

## 9) Security considerations

- **Range-commitment**: Including `ns_min/ns_max` in each `NodeHash` prevents an adversary from attaching a subtree that swaps namespaces without changing the hash.
- **Ordering enforcement**: Builders **must** reject out-of-order leaves; otherwise, range proofs could be malformed.
- **Collision resistance**: Relies on SHA3-256. Domain tags separate contexts for leaves vs nodes.
- **Ambiguity**: Length-prefixing of leaves prevents concatenation ambiguity.
- **Sparse tails**: Avoid “virtual leaves”; the implementation folds segments to maintain a compact tree without placeholders.
- **DoS**: Proof verification is `O(log N + k)` where `k` is the number of returned leaves in the segment; inputs are size-capped and validated before hashing.

---

## 10) Light client flow (informative)

1) Fetch `(root.hash, root.ns_min, root.ns_max)` from the header.
2) Request a **proof** for `ns` (or a range).
3) Verify proof per §6; on success, accept the inclusion/absence claim.
4) Optionally, for **Data Availability Sampling (DAS)**, query random share indices; proofs tie samples to the same `daRoot` (`da/sampling/*`).

---

## 11) Test vectors & interoperability

- `da/test_vectors/nmt.json` provides:
  - Leaf sets with namespaces
  - Expected root `(hash, ns_min, ns_max)`
  - Inclusion proofs for single/multi-leaf segments
  - Range proofs for empty/non-empty cases

Implementations must round-trip these vectors exactly.

---

## 12) Reference pseudocode

```text
H_leaf(ns, data):
  return SHA3_256("nmt:leaf:v1" || ns || UVar(len(data)) || data)

H_node(L, R):
  return SHA3_256("nmt:node:v1" ||
                  L.ns_min || L.ns_max ||
                  R.ns_min || R.ns_max ||
                  L.hash   || R.hash)

build_nmt(leaves_sorted_by_ns):
  nodes = [ (H_leaf(ns, data), ns, ns) for (ns,data) in leaves ]
  while len(nodes) > 1:
     next = []
     for i in range(0, len(nodes), 2):
        if i+1 == len(nodes):
           # carry last (odd) node up (or pair with identity by spec choice)
           next.append(nodes[i])
        else:
           L, R = nodes[i], nodes[i+1]
           h = H_node(L, R)
           ns_min = min(L.ns_min, R.ns_min)
           ns_max = max(L.ns_max, R.ns_max)
           next.append( (h, ns_min, ns_max) )
     nodes = next
  return nodes[0]  # (root_hash, root_ns_min, root_ns_max)


⸻

13) Versioning

This is NMT v1 ("nmt:*:v1" tags). Any change to:
	•	hash function,
	•	namespace size/endianness, or
	•	node hashing transcript

requires bumping to vN with new domain tags and MUST NOT collide with existing roots.

