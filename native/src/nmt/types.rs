//! NMT core types: [`NamespaceId`], [`Leaf`], and [`Proof`].
//!
//! These are small, serializable (behind the `serde` feature) structures shared
//! across the native NMT implementation, DA sampling, and fuzz/property tests.
//!
//! ### Overview
//! - **NamespaceId** — fixed 8-byte identifier used to bucket leaves
//!   lexicographically. Big-endian for natural ordering.
//! - **Leaf** — a single `(namespace, payload)` pair (payload is a byte slice).
//! - **Proof** — a membership proof consisting of a sequence of sibling nodes
//!   from the leaf to the root. Each sibling carries its *namespace range*
//!   (`min_ns`, `max_ns`) so the verifier can reconstitute and check ranges.
//!
//! These types are intentionally decoupled from the tree builder so they can be
//! moved across FFI boundaries and used in external tooling with minimal deps.

use crate::hash::Digest32;

#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};

/// Width (in bytes) of a namespace identifier.
pub const NAMESPACE_BYTES: usize = 8;

/// 8-byte namespace identifier (lexicographic order; big-endian).
///
/// This is a simple alias to keep interop ergonomic with `[u8; 8]`.
/// Use [`ns_from_u64`] for a convenient construction in tests/fixtures.
pub type NamespaceId = [u8; NAMESPACE_BYTES];

/// Convenience: construct a big-endian [`NamespaceId`] from a `u64`.
#[inline]
pub const fn ns_from_u64(x: u64) -> NamespaceId {
    x.to_be_bytes()
}

/// Validate and convert a byte slice to a [`NamespaceId`].
///
/// Returns `None` if `bytes.len() != 8`.
#[inline]
pub fn ns_try_from_slice(bytes: &[u8]) -> Option<NamespaceId> {
    if bytes.len() == NAMESPACE_BYTES {
        let mut out = [0u8; NAMESPACE_BYTES];
        out.copy_from_slice(bytes);
        Some(out)
    } else {
        None
    }
}

/// A single NMT leaf containing a namespace and its payload.
///
/// The payload is **not** hashed or canonicalized here; hashing occurs in the
/// NMT builder (`leaf` domain separation) to keep this type lightweight.
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Leaf<'a> {
    pub ns: NamespaceId,
    #[cfg_attr(feature = "serde", serde(with = "serde_bytes"))]
    pub data: &'a [u8],
}

impl<'a> Leaf<'a> {
    /// Create a new leaf.
    #[inline]
    pub fn new(ns: NamespaceId, data: &'a [u8]) -> Self {
        Self { ns, data }
    }
}

/// A sibling node included in a Merkle path.
///
/// `is_left` indicates the sibling is on the **left** of the running hash
/// (i.e., the target node is the right child at this level).
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ProofNode {
    pub is_left: bool,
    pub min_ns: NamespaceId,
    pub max_ns: NamespaceId,
    #[cfg_attr(feature = "serde", serde(with = "serde_bytes"))]
    pub hash: Digest32,
}

/// Merkle membership proof from a leaf to the root.
///
/// The path is ordered from the leaf level upwards.
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Proof {
    pub path: Vec<ProofNode>,
}

/* --------------------------------- Tests ----------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ns_helpers() {
        let a = ns_from_u64(0x0102_0304_0506_0708);
        assert_eq!(a, [1,2,3,4,5,6,7,8]);

        let b = ns_try_from_slice(&[9,8,7,6,5,4,3,2]).unwrap();
        assert_eq!(b, [9,8,7,6,5,4,3,2]);

        assert!(ns_try_from_slice(&[0u8; 7]).is_none());
        assert!(ns_try_from_slice(&[0u8; 9]).is_none());
    }

    #[test]
    fn leaf_constructs() {
        let ns = ns_from_u64(7);
        let d = b"hello";
        let lf = Leaf::new(ns, d);
        assert_eq!(lf.ns, ns);
        assert_eq!(lf.data, d);
    }

    #[test]
    fn proof_shapes() {
        let p = Proof {
            path: vec![
                ProofNode {
                    is_left: true,
                    min_ns: ns_from_u64(1),
                    max_ns: ns_from_u64(3),
                    hash: [0u8; 32],
                },
                ProofNode {
                    is_left: false,
                    min_ns: ns_from_u64(0),
                    max_ns: ns_from_u64(9),
                    hash: [1u8; 32],
                },
            ],
        };
        assert_eq!(p.path.len(), 2);
        assert!(p.path[0].is_left);
        assert!(!p.path[1].is_left);
    }
}
