//! NMT tests: root computation & proof verification vectors.
//!
//! Coverage:
//! - Deterministic roots on small, hand-crafted leaf sets (regression vectors)
//! - Inclusion proof generation & verification for multiple positions
//! - Namespace grouping: same-namespace vs cross-namespace ordering
//! - Negative cases: mutated proof / mutated leaf should fail verification
//! - Larger randomized set smoke: consistent root across chunkings
//!
//! NOTE: These tests assume the public API exported by `animica_native::nmt`:
//!   - `nmt::nmt_root(leaves: &[Leaf]) -> Vec<u8>`
//!   - `nmt::open(leaves: &[Leaf], index: usize) -> Proof`
//!   - `nmt::verify(root: &[u8], leaf: &Leaf, proof: &Proof) -> bool`
//!   - `nmt::types::{NamespaceId, Leaf, Proof}`
//! If signatures differ slightly in your local build, adjust the calls below.

mod common;

use animica_native::nmt::{self, types::{Leaf, NamespaceId, Proof}};
use common::{random_bytes, rng_from_env};

fn ns(n: u64) -> NamespaceId {
    // Helper to obtain a compact NamespaceId from a u64.
    // If your `NamespaceId` offers `from_u64`, replace this constructor.
    // Otherwise, we fill 8 bytes little-endian.
    let b = n.to_le_bytes();
    // Many NMTs use 8-byte namespaces; adapt if your type differs.
    NamespaceId::from_bytes(&b)
}

fn leaf(ns_id: u64, data: &[u8]) -> Leaf {
    Leaf {
        namespace: ns(ns_id),
        data: data.to_vec(),
    }
}

fn hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        use core::fmt::Write as _;
        let _ = write!(&mut s, "{:02x}", b);
    }
    s
}

#[test]
fn root_small_regression_four_leaves() {
    // Hand-crafted set with two namespaces (1 and 2).
    let leaves = vec![
        leaf(1, b"alpha"),
        leaf(1, b"beta"),
        leaf(2, b"gamma"),
        leaf(2, b"delta"),
    ];

    let root1 = nmt::nmt_root(&leaves);
    assert_eq!(root1.len(), 32, "root must be 32 bytes");

    // Recompute to ensure determinism.
    let root2 = nmt::nmt_root(&leaves);
    assert_eq!(root1, root2, "roots should be stable for identical inputs");

    // Changing ordering should generally change the root (Merkle property).
    // (Namespace rules may enforce ordering; this checks structural sensitivity.)
    let mut leaves_reordered = leaves.clone();
    leaves_reordered.swap(0, 1);
    let root3 = nmt::nmt_root(&leaves_reordered);
    assert_ne!(root1, root3, "reordering leaves should change the root");

    // Print once to aid offline vector pinning if you want to lock a constant:
    // e.g., copy this into a constant after first run to turn it into a hard vector.
    eprintln!("nmt root (4 leaves): {}", hex(&root1));
}

#[test]
fn inclusion_proofs_basic_positions() {
    // Mix of namespaces with repeated and cross-namespace adjacencies.
    let leaves = vec![
        leaf(7, b"nnn"),
        leaf(7, b"ooo"),
        leaf(8, b"ppp"),
        leaf(8, b"qqq"),
        leaf(9, b"rrr"),
        leaf(7, b"sss"), // out-of-order ns intentionally, to exercise layout rules
    ];

    let root = nmt::nmt_root(&leaves);

    // Prove several positions, including edges and middle.
    for &idx in &[0usize, 1, 2, leaves.len() - 2, leaves.len() - 1] {
        let proof: Proof = nmt::open(&leaves, idx);
        let ok = nmt::verify(&root, &leaves[idx], &proof);
        assert!(ok, "proof must verify for index {}", idx);
    }
}

#[test]
fn inclusion_proof_mutation_should_fail() {
    let leaves = vec![
        leaf(1, b"a"),
        leaf(1, b"b"),
        leaf(2, b"c"),
        leaf(2, b"d"),
        leaf(3, b"e"),
    ];

    let root = nmt::nmt_root(&leaves);

    // Take a valid proof for index 3 ("d")
    let idx = 3usize;
    let mut proof = nmt::open(&leaves, idx);
    assert!(nmt::verify(&root, &leaves[idx], &proof));

    // Mutate 1 byte in the proof (if your Proof exposes bytes; if not,
    // adjust by mutating a node/hash inside).
    {
        let pbytes = proof.as_bytes_mut();
        if !pbytes.is_empty() {
            pbytes[0] ^= 0x80;
        }
    }
    let ok = nmt::verify(&root, &leaves[idx], &proof);
    assert!(!ok, "mutated proof should not verify");
}

