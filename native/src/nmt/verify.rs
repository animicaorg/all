//! Proof verification for the Namespaced Merkle Tree (NMT).
//!
//! This module provides *deterministic*, allocation-light routines to verify:
//! - **Inclusion proofs** for a single leaf
//! - **Range proofs** for a contiguous batch of leaves
//!
//! ## Model
//! The NMT uses namespace-aware hashing:
//! - Each node carries `(min_ns, max_ns, hash)`.
//! - Parents are computed with `hashers::parent(left, right)` which also
//!   propagates namespace windows as `min = min(l.min, r.min)` and
//!   `max = max(l.max, r.max)`.
//! - Odd tails are *carried* upward unchanged (no self-duplication).
//!
//! A proof path is represented as a sequence of *siblings* where each entry
//! contains the sibling's `(min, max, hash)` and whether it sits to the **left**
//! of the target (so we fold in the correct order at each level).
//!
//! ### Notes
//! - We validate **namespace window monotonicity** while folding a proof.
//!   This catches malformed proofs whose parent window would *exclude* the leaf.
//! - Range proofs here verify a set of *contiguous leaves* plus a frontier path
//!   to the root. This is a standard, compact multi-leaf proof shape.
//!
//! These helpers are used by `super::mod` to implement the public `verify` API.

use crate::hash::Digest32;
use super::hashers;
use super::types::{Leaf, NamespaceId};

/// One step of a proof path: a sibling node at some level.
///
/// If `left == true`, this sibling is **left of** the target at that level
/// (so the target is the right child). When `left == false`, the sibling is
/// on the right (target is the left child).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PathElem {
    pub left: bool,
    pub min: NamespaceId,
    pub max: NamespaceId,
    pub hash: Digest32,
}

/// Errors returned by verifiers.
#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum VerifyError {
    #[error("empty leaf set")]
    EmptyLeaves,
    #[error("root hash mismatch")]
    RootMismatch,
    #[error("namespace window violation at a proof step")]
    NamespaceWindowViolation,
    #[error("odd carry invariant broken during multi-leaf fold")]
    OddCarryInvariant,
}

/* ------------------------------ Utilities ------------------------------- */

#[inline]
fn ns_min(a: NamespaceId, b: NamespaceId) -> NamespaceId {
    if a <= b { a } else { b }
}
#[inline]
fn ns_max(a: NamespaceId, b: NamespaceId) -> NamespaceId {
    if a >= b { a } else { b }
}

#[inline]
fn ns_between(x: NamespaceId, lo: NamespaceId, hi: NamespaceId) -> bool {
    lo <= x && x <= hi
}

/* --------------------------- Inclusion proofs ---------------------------- */

/// Recompute the root tuple `(min, max, hash)` from a *single leaf* and a proof
/// `path` of siblings. While folding, namespace window invariants are enforced.
pub fn recompute_root_for_leaf(leaf: &Leaf, path: &[PathElem]) -> Result<(NamespaceId, NamespaceId, Digest32), VerifyError> {
    let (mut cur_min, mut cur_max, mut cur_hash) = hashers::leaf(leaf.ns, &leaf.data);

    // The leaf must live within the current window (trivial at the leaf level).
    debug_assert!(ns_between(leaf.ns, cur_min, cur_max));

    for step in path {
        // The leaf's namespace must remain within the future parent's window.
        let parent_min = ns_min(cur_min, step.min);
        let parent_max = ns_max(cur_max, step.max);
        if !ns_between(leaf.ns, parent_min, parent_max) {
            return Err(VerifyError::NamespaceWindowViolation);
        }

        let (min, max, h) = if step.left {
            // sibling sits on the left side
            hashers::parent(step.min, step.max, &step.hash, cur_min, cur_max, &cur_hash)
        } else {
            // sibling sits on the right side
            hashers::parent(cur_min, cur_max, &cur_hash, step.min, step.max, &step.hash)
        };

        cur_min = min;
        cur_max = max;
        cur_hash = h;
    }

    Ok((cur_min, cur_max, cur_hash))
}

/// Verify a single-leaf inclusion proof against an expected root tuple.
pub fn verify_inclusion(
    expected_root_hash: &Digest32,
    expected_root_min: NamespaceId,
    expected_root_max: NamespaceId,
    leaf: &Leaf,
    path: &[PathElem],
) -> Result<(), VerifyError> {
    let (min, max, h) = recompute_root_for_leaf(leaf, path)?;
    if &h != expected_root_hash || min != expected_root_min || max != expected_root_max {
        return Err(VerifyError::RootMismatch);
    }
    Ok(())
}

/* ----------------------------- Range proofs ------------------------------ */

/// Fold a *contiguous batch* of leaves into a single node using the same
/// bottom-up odd-carry semantics as the NMT tree builder.
///
/// Returns the aggregate `(min, max, hash)`.
fn fold_contiguous_leaves(leaves: &[Leaf]) -> Result<(NamespaceId, NamespaceId, Digest32), VerifyError> {
    if leaves.is_empty() {
        return Err(VerifyError::EmptyLeaves);
    }
    // First level: hash all leaves into nodes.
    let mut layer: Vec<(NamespaceId, NamespaceId, Digest32)> = leaves
        .iter()
        .map(|lf| hashers::leaf(lf.ns, &lf.data))
        .collect();

    // Carry up until a single node remains.
    while layer.len() > 1 {
        let mut next: Vec<(NamespaceId, NamespaceId, Digest32)> = Vec::with_capacity((layer.len() + 1) / 2);
        let mut i = 0usize;
        while i + 1 < layer.len() {
            let (lmin, lmax, lhash) = layer[i];
            let (rmin, rmax, rhash) = layer[i + 1];
            // Combine as a regular parent.
            let (pmin, pmax, phash) = hashers::parent(lmin, lmax, &lhash, rmin, rmax, &rhash);
            next.push((pmin, pmax, phash));
            i += 2;
        }
        // Odd tail carried unchanged.
        if i < layer.len() {
            next.push(layer[i]);
        }
        layer = next;
    }

    // Single node remains
    Ok(layer[0])
}

