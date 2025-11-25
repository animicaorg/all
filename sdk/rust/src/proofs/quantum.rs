//! QuantumProof assembly helpers (developer-facing).
//!
//! This module helps SDK users assemble a **Quantum proof reference**
//! from the quantum circuit description, provider identity/attestation,
//! trap-circuit outcomes, and optional QoS metrics. It mirrors the
//! shape expected by node tooling that serializes canonical envelopes
//! in the `proofs/` module, but keeps things lightweight for clients.
//
// Design notes
// - We bind the circuit via `circuit_digest = sha3_256(circuit_bytes)`.
// - Optionally bind an output digest if your flow stores the raw outputs.
// - We expose a small "bench" estimator to compute `quantum_units` from
//   (depth × width × shots); networks may tune coefficients on-chain.
// - We validate obvious bounds (ratios in [0,1], shots > 0, hex lengths).
//
// This is **not** a consensus implementation. It produces a reference
// object that higher-level tooling can convert into the canonical CBOR
// envelope expected by validators.

use crate::error::{Error, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value as JsonValue;
use sha3::{Digest, Sha3_256};
use std::collections::BTreeMap;

/// Provider identity/attestation bundle (opaque to the SDK).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct QuantumProviderCert {
    /// Human/registry identifier, e.g. "qpu:acme-iontrap-1" or "sim:oqc".
    pub provider: String,
    /// Evidence object (X.509 chain, EdDSA/PQ hybrid, statements, etc.).
    pub evidence: JsonValue,
    /// Future-proof fields.
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

/// Trap-circuit outcomes (for correctness confidence).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct QuantumTraps {
    pub passed: u32,
    pub total: u32,
    /// Optional ratio; computed when absent on `validate()`.
    #[serde(default)]
    pub ratio: Option<f64>,
    /// Optional Wilson/Clopper-Pearson lower bound in [0,1].
    #[serde(default)]
    pub confidence: Option<f64>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

impl QuantumTraps {
    pub fn compute_ratio(&self) -> f64 {
        if self.total == 0 {
            0.0
        } else {
            (self.passed as f64) / (self.total as f64)
        }
    }
}

/// QoS metrics for the quantum provider/job.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct QpuQos {
    #[serde(default)]
    pub latency_ms: Option<u64>,
    #[serde(default)]
    pub availability: Option<f64>,
    #[serde(default)]
    pub success: Option<bool>,
    #[serde(default)]
    pub score: Option<f64>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

/// Optional complexity meta for pricing/visualization.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct CircuitComplexity {
    #[serde(default)]
    pub depth: Option<u32>,
    #[serde(default)]
    pub width: Option<u32>,
    #[serde(default)]
    pub shots: Option<u32>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

/// A reference object your app can pass to proof builders on the node.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct QuantumProofRef {
    /// SHA3-256 digest of the circuit description (0x-hex, 32 bytes).
    pub circuit_digest: String,
    /// Number of shots requested/executed for this run.
    pub shots: u32,
    /// Optional SHA3-256 digest of the raw outputs (0x-hex, 32 bytes).
    #[serde(default)]
    pub output_digest: Option<String>,
    /// Provider certificate/attestation.
    pub provider_cert: QuantumProviderCert,
    /// Optional trap-circuit outcomes.
    #[serde(default)]
    pub traps: Option<QuantumTraps>,
    /// Optional QoS metrics.
    #[serde(default)]
    pub qos: Option<QpuQos>,
    /// Optional complexity meta (depth/width/shots).
    #[serde(default)]
    pub complexity: Option<CircuitComplexity>,
    /// Optional computed pricing/work units (network-specific).
    #[serde(default)]
    pub quantum_units: Option<u64>,
    /// Free-form application metadata.
    #[serde(default)]
    pub meta: BTreeMap<String, JsonValue>,
    /// Future-proof fields.
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

impl QuantumProofRef {
    /// Construct from circuit bytes and provider certificate.
    pub fn from_circuit(
        circuit_bytes: &[u8],
        shots: u32,
        provider_cert: QuantumProviderCert,
        traps: Option<QuantumTraps>,
        qos: Option<QpuQos>,
        complexity: Option<CircuitComplexity>,
    ) -> Self {
        let mut hasher = Sha3_256::new();
        hasher.update(circuit_bytes);
        let digest = hasher.finalize();

        Self {
            circuit_digest: hex0x(&digest),
            shots,
            output_digest: None,
            provider_cert,
            traps,
            qos,
            complexity,
            quantum_units: None,
            meta: BTreeMap::new(),
            extra: BTreeMap::new(),
        }
    }

    /// Bind an output digest (e.g., result payload), if available.
    pub fn with_output_digest(mut self, output_bytes: &[u8]) -> Self {
        let mut h = Sha3_256::new();
        h.update(output_bytes);
        let d = h.finalize();
        self.output_digest = Some(hex0x(&d));
        self
    }

    /// Attach a computed `quantum_units` value.
    pub fn with_units(mut self, units: u64) -> Self {
        self.quantum_units = Some(units);
        self
    }

    /// Validate invariants and fill derived fields where applicable.
    pub fn validate(&mut self) -> Result<()> {
        if self.shots == 0 {
            return Err(Error::InvalidData("shots must be > 0".into()));
        }
        if !is_hex32(&self.circuit_digest) {
            return Err(Error::InvalidData("circuitDigest must be 0x + 64 hex chars".into()));
        }
        if let Some(od) = self.output_digest.as_ref() {
            if !is_hex32(od) {
                return Err(Error::InvalidData("outputDigest must be 0x + 64 hex chars".into()));
            }
        }
        if let Some(t) = self.traps.as_mut() {
            let ratio = t.ratio.unwrap_or_else(|| t.compute_ratio());
            if !(0.0..=1.0).contains(&ratio) {
                return Err(Error::InvalidData(format!("trap ratio out of range: {ratio}")));
            }
            if let Some(c) = t.confidence {
                if !(0.0..=1.0).contains(&c) {
                    return Err(Error::InvalidData(format!("confidence out of range: {c}")));
                }
            }
            t.ratio = Some(ratio);
        }
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
        if let Some(c) = self.complexity.as_ref() {
            if let Some(w) = c.width {
                if w == 0 {
                    return Err(Error::InvalidData("width must be > 0".into()));
                }
            }
            if let Some(d) = c.depth {
                if d == 0 {
                    return Err(Error::InvalidData("depth must be > 0".into()));
                }
            }
            if let Some(s) = c.shots {
                if s == 0 {
                    return Err(Error::InvalidData("complexity.shots must be > 0".into()));
                }
            }
        }
        Ok(())
    }
}

/// Coefficients for unit estimation from (depth, width, shots).
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BenchCoeffs {
    /// Additive base per shot.
    pub base_per_shot: f64,
    /// Linear depth contribution per shot.
    pub depth_per_shot: f64,
    /// Linear width contribution per shot.
    pub width_per_shot: f64,
    /// Optional multiplicative scale (e.g., to convert to "units").
    pub scale: f64,
}

impl Default for BenchCoeffs {
    fn default() -> Self {
        Self {
            base_per_shot: 1.0,
            depth_per_shot: 0.25,
            width_per_shot: 0.50,
            scale: 1.0,
        }
    }
}

/// Estimate quantum work units.
/// This is a simple linear model intended for UI/pricing hints.
///
/// units = scale * shots * (base + depth*α + width*β)
pub fn estimate_quantum_units(depth: u32, width: u32, shots: u32, coeffs: BenchCoeffs) -> u64 {
    let d = depth as f64;
    let w = width as f64;
    let s = shots as f64;
    let per_shot = coeffs.base_per_shot + d * coeffs.depth_per_shot + w * coeffs.width_per_shot;
    let units = coeffs.scale * s * per_shot;
    if units.is_finite() && units >= 0.0 {
        units.round() as u64
    } else {
        0
    }
}

// ------------------------------ Utilities -----------------------------------

fn hex0x(bytes: impl AsRef<[u8]>) -> String {
    format!("0x{}", hex::encode(bytes))
}

fn is_hex32(s: &str) -> bool {
    s.starts_with("0x") && s.len() == 66 && s[2..].bytes().all(|b| matches!(b, b'0'..=b'9'|b'a'..=b'f'|b'A'..=b'F'))
}

// ---------------------------------- Tests -----------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn cert() -> QuantumProviderCert {
        QuantumProviderCert {
            provider: "qpu:acme-iontrap-1".into(),
            evidence: json!({
                "certChain": ["-----BEGIN CERT-----..."],
                "pubkey": "ed25519:abcd..."
            }),
            extra: BTreeMap::new(),
        }
    }

    #[test]
    fn build_validate_and_units() {
        let circ = br#"{"qasm":"OPENQASM 3; qubit[4] q; h q[0]; cx q[0], q[1];"}"#;
        let complexity = CircuitComplexity {
            depth: Some(32),
            width: Some(4),
            shots: Some(128),
            extra: BTreeMap::new(),
        };
        let mut pref = QuantumProofRef::from_circuit(
            circ,
            128,
            cert(),
            Some(QuantumTraps {
                passed: 95,
                total: 100,
                ratio: None,
                confidence: Some(0.95),
                extra: BTreeMap::new(),
            }),
            Some(QpuQos {
                latency_ms: Some(2500),
                availability: Some(0.999),
                success: Some(true),
                score: Some(0.97),
                extra: BTreeMap::new(),
            }),
            Some(complexity),
        );
        let units = estimate_quantum_units(32, 4, 128, BenchCoeffs::default());
        pref = pref.with_units(units);
        pref.validate().unwrap();
        assert!(pref.circuit_digest.starts_with("0x"));
        assert_eq!(pref.shots, 128);
        assert_eq!(pref.quantum_units, Some(units));
        assert!(pref.traps.as_ref().unwrap().ratio.unwrap() > 0.0);
    }

    #[test]
    fn output_digest_attachment() {
        let circ = b"circuit";
        let output = b"measurement-bits";
        let mut pref = QuantumProofRef::from_circuit(circ, 10, cert(), None, None, None)
            .with_output_digest(output);
        pref.validate().unwrap();
        assert!(pref.output_digest.as_ref().unwrap().starts_with("0x"));
    }

    #[test]
    fn bad_bounds_fail() {
        let circ = b"x";
        let mut pref = QuantumProofRef::from_circuit(
            circ,
            0, // invalid shots
            cert(),
            None,
            Some(QpuQos {
                availability: Some(1.5),
                ..Default::default()
            }),
            None,
        );
        assert!(pref.validate().is_err());
    }
}