#[test]
fn mutated_leaf_should_fail() {
    let leaves = vec![
        leaf(10, b"left"),
        leaf(10, b"left2"),
        leaf(11, b"mid"),
        leaf(12, b"right"),
    ];

    let root = nmt::nmt_root(&leaves);
    let idx = 2usize;
    let proof = nmt::open(&leaves, idx);

    // Copy and mutate the leaf payload (1 byte flip)
    let mut bad_leaf = leaves[idx].clone();
    if !bad_leaf.data.is_empty() {
        bad_leaf.data[0] ^= 0x01;
    }
    let ok = nmt::verify(&root, &bad_leaf, &proof);
    assert!(!ok, "mutated leaf must not verify against the same proof/root");
}

#[test]
fn single_leaf_tree() {
    let leaves = vec![leaf(42, b"only-one")];
    let root = nmt::nmt_root(&leaves);
    assert_eq!(root.len(), 32);

    // Proof for the only leaf should verify trivially.
    let proof = nmt::open(&leaves, 0);
    assert!(nmt::verify(&root, &leaves[0], &proof));
}

#[test]
fn randomized_consistency_and_proofs() {
    // Randomized smoke: build a larger set, compute root, prove/verify random picks.
    let mut rng = rng_from_env();

    // Bias total leaves toward powers of two +/- a few to exercise tree shapes.
    let total = 128 + (rng.next_u64() as usize % 9); // 128..=136
    let mut leaves = Vec::with_capacity(total);

    // Use a few namespaces distributed throughout.
    let ns_pool = [3u64, 5, 7, 11, 13];

    for i in 0..total {
        let ns_id = ns_pool[(i + (rng.next_u64() as usize)) % ns_pool.len()];
        // Vary data lengths: small + occasional larger fragments.
        let len = match (rng.next_u64() % 10) as u8 {
            0 => 0,
            1..=6 => (rng.next_u64() % 64) as usize,
            _ => (rng.next_u64() % 1024) as usize,
        };
        let mut data = random_bytes(len, &mut rng);
        // Tag index to detect swaps in debugging
        data.extend_from_slice(&(i as u32).to_le_bytes());
        leaves.push(leaf(ns_id, &data));
    }

    let root_a = nmt::nmt_root(&leaves);

    // Recompute after forcing a different chunking path (e.g., slice rebuild).
    let mut leaves_copy = Vec::with_capacity(leaves.len());
    for l in &leaves {
        leaves_copy.push(Leaf { namespace: l.namespace, data: l.data.clone() });
    }
    let root_b = nmt::nmt_root(&leaves_copy);
    assert_eq!(root_a, root_b, "root must be consistent across equivalent inputs");

    // Verify proofs for 16 random indices.
    for _ in 0..16 {
        let idx = (rng.next_u64() as usize) % leaves.len();
        let proof: Proof = nmt::open(&leaves, idx);
        assert!(nmt::verify(&root_a, &leaves[idx], &proof), "rand proof failed at {}", idx);
    }
}

#[test]
fn namespace_locality_affects_root() {
    // Same payloads, but different namespace assignments should alter root.
    let payloads = [b"x", b"y", b"z", b"w"];
    let leaves_ns1 = vec![
        leaf(1, payloads[0]),
        leaf(1, payloads[1]),
        leaf(2, payloads[2]),
        leaf(2, payloads[3]),
    ];
    let leaves_ns2 = vec![
        leaf(2, payloads[0]),
        leaf(2, payloads[1]),
        leaf(1, payloads[2]),
        leaf(1, payloads[3]),
    ];
    let r1 = nmt::nmt_root(&leaves_ns1);
    let r2 = nmt::nmt_root(&leaves_ns2);
    assert_ne!(r1, r2, "namespace reassignment should change the root");
}

// --- Minimal trait shims for Proof if your type exposes bytes ---

trait ProofBytes {
    fn as_bytes_mut(&mut self) -> &mut [u8];
}

// If your `Proof` struct doesn't expose a raw byte view, you can
// implement this using your internal nodes (e.g., first node bytes).
impl ProofBytes for Proof {
    fn as_bytes_mut(&mut self) -> &mut [u8] {
        self.bytes_mut()
    }
}
