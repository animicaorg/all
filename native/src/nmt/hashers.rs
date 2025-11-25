//! Namespace-aware hash combiners for Namespaced Merkle Trees (NMT).
//!
//! This module defines *domain-separated* hashing rules for:
//! - **Leaves**: commit to the leaf's namespace and payload
//! - **Inner nodes**: commit to *both* children's namespace ranges and hashes
//!
//! The resulting parent node also carries the aggregated namespace range
//! `[min(left.min), max(right.max)]` (lexicographic over big-endian bytes).
//!
//! # Design notes
//! - We *domain-separate* leaves vs inner nodes using distinct 1-byte tags.
//! - Inner-node digest commits to:
//!   `DS_NODE || l.min || l.max || l.hash || r.min || r.max || r.hash`
//! - Leaf digest commits to:
//!   `DS_LEAF || ns || data`
//!
//! This ensures the namespace bounds are *binding* (i.e., not malleable via a
//! proof that only includes raw child hashes). Left/right order matters.
//!
//! The implementation uses BLAKE3 as the internal compression function for
//! speed. If you need a different hash, add thin wrappers or swap the
//! `hash_with_domain` implementation.

use crate::hash::Digest32;
use super::types::NamespaceId;

/// Domain separation tags.
const DS_LEAF: u8 = 0x00;
const DS_NODE: u8 = 0x01;

/// Compute a BLAKE3 hash over `domain || parts...`.
#[inline]
fn hash_with_domain(domain: u8, parts: &[&[u8]]) -> Digest32 {
    let mut h = blake3::Hasher::new();
    h.update(&[domain]);
    for p in parts {
        h.update(p);
    }
    let out = h.finalize();
    *out.as_bytes()
}

/// Compute a leaf digest (namespace-aware).
///
/// Returns `(min_ns, max_ns, digest)` where `min_ns == max_ns == ns`.
#[inline]
pub fn leaf(ns: NamespaceId, data: &[u8]) -> (NamespaceId, NamespaceId, Digest32) {
    let digest = hash_with_domain(DS_LEAF, &[&ns, data]);
    (ns, ns, digest)
}

/// Combine two children (left/right) into a parent namespace-aware digest.
///
/// Inputs are `(min_ns, max_ns, hash)` tuples. The returned tuple is the
/// parent's `(min_ns, max_ns, hash)`.
///
/// **Important:** Order is significant. Calling with `(right, left)` will
/// produce a different digest than `(left, right)`.
#[inline]
pub fn parent(
    left_min: NamespaceId,
    left_max: NamespaceId,
    left_hash: &Digest32,
    right_min: NamespaceId,
    right_max: NamespaceId,
    right_hash: &Digest32,
) -> (NamespaceId, NamespaceId, Digest32) {
    let min_ns = if left_min <= right_min { left_min } else { right_min };
    let max_ns = if left_max >= right_max { left_max } else { right_max };

    let digest = hash_with_domain(
        DS_NODE,
        &[
            &left_min,
            &left_max,
            left_hash,
            &right_min,
            &right_max,
            right_hash,
        ],
    );
    (min_ns, max_ns, digest)
}

/* --------------------------------- Tests ----------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;
    use super::super::types::ns_from_u64;

    #[test]
    fn leaf_commit_includes_namespace() {
        let ns_a = ns_from_u64(1);
        let ns_b = ns_from_u64(2);
        let (_, _, ha) = leaf(ns_a, b"payload");
        let (_, _, hb) = leaf(ns_b, b"payload");
        assert_ne!(ha, hb, "different namespaces must change the leaf digest");
    }

    #[test]
    fn parent_commits_children_bounds_and_order() {
        let la = ns_from_u64(1);
        let lb = ns_from_u64(1);
        let (_, _, lh) = leaf(la, b"a");

        let ra = ns_from_u64(9);
        let rb = ns_from_u64(9);
        let (_, _, rh) = leaf(ra, b"b");

        let (min1, max1, p1) = parent(la, lb, &lh, ra, rb, &rh);
        let (min2, max2, p2) = parent(ra, rb, &rh, la, lb, &lh);

        assert_eq!(min1, la);
        assert_eq!(max1, rb);
        assert_ne!(p1, p2, "left/right order must affect the parent digest");

        // Ranges must aggregate correctly regardless of order.
        assert_eq!(min2, la.min(ra));
        assert_eq!(max2, lb.max(rb));
    }

    #[test]
    fn parent_commits_to_child_bounds_not_only_hashes() {
        let ns1 = ns_from_u64(3);
        let ns2 = ns_from_u64(7);
        let (_, _, h_same) = leaf(ns1, b"same");
        let (_, _, h_same2) = leaf(ns1, b"same");
        assert_eq!(h_same, h_same2);

        // Two children with identical *hashes* but different namespace ranges
        // should not yield the same parent if we flip the ranges without updating
        // the inputs, because ranges are committed inside the parent hash.
        let (min_a, max_a, p_a) = parent(ns1, ns1, &h_same, ns2, ns2, &h_same);
        let (min_b, max_b, p_b) = parent(ns2, ns2, &h_same, ns1, ns1, &h_same);
        assert_ne!(p_a, p_b);
        assert_eq!(min_a, ns1.min(ns2));
        assert_eq!(max_a, ns1.max(ns2));
        assert_eq!(min_b, ns1.min(ns2));
        assert_eq!(max_b, ns1.max(ns2));
    }
}
