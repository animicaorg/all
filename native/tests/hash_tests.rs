//! Hash tests: fixed vectors and cross-implementation comparisons.
//!
//! Coverage:
//! - Known test vectors for SHA-256 and Keccak-256 (Ethereum variant)
//! - Cross-check BLAKE3 against the upstream `blake3` crate
//! - Randomized cross-impl checks (one-shot vs streaming, varying chunk sizes)
//! - Large input smoke checks to exercise parallel/optimized paths
//!
//! These are integration tests (crate-level) so they live under `native/tests/`.

mod common;

use common::{random_bytes, rng_from_env};
use animica_native::hash::bench_api as nhash;

// Dev-time references. These crates are already used by the library itself.
use blake3 as blake3_ref;
use sha2::{Digest as _, Sha256 as Sha256Ref};
use tiny_keccak::{Hasher as _, Keccak as KeccakRef};

fn hex_to_bytes(s: &str) -> Vec<u8> {
    let mut out = Vec::with_capacity(s.len() / 2);
    let mut b = 0u8;
    let mut hi = true;
    for c in s.bytes() {
        let v = match c {
            b'0'..=b'9' => c - b'0',
            b'a'..=b'f' => c - b'a' + 10,
            b'A'..=b'F' => c - b'A' + 10,
            b' ' | b'\n' | b'\r' | b'\t' | b'_' => continue,
            _ => panic!("invalid hex char: {}", c as char),
        };
        if hi {
            b = v << 4;
            hi = false;
        } else {
            b |= v;
            out.push(b);
            hi = true;
        }
    }
    assert!(hi, "odd-length hex input");
    out
}

fn assert_eq_hex(actual: &[u8], expected_hex: &str) {
    let expected = hex_to_bytes(expected_hex);
    assert_eq!(actual, expected.as_slice(), "mismatch vs expected hex");
}

#[test]
fn sha256_known_vectors() {
    // SHA-256 test vectors (RFC 4634):
    // "" (empty)
    let h_empty = nhash::sha256(&[]);
    assert_eq_hex(
        &h_empty,
        "e3b0c44298fc1c149afbf4c8996fb924\
         27ae41e4649b934ca495991b7852b855",
    );

    // "abc"
    let h_abc = nhash::sha256(b"abc");
    assert_eq_hex(
        &h_abc,
        "ba7816bf8f01cfea414140de5dae2223\
         b00361a396177a9cb410ff61f20015ad",
    );

    // Cross-check with upstream sha2 crate (streaming)
    let mut s = Sha256Ref::new();
    s.update(b"abc");
    let ref_digest = s.finalize();
    assert_eq!(&h_abc[..], &ref_digest[..]);
}

#[test]
fn keccak256_known_vectors() {
    // Keccak-256 (Ethereum): note this is NOT the NIST SHA3-256 vector set.
    // "" (empty)
    let h_empty = nhash::keccak256(&[]);
    assert_eq_hex(
        &h_empty,
        "c5d2460186f7233c927e7db2dcc703c0\
         e500b653ca82273b7bfad8045d85a470",
    );

    // "abc"
    let h_abc = nhash::keccak256(b"abc");
    assert_eq_hex(
        &h_abc,
        "4e03657aea45a94fc7d47ba826c8d667\
         c0d1e6e33a64a036ec44f58fa12d6c45",
    );

    // Cross-check with tiny-keccak (streaming)
    let mut k = KeccakRef::v256();
    k.update(b"abc");
    let mut out = [0u8; 32];
    k.finalize(&mut out);
    assert_eq!(&h_abc[..], &out[..]);
}

