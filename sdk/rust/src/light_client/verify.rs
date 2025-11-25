//! Light client verification: header linkage and DA light proofs.
//!
//! This module performs **offline** checks suitable for wallet/SDK usage:
//! - Header chain linkage: `parentHash` continuity, monotonic height.
//! - Optional DA light proof verification against a header's `daRoot`.
//!
//! It intentionally avoids full consensus validation. For production,
//! pair this with trusted checkpoints (anchor headers) and server-side
//! SPV/consensus checks where applicable.

use hex::FromHex;
use serde::{Deserialize, Serialize};
use serde_json::Value as JsonValue;
use sha3::{Digest, Sha3_256};
use std::collections::BTreeMap;

/// Minimal header view (as returned by RPC explorers or node light endpoints).
/// Unknown fields are preserved in `extra` for forward compatibility.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LightHeader {
    pub hash: String,         // 0x-prefixed
    pub parent_hash: String,  // 0x-prefixed
    #[serde(alias = "height", alias = "number")]
    pub number: u64,
    #[serde(default)]
    pub da_root: Option<String>, // 0x-prefixed (NMT root)
    #[serde(default)]
    pub state_root: Option<String>,
    #[serde(default)]
    pub receipts_root: Option<String>,
    #[serde(default)]
    pub chain_id: Option<u64>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

/// A single Merkle sample proof for availability.
/// Branch is bottom-up; each node is a 32-byte 0x-hex digest.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SampleProof {
    pub index: u64,
    pub leaf_hash: String,       // 0x-hex, 32 bytes
    pub branch: Vec<String>,     // 0x-hex digests, deepest-first
}

/// Availability/light proof bundle (subset that light clients need).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AvailabilityProof {
    pub samples: Vec<SampleProof>,
    #[serde(default)]
    pub leaves_count: Option<u64>,
    #[serde(default)]
    pub algo: Option<String>, // e.g., "sha3-256"
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

/// Summary of performed checks.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct VerifySummary {
    pub headers_checked: usize,
    pub da_samples_checked: usize,
}

/// Public API: verify a contiguous header slice (oldest → newest).
/// If `anchor_hash` is provided, require headers[0].parent_hash == anchor_hash.
/// Returns a summary of performed checks.
pub fn verify_headers(headers: &[LightHeader], anchor_hash: Option<&str>) -> Result<VerifySummary, String> {
    if headers.is_empty() {
        return Err("verify_headers: empty slice".into());
    }
    // Verify anchor linkage if provided.
    if let Some(anchor) = anchor_hash {
        if !eq_hex_case_insensitive(&headers[0].parent_hash, anchor) {
            return Err(format!(
                "anchor mismatch: {} (header[0].parentHash) != {} (anchor)",
                headers[0].parent_hash, anchor
            ));
        }
    }
    // Verify continuity and monotonic height.
    for i in 1..headers.len() {
        let prev = &headers[i - 1];
        let cur = &headers[i];
        if !eq_hex_case_insensitive(&cur.parent_hash, &prev.hash) {
            return Err(format!(
                "parent linkage break at index {}: parentHash={} != prev.hash={}",
                i, cur.parent_hash, prev.hash
            ));
        }
        if cur.number != prev.number + 1 {
            return Err(format!(
                "height not monotonic at index {}: {} then {}",
                i, prev.number, cur.number
            ));
        }
    }
    Ok(VerifySummary {
        headers_checked: headers.len(),
        da_samples_checked: 0,
    })
}

/// Verify a DA availability proof against a 32-byte `da_root` (0x-hex).
/// Returns the number of samples checked on success.
pub fn verify_da_proof(da_root: &str, proof: &AvailabilityProof) -> Result<usize, String> {
    let root = hex32(da_root)?;
    if let Some(algo) = &proof.algo {
        let a = algo.to_ascii_lowercase();
        if a != "sha3-256" && a != "sha3_256" {
            return Err(format!("unsupported algo: {}", algo));
        }
    }
    if proof.samples.is_empty() {
        return Err("availability proof has zero samples".into());
    }
    for (idx, sp) in proof.samples.iter().enumerate() {
        // Accept either full leaf hash or a leaf preimage already hashed by producer.
        let mut cur = hex32(&sp.leaf_hash)?;
        let mut index = sp.index as usize;

        for sib_hex in &sp.branch {
            let sib = hex32(sib_hex)?;
            let (left, right) = if (index & 1) == 0 {
                (cur, sib)
            } else {
                (sib, cur)
            };
            cur = merkle_parent_sha3_256(&left, &right);
            index >>= 1;
        }
        if cur != root {
            return Err(format!(
                "sample {} failed: computed root {} != expected {}",
                idx,
                hex::encode_prefixed(cur),
                hex::encode_prefixed(root)
            ));
        }
    }
    Ok(proof.samples.len())
}

