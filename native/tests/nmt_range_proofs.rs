use animica_native::nmt;

/// Helper: encode a u64 into an 8-byte big-endian namespace id.
fn ns(n: u64) -> [u8; 8] {
    n.to_be_bytes()
}

/// Multi-namespace fixture; indices are intentionally ordered.
fn leaves_fixture<'a>() -> Vec<([u8; 8], &'a [u8])> {
    vec![
        (ns(1), b"a".as_ref()),
        (ns(1), b"aa".as_ref()),
        (ns(2), b"bbb".as_ref()),
        (ns(3), b"cccc".as_ref()),
        (ns(5), b"ddddd".as_ref()),
        (ns(7), b"eeeeee".as_ref()),
        (ns(9), b"fffffff".as_ref()),
    ]
}

/// Build a range NMT for a contiguous leaf slice [start, end) and test that all
/// leaves in that range verify under the range root.
#[test]
fn contiguous_subrange_has_valid_root_and_proofs() {
    let leaves = leaves_fixture();

    // Pick an interior contiguous range [start, end).
    let start = 1usize;
    let end = 6usize;
    assert!(end <= leaves.len() && start < end);

    let range_leaves: Vec<([u8; 8], &[u8])> = leaves[start..end]
        .iter()
        .cloned()
        .collect();

    let range_root = nmt::nmt_root(&range_leaves)
        .expect("non-empty contiguous range should yield an NMT root");

    // For every leaf in the contiguous range, the range-local proofs must verify
    // against the range root.
    for (idx, (leaf_ns, leaf_data)) in range_leaves.iter().enumerate() {
        let proof = nmt::open(&range_leaves, idx)
            .expect("valid index inside range tree should yield a membership proof");

        assert!(
            nmt::verify(&range_root, *leaf_ns, *leaf_data, &proof),
            "range proof for local index {idx} should verify against range_root"
        );
    }
}

/// Check that mixing roots and proofs between the full tree and a subrange
/// behaves as expected: proofs are *scoped* to the tree they were generated from.
#[test]
fn mixing_full_tree_and_range_proofs_fails() {
    let leaves = leaves_fixture();

    // Full tree root.
    let full_root = nmt::nmt_root(&leaves)
        .expect("non-empty tree should yield a root");

    // A contiguous subrange [2, 5).
    let start = 2usize;
    let end = 5usize;
    let range_leaves: Vec<([u8; 8], &[u8])> = leaves[start..end]
        .iter()
        .cloned()
        .collect();
    let range_root = nmt::nmt_root(&range_leaves)
        .expect("non-empty range should yield a range root");

    // 1) Range proof should *not* verify against the full tree root.
    for (idx, (leaf_ns, leaf_data)) in range_leaves.iter().enumerate() {
        let proof = nmt::open(&range_leaves, idx)
            .expect("valid index inside range tree should yield a membership proof");

        assert!(
            !nmt::verify(&full_root, *leaf_ns, *leaf_data, &proof),
            "range-local proof for index {idx} must not verify against full_root"
        );
    }

    // 2) Full-tree proof should *not* verify against the range root.
    for (idx, (leaf_ns, leaf_data)) in leaves.iter().enumerate() {
        let proof = nmt::open(&leaves, idx)
            .expect("valid index inside full tree should yield a membership proof");

        assert!(
            !nmt::verify(&range_root, *leaf_ns, *leaf_data, &proof),
            "full-tree proof for index {idx} must not verify against range_root"
        );
    }
}

/// Error cases / edge behavior for ranges: empty ranges and degenerate slices.
#[test]
fn empty_and_degenerate_ranges_have_no_root_and_no_proofs() {
    let leaves = leaves_fixture();

    // Empty range: [k, k) => empty slice.
    for k in 0..=leaves.len() {
        let sub: Vec<([u8; 8], &[u8])> = leaves[k..k].iter().cloned().collect();
        assert!(
            nmt::nmt_root(&sub).is_none(),
            "empty subrange [{k},{k}) must not expose an NMT root"
        );
        assert!(
            nmt::open(&sub, 0).is_none(),
            "open() in an empty subrange [{k},{k}) must return None"
        );
    }

    // Out-of-range indices are already covered in other tests; here just sanity
    // check one obvious case for a non-empty slice.
    let non_empty: Vec<([u8; 8], &[u8])> = leaves[1..4].iter().cloned().collect();
    assert!(
        nmt::open(&non_empty, non_empty.len()).is_none(),
        "open() past the end of a non-empty range must return None"
    );
}
