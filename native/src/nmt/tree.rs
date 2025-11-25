//! Iterative, bottom-up Namespaced Merkle Tree (NMT) construction.
//!
//! - **Leaves** are hashed (namespace-aware) possibly in parallel (feature `rayon`).
//! - The tree is built bottom-up in *layers* iteratively (no recursion).
//! - For odd-width layers, the last node is **carried up** unchanged.
//! - Provides helpers to (a) build all layers, (b) get the root,
//!   and (c) extract a Merkle *path* (sibling triplets) for a leaf index.
//!
//! This module is intentionally "internal": it exposes a small surface used
//! by `super::mod` to implement the public `nmt_root`, `open`, and `verify`.

use crate::hash::Digest32;
use super::types::{Leaf, NamespaceId};
use super::hashers;

/// A compact node representation carried across layers.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Node {
    pub min: NamespaceId,
    pub max: NamespaceId,
    pub hash: Digest32,
}

impl Node {
    #[inline]
    pub fn new(min: NamespaceId, max: NamespaceId, hash: Digest32) -> Self {
        Self { min, max, hash }
    }
}

/* ----------------------------- Leaf hashing ------------------------------ */

#[cfg(feature = "rayon")]
use rayon::prelude::*;

/// Hash leaves into the first layer of `Node`s (namespace-aware).
#[inline]
pub(crate) fn hash_leaves(leaves: &[Leaf]) -> Vec<Node> {
    #[cfg(feature = "rayon")]
    {
        leaves
            .par_iter()
            .map(|lf| {
                let (min, max, h) = hashers::leaf(lf.ns, &lf.data);
                Node::new(min, max, h)
            })
            .collect()
    }
    #[cfg(not(feature = "rayon"))]
    {
        leaves
            .iter()
            .map(|lf| {
                let (min, max, h) = hashers::leaf(lf.ns, &lf.data);
                Node::new(min, max, h)
            })
            .collect()
    }
}

/* --------------------------- Iterative build ----------------------------- */

/// Build the *next* layer from a slice of nodes. Odd tail is carried up.
#[inline]
fn next_layer(curr: &[Node]) -> Vec<Node> {
    if curr.is_empty() {
        return Vec::new();
    }
    let mut out = Vec::with_capacity((curr.len() + 1) / 2);
    let mut i = 0usize;
    while i + 1 < curr.len() {
        let l = curr[i];
        let r = curr[i + 1];
        let (min, max, h) = hashers::parent(l.min, l.max, &l.hash, r.min, r.max, &r.hash);
        out.push(Node::new(min, max, h));
        i += 2;
    }
    // Carry an odd tail upwards unchanged (no self-duplication).
    if i < curr.len() {
        out.push(curr[i]);
    }
    out
}

/// Build all layers bottom-up, returning a vector of layers:
/// `layers[0]` = hashed leaves, `layers.last()` = root layer (len=1).
///
/// If `leaves` is empty, returns `vec![]`.
pub(crate) fn build_layers(leaves: &[Leaf]) -> Vec<Vec<Node>> {
    let mut layers = Vec::new();
    let mut curr = hash_leaves(leaves);
    if curr.is_empty() {
        return layers;
    }
    layers.push(curr.clone());

    loop {
        let next = next_layer(&curr);
        layers.push(next.clone());
        if next.len() == 1 {
            break;
        }
        curr = next;
    }
    layers
}

/// Compute the root Node from already-built layers (convenience).
#[inline]
pub(crate) fn root_from_layers(layers: &[Vec<Node>]) -> Option<Node> {
    layers.last().and_then(|top| top.first().copied())
}

/// Build and return the root Node in one call (no layers retained).
#[inline]
pub(crate) fn build_root(leaves: &[Leaf]) -> Option<Node> {
    if leaves.is_empty() {
        return None;
    }
    let mut curr = hash_leaves(leaves);
    while curr.len() > 1 {
        curr = next_layer(&curr);
    }
    curr.first().copied()
}

/* --------------------------- Path extraction ----------------------------- */

/// Sibling along a path. `left` indicates whether the sibling is the *left*
/// child at that level (`true` => sibling is left of the target).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Sibling {
    pub left: bool,
    pub node: Node,
}

