use animica_native::nmt;

/// Helper: encode a u64 into an 8-byte big-endian namespace id.
fn ns(n: u64) -> [u8; 8] {
    n.to_be_bytes()
}

/// A multi-namespace fixture covering several distinct namespaces.
fn multi_namespace_leaves<'a>() -> Vec<([u8; 8], &'a [u8])> {
    vec![
        (ns(1), b"a".as_ref()),
        (ns(1), b"aa".as_ref()),
        (ns(2), b"bbb".as_ref()),
        (ns(3), b"cccc".as_ref()),
        (ns(5), b"ddddd".as_ref()),
        (ns(7), b"eeeeee".as_ref()),
        (ns(7), b"ffffff".as_ref()),
    ]
}

#[test]
fn multi_namespace_inclusion_proofs_are_valid() {
    let leaves = multi_namespace_leaves();

    let root = nmt::nmt_root(&leaves)
        .expect("non-empty multi-namespace tree should yield a root");

    for (idx, (leaf_ns, leaf_data)) in leaves.iter().enumerate() {
        let proof = nmt::open(&leaves, idx)
            .expect("valid index should yield a membership proof");

        assert!(
            nmt::verify(&root, *leaf_ns, *leaf_data, &proof),
            "proof for index {idx} should verify for the same leaf/root"
        );
    }
}

/// Verifies that proofs are *tied* to the correct namespace and payload:
/// - Changing the namespace with same payload must fail.
/// - Changing the payload with same namespace must fail.
#[test]
fn proofs_enforce_namespace_and_payload_boundaries() {
    let leaves = multi_namespace_leaves();
    let root = nmt::nmt_root(&leaves)
        .expect("non-empty tree should yield a root");

    for (idx, (leaf_ns, leaf_data)) in leaves.iter().enumerate() {
        let proof = nmt::open(&leaves, idx)
            .expect("valid index should yield a membership proof");

        // 1) Change namespace, keep payload → must fail.
        let wrong_ns = {
            // Just bump the numeric id; this keeps it a valid [u8; 8] ns.
            let id = u64::from_be_bytes(*leaf_ns);
            ns(id.wrapping_add(1))
        };
        assert!(
            !nmt::verify(&root, wrong_ns, *leaf_data, &proof),
            "proof for index {idx} should NOT verify if namespace is changed"
        );

        // 2) Change payload, keep namespace → must fail.
        let mut wrong_payload = leaf_data.to_vec();
        wrong_payload.push(0xFF); // simple, deterministic perturbation
        assert!(
            !nmt::verify(&root, *leaf_ns, &wrong_payload, &proof),
            "proof for index {idx} should NOT verify if payload is changed"
        );
    }
}

/// "Range-like" check: filter leaves by a namespace band and ensure that we can
/// build a subtree whose root is stable and whose leaves all verify against that
/// subtree root. This exercises ordering & boundary handling across namespaces.
#[test]
fn namespace_band_subtree_verifies_for_filtered_leaves() {
    let leaves = multi_namespace_leaves();

    // Choose a band [2, 7]; this includes namespaces 2, 3, 5, 7.
    let min_band = 2u64;
    let max_band = 7u64;

    let band_leaves: Vec<([u8; 8], &[u8])> = leaves
        .iter()
        .cloned()
        .filter(|(ns_bytes, _)| {
            let id = u64::from_be_bytes(*ns_bytes);
            id >= min_band && id <= max_band
        })
        .collect();

    assert!(
        !band_leaves.is_empty(),
        "fixture should yield at least one leaf in the namespace band"
    );

    let band_root = nmt::nmt_root(&band_leaves)
        .expect("non-empty band subtree should yield a root");

    // For the filtered tree, inclusion proofs must still verify under band_root.
    for (idx, (leaf_ns, leaf_data)) in band_leaves.iter().enumerate() {
        let proof = nmt::open(&band_leaves, idx)
            .expect("valid band index should yield a membership proof");

        assert!(
            nmt::verify(&band_root, *leaf_ns, *leaf_data, &proof),
            "band proof for index {idx} should verify against band_root"
        );
    }
}
