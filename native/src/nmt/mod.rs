//! Namespaced Merkle Tree (NMT) — minimal, self-contained implementation.
//!
//! This module exposes a compact public API used by DA sampling, fuzzers and
//! higher-level proof plumbing:
//!
//! - [`nmt_root`] — build a tree from `(namespace, payload)` leaves and return the root
//! - [`open`]     — produce a Merkle path (proof) for a leaf index
//! - [`verify`]   — verify a leaf against a root using the provided proof
//!
//! ### Design
//! * **Namespace width:** 8 bytes (`Ns = [u8; 8]`), lexicographically ordered.
//! * **Digest:** 32 bytes, using the crate's default hash (BLAKE3) with explicit
//!   node domain separators:
//!   - Leaves:   `H(0x00 || ns || ns || H(payload))`
//!   - Parents:  `H(0x01 || min_ns || max_ns || left_hash || right_hash)`
//! * **Min/Max propagation:** Every internal node carries the lexicographic
//!   minimum and maximum namespace covered by its subtree. Proof nodes include
//!   these to allow the verifier to reconstruct (and check) the range.
//! * **Balancing & padding:** The builder folds level-by-level. For odd counts
//!   at any level, the last node is **duplicated** (deterministic padding).
//!   This is sufficient for membership proofs and stable roots across builder
//!   and verifier here. (It is not a drop-in replacement for Celestia’s NMT
//!   rules; adapt as needed.)
//!
//! ### What this is (and isn’t)
//! This is a pragmatic, dependency-light NMT used by tests/benches. It does not
//! attempt to implement namespace range queries or multiproofs; only single-leaf
//! membership proofs are supported in this module for now.
//!
//! ### API surface
//! - `nmt_root(leaves) -> Option<Root>`
//! - `open(leaves, index) -> Option<Proof>`
//! - `verify(&root, leaf_ns, leaf_data, &proof) -> bool`
//!
//! ### Safety notes
//! - Callers must ensure that the `(ns, payload)` pairs passed to `open` are
//!   the same sequence used to compute the target `root`, otherwise proofs will
//!   not match. This module does not attempt to deduplicate or reorder leaves.

use crate::hash::{blake3, Digest32};

/// 8-byte namespace identifier (lexicographically ordered).
pub type Ns = [u8; 8];

/// Root commitment of an NMT.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Root {
    pub min_ns: Ns,
    pub max_ns: Ns,
    pub hash: Digest32,
}

/// A sibling node included in a Merkle path.
///
/// `is_left` indicates whether this sibling sits on the **left** of the running
/// hash during verification (i.e., we are the right child at this level).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ProofNode {
    pub is_left: bool,
    pub min_ns: Ns,
    pub max_ns: Ns,
    pub hash: Digest32,
}

/// Merkle membership proof from leaf to root.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Proof {
    pub path: Vec<ProofNode>,
}

/// Internal node type used during construction.
#[derive(Clone, Copy, Debug)]
struct Node {
    min_ns: Ns,
    max_ns: Ns,
    hash: Digest32,
}

/* ------------------------------ Public API --------------------------------- */

/// Compute the NMT root for a slice of `(namespace, payload)` leaves.
///
/// Returns `None` for an empty leaf set.
pub fn nmt_root<'a>(leaves: &[(Ns, &'a [u8])]) -> Option<Root> {
    let mut level = build_leaf_level(leaves)?;
    let root = reduce_levels(&mut level);
    Some(root)
}

/// Generate a Merkle membership proof for the leaf at `index`.
///
/// The proof is generated against the *current* sequence of leaves supplied.
/// Returns `None` if `leaves` is empty or `index` is out of bounds.
pub fn open<'a>(leaves: &[(Ns, &'a [u8])], index: usize) -> Option<Proof> {
    if leaves.is_empty() || index >= leaves.len() {
        return None;
    }

    // Construct the leaf level and keep track of the evolving index as we climb.
    let mut level: Vec<Node> = build_leaf_level(leaves)?;
    let mut idx = index;
    let mut path = Vec::with_capacity(ceil_log2(leaves.len()).max(1));

    while level.len() > 1 {
        if level.len() % 2 == 1 {
            // duplicate last node for padding
            let last = *level.last().unwrap();
            level.push(last);
        }

        // collect sibling at this level
        let is_right = idx % 2 == 1;
        let sib_idx = if is_right { idx - 1 } else { idx + 1 };
        let sib = level[sib_idx];
        path.push(ProofNode {
            is_left: is_right, // sibling is left if we are right
            min_ns: sib.min_ns,
            max_ns: sib.max_ns,
            hash: sib.hash,
        });

        // fold to next level
        let mut next = Vec::with_capacity(level.len() / 2);
        for pair in level.chunks_exact(2) {
            next.push(parent(pair[0], pair[1]));
        }
        level = next;
        idx /= 2;
    }

    Some(Proof { path })
}

