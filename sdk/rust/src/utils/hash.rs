//! Hash helpers for the Rust SDK.
//!
//! Provided algorithms:
//! - `sha3_256`, `sha3_512` (NIST SHA-3 family)
//! - `keccak256` (legacy Keccak-256, Ethereum-style padding)
//! - `blake3_256` (optional via `features = ["blake3"]`)
//!
//! Also includes simple **domain-separated** hashing helpers used throughout the
//! Animica stack to avoid cross-protocol collisions. The domain prefix is
//! `b"animica|" + tag.as_bytes() + b"|"`
use crate::error::Error;
use crate::utils::bytes::hex_encode;
use sha3::{Digest, Sha3_256, Sha3_512, Keccak256};

#[cfg(feature = "blake3")]
use blake3;

/// SHA3-256 digest.
#[inline]
pub fn sha3_256<B: AsRef<[u8]>>(bytes: B) -> [u8; 32] {
    let mut hasher = Sha3_256::new();
    hasher.update(bytes.as_ref());
    let out = hasher.finalize();
    out.into()
}

/// SHA3-512 digest.
#[inline]
pub fn sha3_512<B: AsRef<[u8]>>(bytes: B) -> [u8; 64] {
    let mut hasher = Sha3_512::new();
    hasher.update(bytes.as_ref());
    let out = hasher.finalize();
    out.into()
}

/// Keccak-256 digest (pre-SHA3 padding), commonly used for ABI selectors.
#[inline]
pub fn keccak256<B: AsRef<[u8]>>(bytes: B) -> [u8; 32] {
    let mut hasher = Keccak256::new();
    hasher.update(bytes.as_ref());
    let out = hasher.finalize();
    out.into()
}

/// BLAKE3-256 digest (feature-gated).
#[cfg(feature = "blake3")]
#[inline]
pub fn blake3_256<B: AsRef<[u8]>>(bytes: B) -> [u8; 32] {
    let hash = blake3::hash(bytes.as_ref());
    *hash.as_bytes()
}

/// Domain-separated SHA3-256: H = SHA3-256("animica|{tag}|" || data)
#[inline]
pub fn sha3_256_domain(tag: &str, data: &[u8]) -> [u8; 32] {
    let mut hasher = Sha3_256::new();
    hasher.update(b"animica|");
    hasher.update(tag.as_bytes());
    hasher.update(b"|");
    hasher.update(data);
    hasher.finalize().into()
}

/// Domain-separated SHA3-256 over multiple parts with length-prefixing to avoid ambiguity.
///
/// Layout:
/// `prefix = "animica|{tag}|"` then for each part: `u32_be(len) || part`
pub fn sha3_256_domain_parts(tag: &str, parts: &[&[u8]]) -> [u8; 32] {
    let mut hasher = Sha3_256::new();
    hasher.update(b"animica|");
    hasher.update(tag.as_bytes());
    hasher.update(b"|");
    for p in parts {
        let len = p.len() as u32;
        hasher.update(len.to_be_bytes());
        hasher.update(p);
    }
    hasher.finalize().into()
}

/// Hex helpers for digests -----------------------------------------------------

#[inline]
pub fn sha3_256_hex<B: AsRef<[u8]>>(bytes: B) -> String {
    hex_encode(sha3_256(bytes))
}

#[inline]
pub fn sha3_512_hex<B: AsRef<[u8]>>(bytes: B) -> String {
    hex_encode(sha3_512(bytes))
}

#[inline]
pub fn keccak256_hex<B: AsRef<[u8]>>(bytes: B) -> String {
    hex_encode(keccak256(bytes))
}

#[cfg(feature = "blake3")]
#[inline]
pub fn blake3_256_hex<B: AsRef<[u8]>>(bytes: B) -> String {
    hex_encode(blake3_256(bytes))
}

/// Utility: compute a 4-byte function selector using Keccak-256 of the signature string.
///
/// Example: `"inc(uint64)"` → first 4 bytes (big-endian order as bytes slice).
pub fn selector4(signature: &str) -> [u8; 4] {
    let h = keccak256(signature.as_bytes());
    [h[0], h[1], h[2], h[3]]
}

/// Utility: checksum a payload and compare to expected `0x`-hex digest (SHA3-256).
pub fn verify_sha3_256_hex(payload: &[u8], expected_hex: &str) -> Result<(), Error> {
    let got = sha3_256_hex(payload);
    if got.eq_ignore_ascii_case(expected_hex) {
        Ok(())
    } else {
        Err(Error::VerifyFailed(format!(
            "sha3-256 mismatch: expected {}, got {}",
            expected_hex, got
        )))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sha3_vectors_smoke() {
        // Simple stability checks against known hex strings.
        let h = sha3_256_hex(b"");
        assert_eq!(h, "0xa7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a");

        let h512 = sha3_512_hex(b"abc");
        assert_eq!(
            h512,
            "0xb751850b1a57168a5693cd924b6b096e08f621827444f70d884f5d0240d2712e10e116e9192af3c91a7ec57647e3934057340b4cf408d5a56592f8274eec53f0"
        );
    }

    #[test]
    fn keccak_selector() {
        // Keccak256("transfer(address,uint256)") → 0xa9059cbb
        let sel = selector4("transfer(address,uint256)");
        assert_eq!(hex_encode(sel), "0xa9059cbb");
    }

    #[test]
    fn domain_parts_len_prefixing() {
        let a = b"alpha";
        let b = b"beta";
        let h1 = sha3_256_domain_parts("test", &[a, b]);
        let h2 = sha3_256_domain("test", &[a, b].concat());
        // Different because of len-prefixing and no delimiter in the single-call version.
        assert_ne!(h1, h2);
    }

    #[test]
    fn verify_ok_and_fail() {
        let data = b"xyz";
        let ok = sha3_256_hex(data);
        assert!(verify_sha3_256_hex(data, &ok).is_ok());
        assert!(verify_sha3_256_hex(data, "0xdeadbeef").is_err());
    }
}
