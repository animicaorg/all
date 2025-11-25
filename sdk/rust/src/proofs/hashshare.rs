//! HashShare local build & verify helpers (developer tools).
//!
//! This module mirrors the reference "u-draw" used by miners to produce
//! HashShare proofs. It does **not** implement the full on-chain envelope
//! from `proofs/` — instead it lets SDK users and tests:
//!   * deterministically derive the uniform draw `u ∈ (0,1)`
//!   * compute H(u) = -ln(u) in micro-nats (µ-nats)
//!   * compare against a micro-threshold Θ (Theta) to decide acceptance
//!   * optionally scan a nonce range to find an acceptable share
//!
//! Hashing domain:
//!   SHA3-256( DOMAIN || header_hash(32B) || nonce_le(8B) || mix_seed? )
//! where `DOMAIN = b"anim/hs/v1"` and `mix_seed` is optional bytes used
//! by some networks to bind additional entropy (e.g., a mix digest).
//!
//! The uniform `u` is derived from the first 16 bytes (big-endian) of the
//! digest: `u = (v + 1) / 2^128`, ensuring `u ∈ (0,1]` (never zero).

use crate::error::{Error, Result};
use hex::FromHex;
use serde::{Deserialize, Serialize};
use sha3::{Digest, Sha3_256};

const DOMAIN: &[u8] = b"anim/hs/v1";
const TWO_POW_128: f64 = 340282366920938463463374607431768211456.0; // 2^128 as f64

/// Inputs to a local HashShare draw.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HashShareInput {
    /// 0x-hex, 32 bytes (block header sign-domain hash).
    pub header_hash: String,
    /// 64-bit nonce interpreted as little-endian when hashed.
    pub nonce: u64,
    /// Optional 0x-hex seed mixed into the domain (e.g., mix digest).
    #[serde(default)]
    pub mix_seed: Option<String>,
}

/// Result of a local HashShare draw.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HashShareDraw {
    /// Full SHA3-256 digest (0x-hex) of the draw preimage.
    pub digest: String,
    /// Uniform draw in (0,1]; represented as f64 for convenience.
    pub u: f64,
    /// H(u) in micro-nats (µ-nats) = round(-ln(u) * 1e6).
    pub h_micro: u64,
}

/// Verification summary.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct VerifyResult {
    /// Whether H(u) ≥ Θ (micro-nats).
    pub accepted: bool,
    /// H(u) in micro-nats.
    pub h_micro: u64,
    /// Difficulty ratio: H(u) / Θ (>= 1.0 implies acceptance).
    pub d_ratio: f64,
    /// The underlying draw (digest and u).
    pub draw: HashShareDraw,
}

/// Perform a local draw from inputs.
pub fn draw(input: &HashShareInput) -> Result<HashShareDraw> {
    let header = hex32(&input.header_hash)?;
    let mut hasher = Sha3_256::new();
    hasher.update(DOMAIN);
    hasher.update(header);

    // Nonce little-endian (matches miner inner-loop conventions).
    let nonce_le = input.nonce.to_le_bytes();
    hasher.update(nonce_le);

    if let Some(ms) = &input.mix_seed {
        let mix = hex_decode(ms)?;
        hasher.update(&mix);
    }

    let digest = hasher.finalize();
    let digest_hex = hex0x(&digest);

    // Uniform u from first 16 bytes (big-endian) mapped into (0,1].
    let mut hi16 = [0u8; 16];
    hi16.copy_from_slice(&digest[..16]);
    let v = u128::from_be_bytes(hi16);
    // Avoid zero for -ln(u): map v=0 → 1 (smallest non-zero) to cap H(u).
    let v = if v == 0 { 1 } else { v };
    let u = (v as f64) / TWO_POW_128;
    let h_micro = (-u.ln() * 1_000_000.0).round() as u64;

    Ok(HashShareDraw {
        digest: digest_hex,
        u,
        h_micro,
    })
}