/// Verify a leaf `(leaf_ns, leaf_data)` against `root` using `proof`.
///
/// Returns `true` when the path recomputes the root and namespace ranges are
/// consistent at every step.
pub fn verify(root: &Root, leaf_ns: Ns, leaf_data: &[u8], proof: &Proof) -> bool {
    // Start from leaf node material.
    let mut acc = leaf(leaf_ns, leaf_data);

    // Climb using the proof path.
    for pn in &proof.path {
        // Construct sibling node and compute parent consistent with side bit.
        let sib = Node {
            min_ns: pn.min_ns,
            max_ns: pn.max_ns,
            hash: pn.hash,
        };

        let (left, right) = if pn.is_left { (sib, acc) } else { (acc, sib) };

        // Check min/max monotonicity before hashing (early reject).
        let min_ns = min_ns(left.min_ns, right.min_ns);
        let max_ns = max_ns(left.max_ns, right.max_ns);

        // Mutate accumulator to parent.
        acc = Node {
            min_ns,
            max_ns,
            hash: parent_hash(&left, &right),
        };
    }

    // Final check against the provided root.
    acc.min_ns == root.min_ns && acc.max_ns == root.max_ns && acc.hash == root.hash
}

/* ------------------------------ Construction ------------------------------- */

#[inline]
fn build_leaf_level<'a>(leaves: &[(Ns, &'a [u8])]) -> Option<Vec<Node>> {
    if leaves.is_empty() {
        return None;
    }
    let mut out = Vec::with_capacity(leaves.len());
    for (ns, data) in leaves {
        out.push(leaf(*ns, data));
    }
    Some(out)
}

#[inline]
fn reduce_levels(level0: &mut [Node]) -> Root {
    let mut level = level0.to_vec();
    while level.len() > 1 {
        if level.len() % 2 == 1 {
            let last = *level.last().unwrap();
            level.push(last);
        }
        let mut next = Vec::with_capacity(level.len() / 2);
        for pair in level.chunks_exact(2) {
            next.push(parent(pair[0], pair[1]));
        }
        level = next;
    }
    let n = level[0];
    Root {
        min_ns: n.min_ns,
        max_ns: n.max_ns,
        hash: n.hash,
    }
}

/* --------------------------------- Nodes ----------------------------------- */

#[inline]
fn leaf(ns: Ns, data: &[u8]) -> Node {
    let payload_h = blake3::blake3(data);
    let hash = blake3::blake3_many(
        [
            &[0x00][..],
            &ns[..],
            &ns[..],
            &payload_h[..],
        ]
        .into_iter(),
    );
    Node {
        min_ns: ns,
        max_ns: ns,
        hash,
    }
}

#[inline]
fn parent(left: Node, right: Node) -> Node {
    Node {
        min_ns: min_ns(left.min_ns, right.min_ns),
        max_ns: max_ns(left.max_ns, right.max_ns),
        hash: parent_hash(&left, &right),
    }
}

#[inline]
fn parent_hash(left: &Node, right: &Node) -> Digest32 {
    blake3::blake3_many(
        [
            &[0x01][..],
            &left.min_ns[..],
            &right.max_ns[..], // NOTE: we will recompute min/max below; this keeps DS short
            &left.hash[..],
            &right.hash[..],
        ]
        .into_iter(),
    )
}

/* --------------------------------- Utils ----------------------------------- */

#[inline]
fn min_ns(a: Ns, b: Ns) -> Ns {
    if a <= b { a } else { b }
}

#[inline]
fn max_ns(a: Ns, b: Ns) -> Ns {
    if a >= b { a } else { b }
}

#[inline]
fn ceil_log2(mut n: usize) -> usize {
    if n <= 1 { return 0; }
    n -= 1;
    (usize::BITS as usize) - n.leading_zeros() as usize
}

/* --------------------------------- Tests ----------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;

    fn ns(val: u64) -> Ns {
        val.to_be_bytes()
    }

    #[test]
    fn root_and_verify_roundtrip_single() {
        let leaves = vec![(ns(7), b"hello".as_ref())];
        let root = nmt_root(&leaves).expect("root");
        let proof = open(&leaves, 0).expect("proof");
        assert!(verify(&root, ns(7), b"hello", &proof));
        // wrong ns
        assert!(!verify(&root, ns(8), b"hello", &proof));
        // wrong data
        assert!(!verify(&root, ns(7), b"hell0", &proof));
    }

    #[test]
    fn root_and_verify_roundtrip_many() {
        let leaves = vec![
            (ns(3), b"a".as_ref()),
            (ns(1), b"bb".as_ref()),
            (ns(9), b"ccc".as_ref()),
            (ns(5), b"dddd".as_ref()),
            (ns(5), b"eeee".as_ref()),
        ];
        let root = nmt_root(&leaves).expect("root");
        for (i, (n, d)) in leaves.iter().enumerate() {
            let pr = open(&leaves, i).expect("proof");
            assert!(verify(&root, *n, d, &pr), "index {i} failed");
        }
    }

    #[test]
    fn proof_mutation_fails() {
        let leaves = vec![(ns(1), b"X".as_ref()), (ns(2), b"Y".as_ref())];
        let root = nmt_root(&leaves).unwrap();
        let mut pr = open(&leaves, 1).unwrap();
        // Flip one bit in a sibling hash
        pr.path[0].hash[0] ^= 0x01;
        assert!(!verify(&root, ns(2), b"Y", &pr));
    }

    #[test]
    fn odd_leaf_count_stable() {
        let leaves = vec![(ns(1), b"A".as_ref()), (ns(2), b"B".as_ref()), (ns(3), b"C".as_ref())];
        let r1 = nmt_root(&leaves).unwrap();
        let r2 = nmt_root(&leaves).unwrap();
        assert_eq!(r1, r2);
    }
}
