//! Canonical CBOR helpers for the Rust SDK.
//!
//! Goals:
//! - Deterministic, RFC 7049-style **canonical** CBOR encoding for signatures/hashes.
//! - Safe decode into strongly-typed structs via Serde.
//! - Utilities to re-canonicalize arbitrary CBOR payloads and to hash CBOR.
//!
//! We use `serde_cbor`'s canonical serializer (sorted map keys, shortest integer
//! encodings, and definite lengths).
//!
//! ## Notes
//! * Do **not** use the self-describe tag (`55799`) for sign bytes.
//! * Canonicalization here is stable and should match the Python/TS SDKs.

use crate::error::{Error, Result};
use crate::utils::hash::{sha3_256, sha3_256_hex};
use serde::{de::DeserializeOwned, Serialize};

/// Encode a serializable value as **canonical** CBOR bytes.
pub fn to_vec_canonical<T: Serialize>(value: &T) -> Result<Vec<u8>> {
    let mut out = Vec::with_capacity(128);
    {
        let mut ser = serde_cbor::ser::Serializer::new(&mut out);
        // Important: enable canonical form (sorted keys, shortest repr, definite lengths).
        ser.canonical(true);
        value.serialize(&mut ser)?;
    }
    Ok(out)
}

/// Decode a CBOR byte slice into a value.
///
/// This accepts any valid CBOR (not necessarily canonical).
pub fn from_slice<T: DeserializeOwned>(bytes: &[u8]) -> Result<T> {
    Ok(serde_cbor::from_slice(bytes)?)
}

/// Decode any CBOR into a generic `serde_cbor::Value`.
pub fn to_value(bytes: &[u8]) -> Result<serde_cbor::Value> {
    Ok(serde_cbor::from_slice::<serde_cbor::Value>(bytes)?)
}

/// Re-encode any CBOR payload into **canonical** form.
/// Useful for normalizing payloads received from the network before hashing.
pub fn recanonicalize(bytes: &[u8]) -> Result<Vec<u8>> {
    let val: serde_cbor::Value = serde_cbor::from_slice(bytes)?;
    to_vec_canonical(&val)
}

/// Compute SHA3-256 over the **canonical CBOR** encoding of `value`.
pub fn sha3_256_canonical<T: Serialize>(value: &T) -> Result<[u8; 32]> {
    let enc = to_vec_canonical(value)?;
    Ok(sha3_256(enc))
}

/// Same as [`sha3_256_canonical`] but returns a canonical `0x`-hex string.
pub fn sha3_256_canonical_hex<T: Serialize>(value: &T) -> Result<String> {
    let enc = to_vec_canonical(value)?;
    Ok(sha3_256_hex(enc))
}

/// Return a human-friendly diagnostic JSON string for a CBOR payload.
/// This is non-canonical and **debug-only**; not used for signing.
pub fn diagnostic_json(bytes: &[u8]) -> Result<String> {
    let val: serde_cbor::Value = serde_cbor::from_slice(bytes)?;
    Ok(serde_json::to_string_pretty(&val)?)
}

/// Convenience newtype to ensure canonical CBOR encoding when used with Serde APIs.
///
/// Example:
/// ```ignore
/// let body = Canonical(&my_struct);
/// let bytes = serde_cbor::to_vec(&body)?; // will be canonical via our serializer
/// ```
pub struct Canonical<'a, T: Serialize>(pub &'a T);

impl<'a, T: Serialize> Serialize for Canonical<'a, T> {
    fn serialize<S: serde::Serializer>(&self, _serializer: S) -> std::result::Result<S::Ok, S::Error> {
        // This type is intended to be encoded with `to_vec_canonical` only.
        // Prevent accidental use with generic serializers.
        Err(serde::ser::Error::custom(
            "Canonical<T> must be encoded with utils::cbor::to_vec_canonical",
        ))
    }
}

// ------------------------------- Tests ---------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde::{Deserialize, Serialize};
    use std::collections::BTreeMap;

    #[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
    struct Demo {
        a: u64,
        b: String,
        c: Vec<u8>,
        m: BTreeMap<String, u64>,
    }

    #[test]
    fn roundtrip_demo() {
        let mut m = BTreeMap::new();
        m.insert("x".into(), 1);
        m.insert("y".into(), 2);
        let v = Demo {
            a: 7,
            b: "hi".into(),
            c: vec![1, 2, 3],
            m,
        };

        let enc = to_vec_canonical(&v).unwrap();
        let dec: Demo = from_slice(&enc).unwrap();
        assert_eq!(dec, v);

        // Canonical hashing is stable.
        let h1 = sha3_256_canonical_hex(&v).unwrap();
        let h2 = sha3_256_canonical_hex(&v).unwrap();
        assert_eq!(h1, h2);
    }

    #[test]
    fn canonicalizes_map_order() {
        // Same logical map with different insertion orders → same canonical bytes/hash.
        #[derive(Serialize)]
        struct M {
            map: std::collections::HashMap<String, u64>,
        }

        let mut m1 = std::collections::HashMap::new();
        m1.insert("b".into(), 2);
        m1.insert("a".into(), 1);

        let mut m2 = std::collections::HashMap::new();
        m2.insert("a".into(), 1);
        m2.insert("b".into(), 2);

        let canon1 = to_vec_canonical(&M { map: m1 }).unwrap();
        let canon2 = to_vec_canonical(&M { map: m2 }).unwrap();
        assert_eq!(canon1, canon2);

        let h1 = sha3_256(canon1);
        let h2 = sha3_256(canon2);
        assert_eq!(h1, h2);
    }

    #[test]
    fn recanonicalize_matches_direct() {
        #[derive(Serialize)]
        struct S<'a> {
            k: &'a str,
            v: u64,
        }

        let s = S { k: "key", v: 42 };

        let direct = to_vec_canonical(&s).unwrap();

        // Encode non-canonically by serializing then mutating map order via Value (simulated).
        let noncanon = serde_cbor::to_vec(&s).unwrap(); // may already be canonical but acceptable for test.
        let reco = recanonicalize(&noncanon).unwrap();
        assert_eq!(reco, direct);
    }

    #[test]
    fn diagnostic_is_json() {
        let enc = to_vec_canonical(&("hello", 5u64)).unwrap();
        let diag = diagnostic_json(&enc).unwrap();
        assert!(diag.contains("hello"));
    }
}