/// Verify a draw against a micro-threshold Θ (Theta).
pub fn verify(input: &HashShareInput, theta_micro: u64) -> Result<VerifyResult> {
    let d = draw(input)?;
    let d_ratio = if theta_micro == 0 {
        f64::INFINITY
    } else {
        (d.h_micro as f64) / (theta_micro as f64)
    };
    let accepted = d_ratio >= 1.0;
    Ok(VerifyResult {
        accepted,
        h_micro: d.h_micro,
        d_ratio,
        draw: d,
    })
}

/// Attempt to find an acceptable share by scanning a bounded number of nonces.
/// Returns `Ok(Some((nonce, result)))` on success, `Ok(None)` if not found.
pub fn scan_for_share(
    mut input: HashShareInput,
    theta_micro: u64,
    max_tries: u64,
) -> Result<Option<(u64, VerifyResult)>> {
    for _ in 0..max_tries {
        let vr = verify(&input, theta_micro)?;
        if vr.accepted {
            return Ok(Some((input.nonce, vr)));
        }
        input.nonce = input.nonce.wrapping_add(1);
    }
    Ok(None)
}

// ------------------------------- Helpers ------------------------------------

fn hex0x(bytes: impl AsRef<[u8]>) -> String {
    format!("0x{}", hex::encode(bytes))
}

fn hex_decode(s: &str) -> Result<Vec<u8>> {
    let s = s.strip_prefix("0x").unwrap_or(s);
    Ok(Vec::<u8>::from_hex(s).map_err(|e| Error::InvalidData(format!("hex decode: {e}")))?)
}

fn hex32(s: &str) -> Result<[u8; 32]> {
    let v = hex_decode(s)?;
    if v.len() != 32 {
        return Err(Error::InvalidData(format!(
            "expected 32-byte hex, got {} bytes",
            v.len()
        )));
    }
    let mut out = [0u8; 32];
    out.copy_from_slice(&v);
    Ok(out)
}

// --------------------------------- Tests ------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn draw_is_deterministic() {
        let input = HashShareInput {
            header_hash: "0x000102030405060708090a0b0c0d0e0f\
                          101112131415161718191a1b1c1d1e1f"
                .to_string(),
            nonce: 42,
            mix_seed: Some("0xdeadbeef".to_string()),
        };
        let d1 = draw(&input).unwrap();
        let d2 = draw(&input).unwrap();
        assert_eq!(d1.digest, d2.digest);
        assert!((d1.u - d2.u).abs() < 1e-18);
        assert_eq!(d1.h_micro, d2.h_micro);
        assert!(d1.u > 0.0 && d1.u <= 1.0);
    }

    #[test]
    fn verify_ratio_behaves() {
        let input = HashShareInput {
            header_hash: "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".into(),
            nonce: 1,
            mix_seed: None,
        };
        let vr = verify(&input, 1).unwrap();
        // Any non-zero H(u) over Θ=1 µ-nat should almost always be accepted;
        // but to avoid flakiness, check ratio is computed consistently.
        assert!(vr.d_ratio >= 0.0);
        assert_eq!(vr.accepted, vr.d_ratio >= 1.0);
    }

    #[test]
    fn scan_finds_or_not() {
        // Low Θ should be easy to satisfy.
        let mut found = false;
        let input = HashShareInput {
            header_hash: "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb".into(),
            nonce: 0,
            mix_seed: None,
        };
        if let Ok(Some((_n, vr))) = scan_for_share(input, 1, 1000) {
            found = vr.accepted;
        }
        assert!(found);

        // Extremely high Θ likely won't be met in a tiny window.
        let input2 = HashShareInput {
            header_hash: "0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc".into(),
            nonce: 0,
            mix_seed: None,
        };
        let none = scan_for_share(input2, 5_000_000_000, 128).unwrap();
        assert!(none.is_none());
    }

    #[test]
    fn hex_guards() {
        let bad = HashShareInput {
            header_hash: "0x1234".into(), // too short
            nonce: 0,
            mix_seed: None,
        };
        assert!(draw(&bad).is_err());
    }
}
