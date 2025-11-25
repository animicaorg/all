# Namespaced Merkle Tree (NMT) — Design & Proofs

This note specifies Animica’s **Namespaced Merkle Tree (NMT)** used to commit to per-namespace blobs and to prove inclusion or *namespace-range* properties of leaves. The NMT root is bound into block headers as the **DA root**.

> TL;DR: Leaves are sorted by namespace id, internal nodes carry the min/max namespace covered by their subtree, and proofs are ordinary Merkle branches augmented with namespace bounds so verifiers can check both hashing integrity and namespace claims.

---

## 1) Types & constants

- **Namespace id (`ns`)**: 32-bit unsigned integer (`uint32`, big-endian on wire).  
  Reserved/system ranges and user ranges are defined in `da/constants.py`.
- **Hash**: `SHA3-256` with domain separation.
  - Leaf tag: `b"nmt:leaf:v1"`
  - Node tag: `b"nmt:node:v1"`
- **Leaf encoding** (byte exact; also see `da/schemas/nmt.cddl` and `da/nmt/codec.py`):

leaf := ns:u32_be || len:uvarint || data:bytes(len)

The `uvarint` is **minimal** (no leading zero continuation groups).

---

## 2) Tree layout & hashing

### 2.1 Ordering
All leaves are sorted by `(ns, stable_index)` where `stable_index` is the insertion order **within the same namespace**. (Writers MUST pre-sort; verifiers MAY re-sort defensively if a source claims otherwise.)

### 2.2 Merkleization (unbalanced, left-carry)
Build the tree level by level, pairing neighbors; if a level has an odd node, **carry** the last node up unpaired.

No padding nodes are introduced; this avoids extra conventions for “empty” hashes.

### 2.3 Node metadata (namespace ranges)
Each internal node tracks:
- `ns_min = min(left.ns_min, right.ns_min)`
- `ns_max = max(left.ns_max, right.ns_max)`

For a leaf, `ns_min = ns_max = leaf.ns`.

### 2.4 Hash functions

H_leaf(leaf) = SHA3-256( “nmt:leaf:v1” || ns:u32_be || len:uvarint || data )
H_node(L,R)  = SHA3-256( “nmt:node:v1” || H(L) || H(R) || L.ns_min:u32_be || R.ns_max:u32_be )

Including `ns_min/ns_max` in the internal-node hash prevents an adversary from
splicing branches across incompatible namespace intervals.

**Root:** the Merkle hash at the top; exported together with `(ns_min, ns_max)` of the whole tree (the latter is derivable but useful in debugging/inspection tools).

---

## 3) Proof forms

Animica uses two proof shapes:

1. **Inclusion proof** for a concrete `(ns, leaf_bytes)` at position `i` in the leafset.  
   - Prover supplies the leaf bytes and the **branch**: the sequence of siblings (left/right) up to the root, each annotated with the sibling’s `(ns_min, ns_max)`.
   - Verifier checks the leaf encoding, recomputes `H_leaf`, folds the branch, verifying at each step:
     - Hash equality with the supplied sibling
     - Namespace range propagation:
       ```
       parent.ns_min == min(left.ns_min, right.ns_min)
       parent.ns_max == max(left.ns_max, right.ns_max)
       ```
     - Final computed root equals the claimed root.

2. **Namespace-range proof** that **all leaves** with a namespace `ns` are included (possibly zero leaves). This supports *availability sampling by namespace* and selective sync.
   - Prover supplies:
     - A **left boundary** branch proving the last leaf with namespace `< ns` (or a structural boundary if none exists).
     - A **right boundary** branch proving the first leaf with namespace `> ns` (or a structural boundary if none exists).
     - Optionally the **multi-leaf aggregate** for all leaves with `ns` (e.g., a subtree root covering exactly that namespace span) to reduce branch length.
   - Verifier checks that:
     - Along both branches, every internal node’s `(ns_min, ns_max)` is consistent.
     - The open interval between the verified boundaries contains **only** `ns` (or is empty), which can be determined from ranges carried by the branches and, if present, the aggregate node covering the `ns` span.
     - Folding both sides recomputes the root.

**Absence proof** for `ns` is a special case of (2) where the interval between boundaries proves no node/leaf can carry `ns` (i.e., left boundary `ns_max < ns` and right boundary `ns_min > ns` with no overlap).

