//! Hash roundtrip & sanity tests for animica_native.
//!
//! Goals:
//! - Prove hashing is deterministic (same input → same output).
//! - Prove different inputs produce different digests (basic collision sanity).
//! - Cross-check SHA-256 against canonical test vectors.
//! - Check basic shape/length for BLAKE3 and Keccak-256.
//!
//! Note:
//! - We intentionally **do not** assume a concrete return type for the hash
//!   functions: they can return `[u8; 32]` or `Vec<u8>`; everything is used
//!   via `AsRef<[u8]>`.
//! - For BLAKE3 and Keccak-256 we only test properties + length; you can
//!   later upgrade these to hard test vectors once those are frozen.

use animica_native::hash;

/// Convert bytes to lowercase hex string.
fn to_hex(bytes: impl AsRef<[u8]>) -> String {
    bytes
        .as_ref()
        .iter()
        .map(|b| format!("{:02x}", b))
        .collect()
}

/// Basic helper so tests fail with a clear message when a feature/hash
/// function is missing at runtime (e.g. compiled without `c_keccak`).
fn expect_ok<T, E: core::fmt::Display>(res: Result<T, E>, ctx: &str) -> T {
    match res {
        Ok(v) => v,
        Err(e) => panic!("{ctx} failed: {e}"),
    }
}

// --- SHA-256 ---------------------------------------------------------------

#[test]
fn sha256_known_test_vectors() {
    // Canonical SHA-256 test vectors (RFC 6234 / widespread usage)
    //  - "" (empty string)
    //  - "abc"
    //  - "The quick brown fox jumps over the lazy dog"
    let cases: &[(&[u8], &str)] = &[
        (
            b"" as &[u8],
            "e3b0c44298fc1c149afbf4c8996fb924\
             27ae41e4649b934ca495991b7852b855",
        ),
        (
            b"abc" as &[u8],
            "ba7816bf8f01cfea414140de5dae2223\
             b00361a396177a9cb410ff61f20015ad",
        ),
        (
            b"The quick brown fox jumps over the lazy dog" as &[u8],
            "d7a8fbb307d7809469ca9abcb0082e4f\
             8d5651e46d3cdb762d02d0bf37c9e592",
        ),
    ];

    for (msg, expected_hex) in cases {
        let digest = expect_ok(hash::sha256(msg), "sha256");
        let got = to_hex(digest);
        let expected_flat: String = expected_hex.chars().filter(|c| !c.is_whitespace()).collect();
        assert_eq!(
            got, expected_flat,
            "sha256({:?}) mismatch:\n  expected: {}\n       got: {}",
            core::str::from_utf8(msg).unwrap_or("<non-utf8>"),
            expected_flat,
            got
        );
    }
}

#[test]
fn sha256_is_deterministic_and_len_32() {
    let msg = b"animica-sha256";
    let d1 = expect_ok(hash::sha256(msg), "sha256");
    let d2 = expect_ok(hash::sha256(msg), "sha256");
    let d3 = expect_ok(hash::sha256(b"animica-sha256!"), "sha256");

    let h1 = to_hex(&d1);
    let h2 = to_hex(&d2);
    let h3 = to_hex(&d3);

    assert_eq!(h1, h2, "sha256 should be deterministic for same input");
    assert_ne!(h1, h3, "sha256 should differ for different inputs");
    assert_eq!(d1.as_ref().len(), 32, "sha256 digest should be 32 bytes");
    assert_eq!(d3.as_ref().len(), 32, "sha256 digest should be 32 bytes");
}

// --- Keccak-256 / SHA3-256 -------------------------------------------------
//
// We don't hard-code test vectors here because the implementation may be
// parameterised as "Keccak-256" vs "SHA3-256". Instead we:
//   - enforce determinism
//   - enforce 32-byte output
//   - enforce that different inputs produce different outputs
//
// Once the consensus choice is fully frozen, you can replace/augment this
// with canonical vectors.

#[test]
fn keccak256_is_deterministic_and_len_32() {
    // If keccak is not built (e.g. missing `c_keccak` feature), we want this to
    // fail loudly rather than silently skipping.
    let msg = b"animica-keccak";
    let d1 = expect_ok(hash::keccak256(msg), "keccak256");
    let d2 = expect_ok(hash::keccak256(msg), "keccak256");
    let d3 = expect_ok(hash::keccak256(b"animica-keccak!"), "keccak256");

    let h1 = to_hex(&d1);
    let h2 = to_hex(&d2);
    let h3 = to_hex(&d3);

    assert_eq!(h1, h2, "keccak256 should be deterministic for same input");
    assert_ne!(h1, h3, "keccak256 should differ for different inputs");
    assert_eq!(d1.as_ref().len(), 32, "keccak256 digest should be 32 bytes");
    assert_eq!(d3.as_ref().len(), 32, "keccak256 digest should be 32 bytes");
}

// --- BLAKE3 ----------------------------------------------------------------
//
// Here we focus on:
//   - determinism
//   - 32-byte output length
//   - basic distinctness for different inputs
//
// You can later wire in official BLAKE3 test vectors if you want stricter
// guarantees inside this crate, but the upstream BLAKE3 crate already
// self-tests extensively.

#[test]
fn blake3_is_deterministic_and_len_32() {
    let msg = b"animica-blake3";
    let d1 = expect_ok(hash::blake3(msg), "blake3");
    let d2 = expect_ok(hash::blake3(msg), "blake3");
    let d3 = expect_ok(hash::blake3(b"animica-blake3!"), "blake3");

    let h1 = to_hex(&d1);
    let h2 = to_hex(&d2);
    let h3 = to_hex(&d3);

    assert_eq!(h1, h2, "blake3 should be deterministic for same input");
    assert_ne!(h1, h3, "blake3 should differ for different inputs");
    assert_eq!(d1.as_ref().len(), 32, "blake3 digest should be 32 bytes");
    assert_eq!(d3.as_ref().len(), 32, "blake3 digest should be 32 bytes");
}

// --- Cross-hash invariants -------------------------------------------------

#[test]
fn different_hash_functions_disagree_on_same_input() {
    let msg = b"animica-hash-kind";

    let s = expect_ok(hash::sha256(msg), "sha256");
    let k = expect_ok(hash::keccak256(msg), "keccak256");
    let b = expect_ok(hash::blake3(msg), "blake3");

    let hs = to_hex(&s);
    let hk = to_hex(&k);
    let hb = to_hex(&b);

    // It's very unlikely these collide; if they do, something is very wrong
    // (e.g. mis-wiring of hash functions).
    assert_ne!(hs, hk, "sha256 and keccak256 must not be wired to same function");
    assert_ne!(hs, hb, "sha256 and blake3 must not be wired to same function");
    assert_ne!(hk, hb, "keccak256 and blake3 must not be wired to same function");
}