/// Compute the Merkle path (siblings) for a given leaf index from `layers`.
///
/// - `layers[0]` must be the *leaf* layer (as returned by `build_layers`).
/// - The path is ordered from the bottom (adjacent to the leaf) to the top.
/// - If a level has no sibling (odd carry), that level is *skipped*.
///
/// Returns `None` if `index` is out of bounds.
pub(crate) fn path_for_index(index: usize, layers: &[Vec<Node>]) -> Option<Vec<Sibling>> {
    if layers.is_empty() || layers[0].is_empty() || index >= layers[0].len() {
        return None;
    }
    let mut idx = index;
    let mut out = Vec::new();

    // For each layer except the root layer
    for layer in layers.iter().take(layers.len().saturating_sub(1)) {
        let len = layer.len();

        // Determine sibling index, if any.
        if idx % 2 == 0 {
            // even => right child, sibling expected at idx+1
            let sib_idx = idx + 1;
            if sib_idx < len {
                out.push(Sibling {
                    left: false, // sibling is to the right of target (target is left)
                    node: layer[sib_idx],
                });
            }
        } else {
            // odd => left child, sibling at idx-1 (always valid)
            let sib_idx = idx - 1;
            out.push(Sibling {
                left: true, // sibling is to the left of target (target is right)
                node: layer[sib_idx],
            });
        }

        // Move index up to the parent for the next level.
        idx /= 2;
    }

    Some(out)
}

/* --------------------------------- Tests ----------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;
    use super::super::types::{ns_from_u64, Leaf};

    fn mk_leaf(ns_u64: u64, data: &'static [u8]) -> Leaf {
        Leaf { ns: ns_from_u64(ns_u64), data: data.to_vec() }
    }

    #[test]
    fn empty_returns_none_and_no_layers() {
        assert!(build_root(&[]).is_none());
        let layers = build_layers(&[]);
        assert!(layers.is_empty());
    }

    #[test]
    fn single_leaf_root_is_leaf_digest() {
        let leaves = vec![mk_leaf(7, b"hello")];
        let root1 = build_root(&leaves).unwrap();

        let layers = build_layers(&leaves);
        let root2 = root_from_layers(&layers).unwrap();

        assert_eq!(root1, root2);
        // Root min/max must equal the leaf ns.
        assert_eq!(root1.min, ns_from_u64(7));
        assert_eq!(root1.max, ns_from_u64(7));
    }

    #[test]
    fn deterministic_root_two_leaves() {
        let leaves = vec![mk_leaf(1, b"a"), mk_leaf(9, b"b")];
        let r1 = build_root(&leaves).unwrap();

        // Build again to check determinism.
        let r2 = build_root(&leaves).unwrap();
        assert_eq!(r1, r2);

        // Min & max aggregate properly.
        assert_eq!(r1.min, ns_from_u64(1));
        assert_eq!(r1.max, ns_from_u64(9));
    }

    #[test]
    fn odd_width_carries_tail() {
        // 3 leaves => level widths: 3 -> 2 (carry) -> 1
        let leaves = vec![mk_leaf(1, b"a"), mk_leaf(2, b"b"), mk_leaf(3, b"c")];
        let layers = build_layers(&leaves);

        assert_eq!(layers[0].len(), 3);
        assert_eq!(layers[1].len(), 2);
        assert_eq!(layers[2].len(), 1);

        let root = layers.last().unwrap()[0];
        // min/max across all 3
        assert_eq!(root.min, ns_from_u64(1));
        assert_eq!(root.max, ns_from_u64(3));
    }

    #[test]
    fn path_has_expected_length_and_orientation() {
        // 5 leaves -> layers: 5 -> 3 -> 2 -> 1
        // Choose index 3 (4th leaf).
        let leaves = vec![
            mk_leaf(1, b"a"),
            mk_leaf(2, b"b"),
            mk_leaf(3, b"c"),
            mk_leaf(4, b"d"),
            mk_leaf(5, b"e"),
        ];
        let layers = build_layers(&leaves);
        let path = path_for_index(3, &layers).expect("path");

        // Level widths (excluding root): [5, 3, 2]
        // For index 3: siblings exist at level 0 (idx=2), level 1 (idx=1), and at level 2 (idx=1 sibling idx=0).
        // However, note the odd-carry semantics could skip a sibling if at the tail.
        // Concretely for 5:
        //  - L0 (len=5): idx=3 (odd) => sibling at 2 (left=true)  -> present
        //  - L1 (len=3): idx=1 (odd) => sibling at 0 (left=true)  -> present
        //  - L2 (len=2): idx=0 (even) => sibling at 1 (left=false) -> present
        assert_eq!(path.len(), 3);
        assert!(path[0].left,  "L0 sibling should be to the left");
        assert!(path[1].left,  "L1 sibling should be to the left");
        assert!(!path[2].left, "L2 sibling should be to the right");

        // All siblings are valid nodes (non-zero hash is a weak check here).
        for sib in path {
            assert_ne!(sib.node.hash, [0u8; 32]);
        }
    }
}