---

## 4) Algorithms (reference)

### 4.1 Build (writer-side)
```python
def build_nmt(leaves_sorted):
    nodes = [Leaf(ns=l.ns, h=H_leaf(l), ns_min=l.ns, ns_max=l.ns) for l in leaves_sorted]
    while len(nodes) > 1:
        nxt = []
        it = iter(nodes)
        for L in it:
            R = next(it, None)
            if R is None:
                nxt.append(L)  # carry
            else:
                ns_min = min(L.ns_min, R.ns_min)
                ns_max = max(L.ns_max, R.ns_max)
                h = H_node(L, R)
                nxt.append(Node(h=h, ns_min=ns_min, ns_max=ns_max))
        nodes = nxt
    return nodes[0] if nodes else EMPTY_TREE  # empty tree undefined by protocol; avoid.

4.2 Verify inclusion

def verify_inclusion(root, ns, leaf_bytes, branch):
    # branch: list of (sibling_hash, sibling_is_left, sib_ns_min, sib_ns_max)
    h = H_leaf(decode_leaf_assert(ns, leaf_bytes))
    ns_min = ns_max = ns
    for (sib_h, sib_is_left, sib_min, sib_max) in branch:
        if sib_is_left:
            ns_min, ns_max = min(sib_min, ns_min), max(sib_max, ns_max)
            h = SHA3_256(b"nmt:node:v1" + sib_h + h + u32be(sib_min) + u32be(ns_max))
        else:
            ns_min, ns_max = min(ns_min, sib_min), max(ns_max, sib_max)
            h = SHA3_256(b"nmt:node:v1" + h + sib_h + u32be(ns_min) + u32be(sib_max))
    return h == root

4.3 Verify namespace-range (sketch)

Given left boundary branch BL and right boundary branch BR:
	•	Fold each branch to the root, tracking (ns_min, ns_max) at every step.
	•	Ensure:
	•	max_ns(BL) < ns <= min_ns(BR) (or corresponding empty side conditions)
	•	No step on either branch can include a node with range overlapping a foreign namespace inside (BL..BR).
	•	If an aggregate node for ns is provided, verify its branch once and treat it as a single segment spanning [ns, ns].

⸻

5) Interop rules & edge cases
	•	Duplicate leaves within a namespace: Allowed and distinguished by stable_index (insertion order); proofs are position-specific.
	•	Odd node carry: The left-carry rule MUST be used by all implementations; otherwise the root would diverge.
	•	No empty-tree root: The protocol avoids committing an empty NMT for block DA; blocks without blobs commit a fixed known sentinel in the header’s DA root domain (outside the NMT scheme). (See header spec.)
	•	Canonical encodings: Any variation in uvarint length or CBOR map ordering changes H_leaf. Writers MUST follow da/schemas/*.cddl.

⸻

6) Proof sizes & performance notes
	•	Inclusion proofs are O(log N) hashes; range proofs benefit from aggregate subtrees that cover all ns leaves, reducing witness length.
	•	Carrying (ns_min, ns_max) per branch element adds 8 bytes per level and prevents cross-namespace splicing.
	•	No padding means strictly fewer nodes than a full power-of-two tree, at the cost of slightly irregular proof paths.

⸻

7) Security considerations
	•	Collision resistance: Relies on SHA3-256; domain tags separate leaf vs node transcripts.
	•	Range soundness: Hashing (ns_min, ns_max) into internal nodes binds topology to namespace intervals, preventing graft attacks.
	•	Determinism: Sorting and left-carry guarantee a unique root for a given leaf multiset and order-within-namespace.

⸻

8) Conformance
	•	Writers: MUST pre-sort leaves by (ns, stable_index), MUST use left-carry, MUST use the exact encodings.
	•	Verifiers: MUST enforce namespace bounds at every step while folding proofs; MUST reject any branch with inconsistent (ns_min, ns_max) propagation even if the final hash matches.

See:
	•	da/nmt/tree.py, da/nmt/proofs.py, da/nmt/verify.py for the reference implementation.
	•	da/tests/test_nmt_tree.py, da/tests/test_nmt_proofs.py for vectors and negative cases.

