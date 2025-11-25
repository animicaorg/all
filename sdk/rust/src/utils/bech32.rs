//! Bech32m helpers (encode/decode) for Animica-style addresses.
//!
//! Addresses are encoded as **bech32m** with a network-specific HRP (human readable
//! prefix). Canonical form is **lowercase**. This module provides simple helpers
//! to encode a raw payload (e.g., `alg_id || sha3_256(pubkey)`) and to decode
//! & validate an address string.
//!
//! Defaults:
//! - `DEFAULT_HRP` = `"anim"`
//! - Variant: **Bech32m**
//!
//! Notes:
//! - Mixed-case strings are rejected per BIP-0173.
//! - We accept either lowercase or uppercase input, but we always **emit lowercase**.

use crate::error::Error;
use bech32::{self, FromBase32, ToBase32, Variant};

/// Default HRP for mainnet-style addresses.
pub const DEFAULT_HRP: &str = "anim";

/// Quick HRP sanity check: 1..83 chars, lowercase `[a-z0-9]`, must start with a letter.
/// (Looser than spec in places but enough to catch typos in app code.)
pub fn is_valid_hrp(hrp: &str) -> bool {
    if hrp.is_empty() || hrp.len() > 83 {
        return false;
    }
    let mut chars = hrp.chars();
    match chars.next() {
        Some(c) if c.is_ascii_lowercase() => {}
        _ => return false,
    }
    hrp.chars()
        .all(|c| c.is_ascii_lowercase() && (c.is_ascii_alphanumeric()))
}

/// Encode a payload as a **lowercase** bech32m string with the given HRP.
///
/// # Errors
/// - `InvalidParams` if the HRP is invalid.
pub fn encode_address(hrp: &str, payload: &[u8]) -> Result<String, Error> {
    if !is_valid_hrp(hrp) {
        return Err(Error::InvalidParams("invalid HRP"));
    }
    let hrp_lc = hrp.to_ascii_lowercase();
    let data = payload.to_base32();
    Ok(bech32::encode(&hrp_lc, data, Variant::Bech32m)?)
}

/// Decode a bech32/bech32m address string into `(hrp, payload)`
///
/// - Mixed-case strings are rejected.
/// - The variant must be **Bech32m**.
///
/// This function accepts either uppercase or lowercase input, but **returns the
/// HRP as lowercase** and the raw payload bytes.
///
/// # Errors
/// - `InvalidParams` if casing is mixed or variant is not Bech32m, or decode fails.
pub fn decode_address(addr: &str) -> Result<(String, Vec<u8>), Error> {
    if addr.is_empty() {
        return Err(Error::InvalidParams("empty address"));
    }
    let lower = addr.to_ascii_lowercase();
    let upper = addr.to_ascii_uppercase();
    if addr != lower && addr != upper {
        return Err(Error::InvalidParams("mixed-case bech32 string"));
    }
    // Normalize to lowercase to simplify downstream handling.
    let (hrp, data, variant) = bech32::decode(&lower).map_err(|e| Error::InvalidParams(&format!("bech32 decode: {e}")))?;
    if variant != Variant::Bech32m {
        return Err(Error::InvalidParams("expected bech32m variant"));
    }
    let payload = Vec::<u8>::from_base32(&data)
        .map_err(|e| Error::InvalidParams(&format!("bech32 base32: {e}")))?;
    Ok((hrp, payload))
}

/// Validate an address for the **expected HRP** and return the payload bytes.
///
/// # Errors
/// - If HRP mismatches, variant not bech32m, or decoding fails.
pub fn decode_for_hrp(addr: &str, expected_hrp: &str) -> Result<Vec<u8>, Error> {
    let (hrp, payload) = decode_address(addr)?;
    if hrp != expected_hrp {
        return Err(Error::InvalidParams("hrp mismatch"));
    }
    Ok(payload)
}

/// Canonicalize an address string: decode + re-encode (forces lowercase HRP & checksum).
pub fn canonicalize(addr: &str) -> Result<String, Error> {
    let (hrp, payload) = decode_address(addr)?;
    encode_address(&hrp, &payload)
}

/// Quick boolean check for an address (any HRP). Use `decode_for_hrp` for strict checks.
pub fn is_valid_address(addr: &str) -> bool {
    decode_address(addr).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hrp_rules() {
        assert!(is_valid_hrp("anim"));
        assert!(!is_valid_hrp("")); // empty
        assert!(!is_valid_hrp("Anim")); // uppercase
        assert!(!is_valid_hrp("1anim")); // must start with letter
        assert!(is_valid_hrp("an1m")); // digits allowed after first char
    }

    #[test]
    fn roundtrip_default() {
        // 33-byte payload like: alg_id (1) || sha3_256(pubkey) (32)
        let payload = {
            let mut p = vec![0x01];
            p.extend(std::iter::repeat(0xAA).take(32));
            p
        };
        let addr = encode_address(DEFAULT_HRP, &payload).unwrap();
        assert!(addr.starts_with("anim1"));
        assert!(addr.chars().all(|c| !c.is_ascii_uppercase()));

        let (hrp, dec) = decode_address(&addr).unwrap();
        assert_eq!(hrp, DEFAULT_HRP);
        assert_eq!(dec, payload);
    }

    #[test]
    fn rejects_mixed_case() {
        let payload = b"\x01\x02\x03";
        let mut addr = encode_address("anim", payload).unwrap();
        // Introduce mixed case: flip a letter to uppercase (after the separator).
        addr.replace_range(2..3, &addr[2..3].to_ascii_uppercase());
        assert!(decode_address(&addr).is_err());
    }

    #[test]
    fn canonicalize_ok() {
        let p = vec![0, 1, 2, 3, 4, 5, 6];
        let a1 = encode_address("anim", &p).unwrap();
        let a2 = canonicalize(&a1.to_ascii_uppercase()).unwrap();
        assert_eq!(a1, a2);
    }

    #[test]
    fn decode_for_hrp_mismatch() {
        let p = vec![9u8; 10];
        let a = encode_address("anim", &p).unwrap();
        assert!(decode_for_hrp(&a, "animt").is_err());
        assert!(decode_for_hrp(&a, "anim").is_ok());
    }
}
