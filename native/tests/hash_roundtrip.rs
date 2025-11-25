use animica_native::{blake3_hash, sha3_256_hash};

fn to_hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        use std::fmt::Write as _;
        write!(&mut s, "{:02x}", b).unwrap();
    }
    s
}

#[test]
fn sha3_256_known_test_vectors() {
    // Test vectors from SHA3-256 standard (NIST):
    // "" (empty), "abc", "The quick brown fox jumps over the lazy dog"
    let cases = [
        (
            b"" as &[u8],
            "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a",
        ),
        (
            b"abc" as &[u8],
            "3a985da74fe225b2045c172d6bd390bd855f086e3e9d525b46bfe24511431532",
        ),
        (
            b"The quick brown fox jumps over the lazy dog" as &[u8],
            "69070dda01975c8c120c3aada1b282394e7f032fa9cf32f4cb2259a0897dfc04",
        ),
    ];

    for (msg, expected_hex) in cases {
        let digest = sha3_256_hash(msg);
        assert_eq!(32, digest.len(), "SHA3-256 must be 32 bytes");
        let got_hex = to_hex(&digest);
        assert_eq!(
            expected_hex,
            got_hex,
            "SHA3-256 mismatch for input {:?}",
            std::str::from_utf8(msg).unwrap_or("<non-utf8>")
        );
    }
}

#[test]
fn sha3_256_is_deterministic() {
    let msg = b"animica sha3-256 determinism check";
    let d1 = sha3_256_hash(msg);
    let d2 = sha3_256_hash(msg);
    assert_eq!(d1, d2, "SHA3-256 must be deterministic for same input");
    assert_eq!(32, d1.len());
}

#[test]
fn blake3_is_deterministic_and_32_bytes() {
    let msg = b"animica blake3 determinism check";
    let d1 = blake3_hash(msg);
    let d2 = blake3_hash(msg);
    assert_eq!(d1, d2, "BLAKE3 must be deterministic for same input");
    assert_eq!(32, d1.len(), "BLAKE3-256 digest must be 32 bytes");
}

#[test]
fn different_hash_functions_disagree_on_same_input() {
    let msg = b"same message, different hash functions";
    let blake = blake3_hash(msg);
    let sha3 = sha3_256_hash(msg);

    assert_ne!(
        blake, sha3,
        "BLAKE3 and SHA3-256 should not collide on a simple test string"
    );
}
