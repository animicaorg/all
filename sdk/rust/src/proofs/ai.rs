//! AIProof assembly helpers (developer-facing).
//!
//! This module helps SDK users assemble an **AI proof reference** from:
//! - Model output bytes (we hash to a digest bound in the proof)
//! - TEE attestation bundle from the provider
//! - Optional trap-circuit receipts (pass/total → ratio)
//! - Optional QoS metrics (latency, availability, success)
//!
//! It does **not** implement the full on-chain CBOR envelope; rather it
//! produces a forward-compatible JSON-serializable struct your app can
//! pass to node tooling that builds canonical envelopes per `proofs/`.

use crate::error::{Error, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value as JsonValue;
use sha3::{Digest, Sha3_256};
use std::collections::BTreeMap;

/// TEE attestation bundle (opaque JSON; minimally tagged with `vendor`).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AiAttestation {
    /// Provider/vendor marker, e.g. "intel-sgx", "amd-sev-snp", "arm-cca".
    pub vendor: String,
    /// Evidence bundle (e.g., quote/report/token JSON), provider-specific.
    pub evidence: JsonValue,
    /// Future-proof fields.
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

/// Trap-circuit outcomes (used for correctness confidence).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AiTraps {
    pub passed: u32,
    pub total: u32,
    /// Optional precomputed ratio; if absent, computed on validate().
    #[serde(default)]
    pub ratio: Option<f64>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

impl AiTraps {
    pub fn compute_ratio(&self) -> f64 {
        if self.total == 0 {
            return 0.0;
        }
        (self.passed as f64) / (self.total as f64)
    }
}

/// QoS metrics observed for the job/provider.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct AiQos {
    /// Whole-request latency in milliseconds (provider reported or measured).
    #[serde(default)]
    pub latency_ms: Option<u64>,
    /// Recent availability estimate in [0.0, 1.0].
    #[serde(default)]
    pub availability: Option<f64>,
    /// Whether the request completed successfully.
    #[serde(default)]
    pub success: Option<bool>,
    /// Optional provider score in [0.0, 1.0] (SLA composite).
    #[serde(default)]
    pub score: Option<f64>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

/// AI proof reference (what apps pass into proof builders).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AiProofRef {
    /// SHA3-256 digest of the model output bytes (0x-hex, 32 bytes).
    pub output_digest: String,
    /// Size of the raw output in bytes (useful for pricing/quotas).
    pub output_size: u64,
    /// Optional MIME (e.g., "application/json", "text/plain", "image/png").
    #[serde(default)]
    pub mime: Option<String>,
    /// Redundant runs for the same prompt (>=1). Used for confidence boosting.
    #[serde(default)]
    pub redundancy: Option<u32>,
    /// Attestation bundle (TEE or equivalent).
    pub attestation: AiAttestation,
    /// Optional trap-circuit outcomes.
    #[serde(default)]
    pub traps: Option<AiTraps>,
    /// Optional QoS metrics.
    #[serde(default)]
    pub qos: Option<AiQos>,
    /// Free-form application metadata (kept out of consensus-critical fields).
    #[serde(default)]
    pub meta: BTreeMap<String, JsonValue>,
    /// Future-proof fields.
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

impl AiProofRef {
    /// Construct from raw model `output` and an `attestation` bundle.
    /// - Computes `output_digest = 0x + sha3_256(output)`
    /// - Sets `output_size`
    pub fn from_output(
        output: &[u8],
        attestation: AiAttestation,
        traps: Option<AiTraps>,
        qos: Option<AiQos>,
    ) -> Self {
        let mut hasher = Sha3_256::new();
        hasher.update(output);
        let digest = hasher.finalize();
        Self {
            output_digest: hex0x(&digest),
            output_size: output.len() as u64,
            mime: None,
            redundancy: None,
            attestation,
            traps,
            qos,
            meta: BTreeMap::new(),
            extra: BTreeMap::new(),
        }
    }

    /// Validate basic invariants; fills derived fields (e.g., traps.ratio).
    pub fn validate(&mut self) -> Result<()> {
        // Digest should be 0x + 64 hex chars.
        if !self.output_digest.starts_with("0x") || self.output_digest.len() != 66 {
            return Err(Error::InvalidData(format!(
                "outputDigest must be 0x + 64 hex chars, got len={}",
                self.output_digest.len()
            )));
        }
        if let Some(r) = self.redundancy {
            if r == 0 {
                return Err(Error::InvalidData("redundancy must be >= 1".into()));
            }
        }
        // Trap ratio in [0,1].
        if let Some(t) = self.traps.as_mut() {
            let ratio = t.ratio.unwrap_or_else(|| t.compute_ratio());
            if !(0.0..=1.0).contains(&ratio) {
                return Err(Error::InvalidData(format!(
                    "trap ratio out of range: {ratio}"
                )));
            }
            t.ratio = Some(ratio);
        }
        // QoS bounds.
        if let Some(q) = self.qos.as_ref() {
            if let Some(a) = q.availability {
                if !(0.0..=1.0).contains(&a) {
                    return Err(Error::InvalidData(format!("availability out of range: {a}")));
                }
            }
            if let Some(s) = q.score {
                if !(0.0..=1.0).contains(&s) {
                    return Err(Error::InvalidData(format!("score out of range: {s}")));
                }
            }
        }
        Ok(())
    }
}

/// Build a ready-to-serialize AI proof reference with optional MIME and redundancy.
pub fn build_ai_proof_ref(
    output: &[u8],
    mime: Option<&str>,
    redundancy: Option<u32>,
    attestation: AiAttestation,
    traps: Option<AiTraps>,
    qos: Option<AiQos>,
) -> Result<AiProofRef> {
    let mut pref = AiProofRef::from_output(output, attestation, traps, qos);
    if let Some(m) = mime {
        pref.mime = Some(m.to_string());
    }
    if let Some(r) = redundancy {
        pref.redundancy = Some(r);
    }
    pref.validate()?;
    Ok(pref)
}

// ------------------------------- Utilities ----------------------------------

fn hex0x(bytes: impl AsRef<[u8]>) -> String {
    format!("0x{}", hex::encode(bytes))
}

// ---------------------------------- Tests -----------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn sample_attestation() -> AiAttestation {
        AiAttestation {
            vendor: "intel-sgx".into(),
            evidence: json!({
                "quote": "0xdeadbeef",
                "pckCertChain": ["-----BEGIN CERT-----..."]
            }),
            extra: BTreeMap::new(),
        }
    }

    #[test]
    fn build_and_validate_prefills_digest_and_size() {
        let output = b"{\"text\":\"hello\"}";
        let att = sample_attestation();
        let mut pref = AiProofRef::from_output(output, att, None, None);
        assert_eq!(pref.output_size, output.len() as u64);
        assert!(pref.output_digest.starts_with("0x"));
        assert_eq!(pref.output_digest.len(), 66);
        pref.validate().unwrap();
    }

    #[test]
    fn traps_ratio_is_computed_when_missing() {
        let output = b"ok";
        let att = sample_attestation();
        let traps = AiTraps {
            passed: 9,
            total: 10,
            ratio: None,
            extra: BTreeMap::new(),
        };
        let mut pref = AiProofRef::from_output(output, att, Some(traps), None);
        pref.validate().unwrap();
        let r = pref.traps.as_ref().unwrap().ratio.unwrap();
        assert!((r - 0.9).abs() < 1e-9);
    }

    #[test]
    fn qos_bounds_are_checked() {
        let output = b"ok";
        let att = sample_attestation();
        // Bad availability
        let qos = AiQos {
            availability: Some(1.5),
            ..Default::default()
        };
        let mut pref = AiProofRef::from_output(output, att, None, Some(qos));
        let err = pref.validate().unwrap_err();
        assert!(matches!(err, Error::InvalidData(_)));
    }

    #[test]
    fn full_builder_with_mime_and_redundancy() {
        let output = b"result";
        let att = sample_attestation();
        let pref = build_ai_proof_ref(
            output,
            Some("application/json"),
            Some(2),
            att,
            None,
            Some(AiQos {
                latency_ms: Some(120),
                availability: Some(0.999),
                success: Some(true),
                score: Some(0.98),
                extra: BTreeMap::new(),
            }),
        )
        .unwrap();
        assert_eq!(pref.mime.as_deref(), Some("application/json"));
        assert_eq!(pref.redundancy, Some(2));
    }
}
