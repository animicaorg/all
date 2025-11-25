use animica_native::nmt;

/// Helper: encode a u64 into an 8-byte big-endian namespace id.
fn ns(n: u64) -> [u8; 8] {
    n.to_be_bytes()
}

/// A fixture that matches the internal `root_and_verify_roundtrip_many` test.
fn mixed_namespace_leaves<'a>() -> Vec<([u8; 8], &'a [u8])> {
    vec![
        (ns(3), b"a".as_ref()),
        (ns(1), b"bb".as_ref()),
        (ns(9), b"ccc".as_ref()),
        (ns(5), b"dddd".as_ref()),
        (ns(5), b"eeee".as_ref()),
    ]
}

#[test]
fn inclusion_proofs_are_valid_for_mixed_namespaces() {
    let leaves = mixed_namespace_leaves();

    // Compute the NMT root from the real implementation.
    let root = nmt::nmt_root(&leaves)
        .expect("non-empty leaf set should yield an NMT root");

    // For each leaf, open a membership proof and verify it against `root`.
    for (idx, (leaf_ns, leaf_data)) in leaves.iter().enumerate() {
        let proof = nmt::open(&leaves, idx)
            .expect("valid index should yield a membership proof");

        assert!(
            nmt::verify(&root, *leaf_ns, *leaf_data, &proof),
            "proof for index {idx} should verify for the same leaf/root"
        );
    }
}

#[test]
fn open_rejects_out_of_range_indices_and_empty_tree() {
    // Empty tree: no root, no membership proofs.
    let empty: Vec<([u8; 8], &[u8])> = Vec::new();
    assert!(
        nmt::nmt_root(&empty).is_none(),
        "empty NMT must not expose a root"
    );
    assert!(
        nmt::open(&empty, 0).is_none(),
        "open() on an empty NMT must return None"
    );

    // Non-empty tree: out-of-range index must return None.
    let leaves = mixed_namespace_leaves();
    assert!(
        nmt::open(&leaves, leaves.len()).is_none(),
        "open() with an out-of-range index must return None"
    );
}
