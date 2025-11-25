//! Animica addresses (bech32m "anim1..." human strings).
//!
//! Format:
//!   payload = alg_id(2 bytes, big-endian) || sha3_256(pubkey) (32 bytes)  => 34 bytes
//!   address = bech32m(hrp="anim", payload)
//!
//! This matches the Python & TypeScript SDKs and the on-chain bech32m codec.
//!
//! Example: `anim1qq...`
//!
//! Notes:
//! - Case-insensitive per bech32 rules; we always **emit lowercase**.
//! - No chain-id is encoded here; the address is algorithm+pubkey-hash only.

use crate::error::{Error, Result};
use bech32::{self, FromBase32, ToBase32, Variant};
use core::fmt::{Display, Formatter};
use core::str::FromStr;
use sha3::{Digest, Sha3_256};

pub const HRP: &str = "anim";

/// Fixed-length payload = 2 (alg_id) + 32 (sha3(pubkey))
pub const ADDRESS_PAYLOAD_LEN: usize = 34;

/// Canonical address object
#[derive(Clone, PartialEq, Eq, Hash)]
pub struct Address {
    /// Canonical Animica PQ algorithm id (u16 big-endian when encoded)
    pub alg_id: u16,
    /// sha3_256(public_key) digest
    pub hash: [u8; 32],
}

impl core::fmt::Debug for Address {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        write!(
            f,
            "Address{{alg_id=0x{:04x}, hash={}…}}",
            self.alg_id,
            hex::encode(&self.hash[..4])
        )
    }
}

impl Address {
    /// Derive an address from a raw public key for a given algorithm id.
    pub fn from_public_key(alg_id: u16, public_key: &[u8]) -> Self {
        let mut h = Sha3_256::new();
        h.update(public_key);
        let digest = h.finalize();
        let mut hash = [0u8; 32];
        hash.copy_from_slice(&digest);
        Self { alg_id, hash }
    }

    /// Build from raw payload bytes (34 bytes).
    pub fn from_payload(payload: &[u8]) -> Result<Self> {
        if payload.len() != ADDRESS_PAYLOAD_LEN {
            return Err(Error::Serde(format!(
                "address payload must be {} bytes, got {}",
                ADDRESS_PAYLOAD_LEN,
                payload.len()
            )));
        }
        let alg_id = u16::from_be_bytes([payload[0], payload[1]]);
        let mut hash = [0u8; 32];
        hash.copy_from_slice(&payload[2..34]);
        Ok(Self { alg_id, hash })
    }

    /// Return payload bytes (alg_id || hash).
    pub fn to_payload(&self) -> [u8; ADDRESS_PAYLOAD_LEN] {
        let mut out = [0u8; ADDRESS_PAYLOAD_LEN];
        out[0..2].copy_from_slice(&self.alg_id.to_be_bytes());
        out[2..].copy_from_slice(&self.hash);
        out
    }

    /// Encode to bech32m `anim1...` string.
    pub fn encode(&self) -> String {
        let payload = self.to_payload();
        bech32::encode(HRP, payload.to_base32(), Variant::Bech32m)
            .expect("bech32m encode never fails for valid input")
            .to_lowercase()
    }

    /// Decode from a bech32m `anim1...` string (strict HRP/variant checks and payload length).
    pub fn decode(s: &str) -> Result<Self> {
        let (hrp, data, variant) =
            bech32::decode(s).map_err(|e| Error::Serde(format!("bech32 decode: {e}")))?;
        if variant != Variant::Bech32m {
            return Err(Error::Serde("expected bech32m variant".into()));
        }
        if hrp.as_str() != HRP {
            return Err(Error::Serde(format!("invalid HRP: expected '{HRP}', got '{hrp}'")));
        }
        let bytes = Vec::<u8>::from_base32(&data)
            .map_err(|e| Error::Serde(format!("bech32 from_base32: {e}")))?;
        Self::from_payload(&bytes)
    }

    /// Quick boolean validator for a candidate address string.
    pub fn is_valid(s: &str) -> bool {
        Self::decode(s).is_ok()
    }
}

impl Display for Address {
    fn fmt(&self, f: &mut Formatter<'_>) -> core::fmt::Result {
        f.write_str(&self.encode())
    }
}

impl FromStr for Address {
    type Err = Error;
    fn from_str(s: &str) -> Result<Self> {
        Self::decode(s)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rand::RngCore;

    const DILITHIUM3: u16 = 0x0103;
    const SPHINCS_128S: u16 = 0x0201;

    #[test]
    fn roundtrip_from_pubkey() {
        // Fake public key bytes
        let mut pk = vec![0u8; 1952];
        rand::thread_rng().fill_bytes(&mut pk);

        let addr = Address::from_public_key(DILITHIUM3, &pk);
        let s = addr.encode();
        assert!(s.starts_with("anim1"));
        let back = Address::decode(&s).unwrap();
        assert_eq!(addr.alg_id, back.alg_id);
        assert_eq!(addr.hash, back.hash);
        assert_eq!(addr.to_string(), s);
    }

    #[test]
    fn strict_hrp_variant_and_len() {
        // Build a valid payload then deliberately break pieces.
        let addr = Address {
            alg_id: SPHINCS_128S,
            hash: [7u8; 32],
        };
        let good = addr.encode();
        assert!(Address::decode(&good).is_ok());

        // Wrong HRP
        let (hrp, data, _v) = bech32::decode(&good).unwrap();
        let wrong = bech32::encode("wrong", data, Variant::Bech32m).unwrap();
        assert!(Address::decode(&wrong).is_err());

        // Wrong variant (bech32 instead of bech32m)
        let wrong_v = bech32::encode(HRP, addr.to_payload().to_base32(), Variant::Bech32).unwrap();
        assert!(Address::decode(&wrong_v).is_err());

        // Wrong length
        let short_payload = vec![0u8; 10];
        let short = bech32::encode(HRP, short_payload.to_base32(), Variant::Bech32m).unwrap();
        assert!(Address::decode(&short).is_err());
    }

    #[test]
    fn payload_roundtrip() {
        let addr = Address {
            alg_id: 0xBEEF,
            hash: [0xAA; 32],
        };
        let p = addr.to_payload();
        let back = Address::from_payload(&p).unwrap();
        assert_eq!(addr, back);
    }

    #[test]
    fn parse_display_trait_impls() {
        let addr = Address {
            alg_id: 0x0001,
            hash: [0x11; 32],
        };
        let s = addr.to_string();
        let parsed: Address = s.parse().unwrap();
        assert_eq!(addr, parsed);
    }
}