#[test]
fn blake3_cross_with_upstream() {
    // Known vectors are intentionally avoided here to prevent drift; instead
    // validate against the upstream reference crate for several inputs.

    // Empty
    let ours = nhash::blake3_hash(&[]);
    let ref_empty = blake3_ref::hash(&[]);
    assert_eq!(&ours[..], ref_empty.as_bytes());

    // "abc"
    let ours = nhash::blake3_hash(b"abc");
    let mut hasher = blake3_ref::Hasher::new();
    hasher.update(b"a");
    hasher.update(b"b");
    hasher.update(b"c");
    let ref_abc = hasher.finalize();
    assert_eq!(&ours[..], ref_abc.as_bytes());

    // Medium & large random payloads
    let mut rng = rng_from_env();
    for &len in &[1usize, 31, 32, 33, 1_024, 65_537, 1_000_000] {
        let data = random_bytes(len, &mut rng);
        let ours = nhash::blake3_hash(&data);
        let ref_one_shot = blake3_ref::hash(&data);
        assert_eq!(&ours[..], ref_one_shot.as_bytes());

        // Streaming with odd chunking
        let mut ref_stream = blake3_ref::Hasher::new();
        let mut i = 0usize;
        while i < data.len() {
            // Weird chunk pattern: 1, 2, 3, ..., 64, 1, 2, ...
            let step = ((i % 64) + 1).min(data.len() - i);
            ref_stream.update(&data[i..i + step]);
            i += step;
        }
        let ref_final = ref_stream.finalize();
        assert_eq!(&ours[..], ref_final.as_bytes());
    }
}

#[test]
fn randomized_cross_impl_all_three() {
    // Fuzz-ish: many random lengths, compare against reference crates.
    let mut rng = rng_from_env();

    // Keep counts modest for CI; still hits a wide shape of sizes.
    for _case in 0..64 {
        // Bias lengths toward small, with occasional larger payloads.
        let len = match (rng.next_u64() % 10) as u8 {
            0 => (rng.next_u64() % 1) as usize,        // 0
            1..=5 => (rng.next_u64() % 256) as usize,  // small
            6..=8 => (rng.next_u64() % 8192) as usize, // medium
            _ => (rng.next_u64() % 200_000) as usize,  // large-ish
        };
        let data = random_bytes(len, &mut rng);

        // SHA-256: one-shot (ours) vs reference (streaming)
        let got_sha = nhash::sha256(&data);
        let mut ref_sha = Sha256Ref::new();
        // Chunk weirdness to exercise incremental path
        let mut i = 0usize;
        while i < data.len() {
            let step = (1 + ((i * 31 + 7) % 97)).min(data.len() - i);
            ref_sha.update(&data[i..i + step]);
            i += step;
        }
        let ref_sha_bytes = ref_sha.finalize();
        assert_eq!(&got_sha[..], &ref_sha_bytes[..], "sha256 mismatch (len={})", len);

        // Keccak-256: ours vs tiny-keccak streaming
        let got_kec = nhash::keccak256(&data);
        let mut k = KeccakRef::v256();
        // Different chunk cadence
        let mut i = 0usize;
        while i < data.len() {
            let step = (1 + ((i * 17 + 13) % 111)).min(data.len() - i);
            k.update(&data[i..i + step]);
            i += step;
        }
        let mut ref_kec_out = [0u8; 32];
        k.finalize(&mut ref_kec_out);
        assert_eq!(&got_kec[..], &ref_kec_out[..], "keccak256 mismatch (len={})", len);

        // BLAKE3: ours vs upstream one-shot
        let got_b3 = nhash::blake3_hash(&data);
        let ref_b3 = blake3_ref::hash(&data);
        assert_eq!(&got_b3[..], ref_b3.as_bytes(), "blake3 mismatch (len={})", len);
    }
}

#[test]
fn large_input_smoke() {
    // Ensure correctness for multi-megabyte inputs (exercise parallel paths).
    let mut rng = rng_from_env();
    let data = random_bytes(4 * 1024 * 1024 + 7, &mut rng); // 4 MiB + 7

    // SHA-256
    let got_sha = nhash::sha256(&data);
    let ref_sha = Sha256Ref::digest(&data);
    assert_eq!(&got_sha[..], &ref_sha[..]);

    // Keccak-256
    let got_kec = nhash::keccak256(&data);
    let mut k = KeccakRef::v256();
    k.update(&data);
    let mut out = [0u8; 32];
    k.finalize(&mut out);
    assert_eq!(&got_kec[..], &out[..]);

    // BLAKE3
    let got_b3 = nhash::blake3_hash(&data);
    let ref_b3 = blake3_ref::hash(&data);
    assert_eq!(&got_b3[..], ref_b3.as_bytes());
}