/// Convenience: verify headers + (optional) DA proof against the **last** header's daRoot.
pub fn verify_headers_and_da(
    headers: &[LightHeader],
    anchor_hash: Option<&str>,
    da_proof: Option<&AvailabilityProof>,
) -> Result<VerifySummary, String> {
    let mut sum = verify_headers(headers, anchor_hash)?;
    if let Some(p) = da_proof {
        let last = headers.last().ok_or("internal: empty headers")?;
        let da_root = last
            .da_root
            .as_ref()
            .ok_or("last header has no daRoot to verify against")?;
        let n = verify_da_proof(da_root, p)?;
        sum.da_samples_checked = n;
    }
    Ok(sum)
}

// --------------------------- Internal helpers -------------------------------

fn eq_hex_case_insensitive(a: &str, b: &str) -> bool {
    trim_0x(a).eq_ignore_ascii_case(trim_0x(b))
}

fn trim_0x(s: &str) -> &str {
    s.strip_prefix("0x").unwrap_or(s)
}

fn hex32(s: &str) -> Result<[u8; 32], String> {
    let s = trim_0x(s);
    let bytes = <Vec<u8>>::from_hex(s).map_err(|e| format!("hex decode: {e}"))?;
    if bytes.len() != 32 {
        return Err(format!("expected 32 bytes, got {}", bytes.len()));
    }
    let mut out = [0u8; 32];
    out.copy_from_slice(&bytes);
    Ok(out)
}

fn merkle_parent_sha3_256(left: &[u8; 32], right: &[u8; 32]) -> [u8; 32] {
    let mut hasher = Sha3_256::new();
    hasher.update(left);
    hasher.update(right);
    let out = hasher.finalize();
    let mut arr = [0u8; 32];
    arr.copy_from_slice(&out);
    arr
}

// ------------------------------- Tests --------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn header_linkage_ok() {
        let h1 = LightHeader {
            hash: "0x11".into(),
            parent_hash: "0x00".into(),
            number: 1,
            da_root: None,
            state_root: None,
            receipts_root: None,
            chain_id: Some(1),
            extra: BTreeMap::new(),
        };
        let h2 = LightHeader {
            hash: "0x22".into(),
            parent_hash: "0x11".into(),
            number: 2,
            da_root: None,
            state_root: None,
            receipts_root: None,
            chain_id: Some(1),
            extra: BTreeMap::new(),
        };
        let s = verify_headers(&[h1, h2], Some("0x00")).unwrap();
        assert_eq!(s.headers_checked, 2);
        assert_eq!(s.da_samples_checked, 0);
    }

    #[test]
    fn header_linkage_breaks() {
        let h1 = LightHeader {
            hash: "0x11".into(),
            parent_hash: "0x00".into(),
            number: 1,
            da_root: None,
            state_root: None,
            receipts_root: None,
            chain_id: None,
            extra: BTreeMap::new(),
        };
        let h2 = LightHeader {
            hash: "0x22".into(),
            parent_hash: "0x99".into(), // wrong
            number: 2,
            da_root: None,
            state_root: None,
            receipts_root: None,
            chain_id: None,
            extra: BTreeMap::new(),
        };
        let err = verify_headers(&[h1, h2], None).unwrap_err();
        assert!(err.contains("parent linkage break"));
    }

    #[test]
    fn merkle_sample_verifies() {
        // Build a tiny Merkle tree with two leaves: L0, L1 (already 32-byte digests for simplicity).
        let l0 = Sha3_256::digest(b"leaf0");
        let l1 = Sha3_256::digest(b"leaf1");
        let mut l0_arr = [0u8; 32];
        let mut l1_arr = [0u8; 32];
        l0_arr.copy_from_slice(&l0);
        l1_arr.copy_from_slice(&l1);
        let root = merkle_parent_sha3_256(&l0_arr, &l1_arr);
        let root_hex = hex::encode_prefixed(root);

        // Proof for index 0: branch contains sibling (l1), index=0 so current is left.
        let proof = AvailabilityProof {
            samples: vec![SampleProof {
                index: 0,
                leaf_hash: hex::encode_prefixed(l0_arr),
                branch: vec![hex::encode_prefixed(l1_arr)],
            }],
            leaves_count: Some(2),
            algo: Some("sha3-256".into()),
            extra: BTreeMap::new(),
        };

        let checked = verify_da_proof(&root_hex, &proof).unwrap();
        assert_eq!(checked, 1);
    }

    #[test]
    fn merkle_sample_fails_on_wrong_root() {
        let bogus_root = "0x".to_string() + &"00".repeat(32);
        let sample = SampleProof {
            index: 0,
            leaf_hash: "0x".to_string() + &"00".repeat(32),
            branch: vec!["0x".to_string() + &"00".repeat(32)],
        };
        let proof = AvailabilityProof {
            samples: vec![sample],
            leaves_count: None,
            algo: None,
            extra: BTreeMap::new(),
        };
        assert!(verify_da_proof(&bogus_root, &proof).is_err());
    }

    #[test]
    fn json_roundtrip_structs() {
        let j = json!({
            "hash":"0x01",
            "parentHash":"0x00",
            "number": 7,
            "daRoot": "0x" + &"11".repeat(32),
            "unknownField": 42
        });
        let h: LightHeader = serde_json::from_value(j).unwrap();
        assert_eq!(h.number, 7);
        assert!(h.extra.contains_key("unknownField"));
    }
}