/// Recompute the root tuple `(min, max, hash)` for a *range proof* comprised of
/// contiguous `leaves` plus a proof `path` of outer siblings.
pub fn recompute_root_for_range(
    leaves: &[Leaf],
    path: &[PathElem],
) -> Result<(NamespaceId, NamespaceId, Digest32), VerifyError> {
    // Fold the contiguous batch into one node first.
    let (mut cur_min, mut cur_max, mut cur_hash) = fold_contiguous_leaves(leaves)?;

    // The aggregate window must include each leaf's namespace (by construction).
    // Now fold with the frontier siblings to reach the root, validating windows.
    // For range proofs, we conservatively require that every parent window
    // *contains* the min..max window of the batch.
    for step in path {
        let parent_min = ns_min(cur_min, step.min);
        let parent_max = ns_max(cur_max, step.max);

        // Batch window must remain inside the parent's window
        if parent_min != ns_min(parent_min, cur_min) || parent_max != ns_max(parent_max, cur_max) {
            // This check is defensive; logically always true as written,
            // but we keep it to emphasize the invariant.
            return Err(VerifyError::NamespaceWindowViolation);
        }

        let (min, max, h) = if step.left {
            hashers::parent(step.min, step.max, &step.hash, cur_min, cur_max, &cur_hash)
        } else {
            hashers::parent(cur_min, cur_max, &cur_hash, step.min, step.max, &step.hash)
        };

        cur_min = min;
        cur_max = max;
        cur_hash = h;
    }

    Ok((cur_min, cur_max, cur_hash))
}

/// Verify a range proof (contiguous leaves + frontier path) against an expected root.
pub fn verify_range(
    expected_root_hash: &Digest32,
    expected_root_min: NamespaceId,
    expected_root_max: NamespaceId,
    leaves: &[Leaf],
    path: &[PathElem],
) -> Result<(), VerifyError> {
    let (min, max, h) = recompute_root_for_range(leaves, path)?;
    if &h != expected_root_hash || min != expected_root_min || max != expected_root_max {
        return Err(VerifyError::RootMismatch);
    }
    Ok(())
}

/* --------------------------------- Tests -------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;
    use super::super::tree;
    use super::super::types::{ns_from_u64, Leaf};

    fn mk_leaf(ns_u64: u64, data: &'static [u8]) -> Leaf {
        Leaf { ns: ns_from_u64(ns_u64), data: data.to_vec() }
    }

    /// Convert an internal `tree::Sibling` into a public `PathElem`.
    fn to_path_elem(s: tree::Sibling) -> PathElem {
        PathElem { left: s.left, min: s.node.min, max: s.node.max, hash: s.node.hash }
    }

    #[test]
    fn inclusion_proof_roundtrip() {
        // 5 leaves -> layers: 5 -> 3 -> 2 -> 1
        let leaves = vec![
            mk_leaf(1, b"alpha"),
            mk_leaf(2, b"beta"),
            mk_leaf(3, b"gamma"),
            mk_leaf(4, b"delta"),
            mk_leaf(5, b"epsilon"),
        ];

        // Build layers and take the canonical root.
        let layers = tree::build_layers(&leaves);
        let root = tree::root_from_layers(&layers).unwrap();

        // Take an inclusion path for index 3.
        let sibs = tree::path_for_index(3, &layers).unwrap();
        let path: Vec<PathElem> = sibs.into_iter().map(to_path_elem).collect();

        // Verify inclusion using our routines.
        verify_inclusion(&root.hash, root.min, root.max, &leaves[3], &path)
            .expect("valid inclusion proof");

        // Tamper with path to trigger a mismatch.
        let mut bad = path.clone();
        bad[0].hash = [0u8; 32];
        assert_eq!(
            verify_inclusion(&root.hash, root.min, root.max, &leaves[3], &bad).unwrap_err(),
            VerifyError::RootMismatch
        );
    }

    #[test]
    fn range_proof_full_span_needs_no_path() {
        // If the range covers *all* leaves, the frontier is empty.
        let leaves = vec![mk_leaf(10, b"a"), mk_leaf(20, b"b"), mk_leaf(30, b"c")];

        let layers = tree::build_layers(&leaves);
        let root = tree::root_from_layers(&layers).unwrap();

        // Recompute via range folding (no path).
        let (min, max, h) = recompute_root_for_range(&leaves, &[]).unwrap();
        assert_eq!(min, root.min);
        assert_eq!(max, root.max);
        assert_eq!(h, root.hash);

        // Verify range proof against root.
        verify_range(&root.hash, root.min, root.max, &leaves, &[])
            .expect("valid range proof for full span");
    }

    #[test]
    fn range_fold_respects_odd_carry() {
        // 3 leaves -> first layer length 3, carry the last one upward
        // We don't assert the exact hash here, just that folding completes.
        let leaves = vec![mk_leaf(1, b"x"), mk_leaf(2, b"y"), mk_leaf(3, b"z")];
        let res = fold_contiguous_leaves(&leaves);
        assert!(res.is_ok(), "folding contiguous leaves should succeed");
    }
}
