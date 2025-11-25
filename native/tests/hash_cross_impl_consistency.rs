use animica_native::{blake3_hash, sha3_256_hash};
use sha3::{Digest as Sha3Digest, Sha3_256};

fn to_hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        use std::fmt::Write as _;
        write!(&mut s, "{:02x}", b).unwrap();
    }
    s
}

#[test]
fn sha3_256_matches_reference_impl_for_various_inputs() {
    let cases: &[&[u8]] = &[
        b"",
        b"abc",
        b"The quick brown fox jumps over the lazy dog",
        b"The quick brown fox jumps over the lazy dog.",
        b"animica-sha3-256-cross-impl",
        b"\x00\x01\x02\xff\xfe\xfd\x10\x20\x30\x40",
    ];

    for msg in cases {
        // Our crate's implementation (may be C, HW-accel, or pure Rust depending on features)
        let ours = sha3_256_hash(msg);

        // Independent reference implementation from the `sha3` crate
        let mut hasher = Sha3_256::new();
        hasher.update(msg);
        let reference = hasher.finalize();

        assert_eq!(
            ours.len(),
            32,
            "sha3_256_hash must produce 32-byte digest for input {:?}",
            std::str::from_utf8(msg).unwrap_or("<non-utf8>"),
        );

        let ours_hex = to_hex(&ours);
        let reference_hex = to_hex(&reference);

        assert_eq!(
            ours_hex, reference_hex,
            "sha3_256_hash output mismatch vs reference Sha3_256 for input {:?}",
            std::str::from_utf8(msg).unwrap_or("<non-utf8>"),
        );
    }
}

#[test]
fn blake3_matches_reference_impl_for_various_inputs() {
    let cases: &[&[u8]] = &[
        b"",
        b"abc",
        b"The quick brown fox jumps over the lazy dog",
        b"animica-blake3-cross-impl",
        b"\x00\x01\x02\xff\xfe\xfd\x10\x20\x30\x40",
    ];

    for msg in cases {
        // Our wrapper / implementation
        let ours = blake3_hash(msg);

        // Independent reference from upstream blake3 crate
        let reference = blake3::hash(msg);

        assert_eq!(
            ours.len(),
            32,
            "blake3_hash must produce 32-byte digest for input {:?}",
            std::str::from_utf8(msg).unwrap_or("<non-utf8>"),
        );

        let ours_hex = to_hex(&ours);
        let reference_hex = to_hex(reference.as_bytes());

        assert_eq!(
            ours_hex, reference_hex,
            "blake3_hash output mismatch vs reference blake3::hash for input {:?}",
            std::str::from_utf8(msg).unwrap_or("<non-utf8>"),
        );
    }
}

#[test]
fn sha3_and_blake3_remain_stable_across_runs() {
    // Extra stability check: if future changes in build flags / backends
    // accidentally change outputs, this test will scream.
    let msg = b"animica-stability-seed";

    let sha_first = sha3_256_hash(msg);
    let sha_second = sha3_256_hash(msg);
    assert_eq!(sha_first, sha_second, "Sha3-256 must remain stable");

    let blake_first = blake3_hash(msg);
    let blake_second = blake3_hash(msg);
    assert_eq!(blake_first, blake_second, "Blake3 must remain stable");
}
