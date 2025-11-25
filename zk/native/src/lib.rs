// Copyright (c) Animica
// SPDX-License-Identifier: Apache-2.0
//
// animica_zk_native — optional native accelerators for zk verification.
// Features:
//   - "pairing": BN254 pairing helpers (Groth16-related)
//   - "kzg":     Minimal KZG single-opening verification (BN254), implies "pairing"
//   - "python":  Build Python extension module via pyo3 exposing fast paths
//
// This crate is designed to be *optional*. Pure-Python paths remain the default;
// when this library is present and importable, higher-level code can dispatch
// to these accelerators.
//
// Serialization contract for Python bindings:
//   * All curve points are provided as **ark-serialize canonical uncompressed**
//     bytes for the corresponding Affine types.
//   * Field elements (Fr) are provided as **ark-serialize canonical bytes**.
//     (These are *not* decimal strings or hex; callers should use arkworks-
//      compatible serialization from their host language.)
//
// See zk/docs/PERFORMANCE.md for integration notes.

#![allow(clippy::needless_borrow)]
#![deny(missing_docs)]

use thiserror::Error;

#[cfg(feature = "pairing")]
use ark_bn254::{Bn254, G1Affine, G1Projective, G2Affine, G2Projective, Fr};
#[cfg(feature = "pairing")]
use ark_ec::pairing::Pairing;
#[cfg(feature = "pairing")]
use ark_ec::CurveGroup;
#[cfg(feature = "pairing")]
use ark_ff::{BigInteger, PrimeField};
#[cfg(feature = "pairing")]
use ark_serialize::{CanonicalDeserialize, CanonicalSerialize};

/// Errors returned by native helpers.
#[derive(Debug, Error)]
pub enum NativeError {
    /// A required feature is not enabled in this build.
    #[error("feature not enabled: {0}")]
    FeatureDisabled(&'static str),

    /// Failed to deserialize an input using ark-serialize canonical formats.
    #[error("deserialize error: {0}")]
    Deserialize(String),

    /// Invalid input relationship (e.g., empty terms).
    #[error("invalid input: {0}")]
    InvalidInput(String),

    /// Internal error.
    #[error("internal: {0}")]
    Internal(String),
}

#[cfg(feature = "pairing")]
fn deser_g1(bytes: &[u8]) -> Result<G1Affine, NativeError> {
    G1Affine::deserialize_uncompressed(bytes)
        .map_err(|e| NativeError::Deserialize(format!("G1Affine: {e}")))
}

#[cfg(feature = "pairing")]
fn deser_g2(bytes: &[u8]) -> Result<G2Affine, NativeError> {
    G2Affine::deserialize_uncompressed(bytes)
        .map_err(|e| NativeError::Deserialize(format!("G2Affine: {e}")))
}

#[cfg(feature = "pairing")]
fn deser_fr(bytes: &[u8]) -> Result<Fr, NativeError> {
    Fr::deserialize_uncompressed(bytes)
        .map_err(|e| NativeError::Deserialize(format!("Fr: {e}")))
}

/// BN254 product pairing check:
/// Returns `true` iff ∏ e(P_i, Q_i) == 1 in GT.
/// Inputs are canonical uncompressed bytes for G1Affine / G2Affine pairs.
#[cfg(feature = "pairing")]
pub fn pairing_product_check_bytes(pairs: &[(Vec<u8>, Vec<u8>)]) -> Result<bool, NativeError> {
    if pairs.is_empty() {
        return Err(NativeError::InvalidInput("at least one pair required".into()));
    }
    let mut prepared = Vec::with_capacity(pairs.len());
    for (g1b, g2b) in pairs {
        let p = deser_g1(g1b)?;
        let q = deser_g2(g2b)?;
        prepared.push((p, q));
    }
    let ml = Bn254::multi_miller_loop(prepared.iter().map(|(p, q)| (p, q)));
    let gt = ml.final_exponentiation();
    Ok(gt.is_one())
}

/// Minimal KZG single-opening verification over BN254:
///
/// Check: e(C - y*G1, G2) == e(π, G2^{τ} - z*G2),
/// where:
///   - C: commitment (G1)
///   - π: proof (G1)
///   - y: evaluation at point z (Fr)
///   - z: opening point (Fr)
///   - G2, G2^{τ}: verifier SRS elements in G2
///
/// All group elements are canonical uncompressed bytes; field elements use
/// canonical ark-serialize encoding.
///
/// NOTE: This assumes the SRS uses the standard generators. Callers must ensure
/// the commitment/proof/SRS are consistent (same setup). This does **not**
/// check polynomial degree bounds; it is a minimal single-opening verifier.
#[cfg(feature = "kzg")]
pub fn kzg_verify_opening_bytes(
    commit_g1: &[u8],
    proof_g1: &[u8],
    z_fr: &[u8],
    y_fr: &[u8],
    g2_gen_bytes: &[u8],
    g2_tau_bytes: &[u8],
) -> Result<bool, NativeError> {
    let c = deser_g1(commit_g1)?;
    let pi = deser_g1(proof_g1)?;
    let z = deser_fr(z_fr)?;
    let y = deser_fr(y_fr)?;
    let g2_gen = deser_g2(g2_gen_bytes)?;
    let g2_tau = deser_g2(g2_tau_bytes)?;

    // C - y*G1 (using the canonical G1 generator as [1] in SRS)
    let g1_gen = G1Affine::generator();
    let y_g1: G1Projective = g1_gen.mul_bigint(y.into_bigint());
    let c_minus_y = (c.into_group() - y_g1).into_affine();

    // G2^{τ} - z * G2
    let z_g2: G2Projective = g2_gen.mul_bigint(z.into_bigint());
    let g2_tau_minus_z = (g2_tau.into_group() - z_g2).into_affine();

    // Compare pairings
    let lhs = Bn254::pairing(c_minus_y, g2_gen);
    let rhs = Bn254::pairing(pi, g2_tau_minus_z);
    Ok(lhs == rhs)
}

#[cfg(feature = "python")]
mod py {
    use super::*;
    use pyo3::exceptions::{PyRuntimeError, PyValueError};
    use pyo3::prelude::*;
    use pyo3::types::{PyBytes, PyDict, PyList};

    fn py_err<E: std::fmt::Display>(e: E) -> PyErr {
        PyRuntimeError::new_err(e.to_string())
    }

    /// Return build-time and environment metadata for diagnostics.
    #[pyfunction]
    fn version_info(py: Python<'_>) -> PyResult<PyObject> {
        let d = PyDict::new(py);
        d.set_item("crate", "animica_zk_native")?;
        d.set_item("git", option_env!("ANIMICA_ZK_NATIVE_GIT").unwrap_or("unknown"))?;
        d.set_item("target", option_env!("ANIMICA_ZK_NATIVE_TARGET").unwrap_or("unknown"))?;
        d.set_item("profile", option_env!("ANIMICA_ZK_NATIVE_PROFILE").unwrap_or("unknown"))?;
        d.set_item("features", PyDict::from_sequence(
            PyDict::from_iter([
                ("pairing", cfg!(feature = "pairing")),
                ("kzg", cfg!(feature = "kzg")),
                ("python", cfg!(feature = "python")),
                ("parallel", cfg!(build_has_parallel)),
            ]).unwrap().into_py(py).as_ref()
        ).ok())?;
        Ok(d.into())
    }

    /// True if this build was compiled with the given feature flags.
    #[pyfunction]
    fn available(py: Python<'_>) -> PyResult<PyObject> {
        let d = PyDict::new(py);
        d.set_item("pairing", cfg!(feature = "pairing"))?;
        d.set_item("kzg", cfg!(feature = "kzg"))?;
        d.set_item("python", cfg!(feature = "python"))?;
        d.set_item("parallel", cfg!(build_has_parallel))?;
        Ok(d.into())
    }

    /// Product pairing check on BN254.
    ///
    /// Args:
    ///   terms: list of (g1_bytes, g2_bytes), both canonical uncompressed.
    ///
    /// Returns:
    ///   bool — True iff ∏ e(P_i, Q_i) == 1.
    #[pyfunction]
    fn pairing_product_check_bytes_py(py: Python<'_>, terms: &PyAny) -> PyResult<bool> {
        if !cfg!(feature = "pairing") {
            return Err(PyValueError::new_err("feature 'pairing' not enabled"));
        }
        // Accept a Python list of 2-tuples[bytes, bytes]
        let seq = terms.downcast::<PyList>()?;
        let mut v = Vec::with_capacity(seq.len());
        for item in seq.iter() {
            let tup: (Vec<u8>, Vec<u8>) = item.extract()?;
            v.push(tup);
        }
        #[cfg(feature = "pairing")]
        {
            pairing_product_check_bytes(&v).map_err(py_err)
        }
        #[cfg(not(feature = "pairing"))]
        {
            Err(PyValueError::new_err("feature 'pairing' not enabled"))
        }
    }

    /// Minimal KZG single-opening check over BN254.
    ///
    /// Args:
    ///   commit_g1, proof_g1, z_fr, y_fr, g2_gen, g2_tau: bytes
    /// Returns:
    ///   bool — verification result.
    #[pyfunction]
    fn kzg_verify_opening_bytes_py(
        commit_g1: &PyAny,
        proof_g1: &PyAny,
        z_fr: &PyAny,
        y_fr: &PyAny,
        g2_gen: &PyAny,
        g2_tau: &PyAny,
    ) -> PyResult<bool> {
        if !cfg!(feature = "kzg") {
            return Err(PyValueError::new_err("feature 'kzg' not enabled"));
        }
        let c: Vec<u8> = commit_g1.extract()?;
        let p: Vec<u8> = proof_g1.extract()?;
        let z: Vec<u8> = z_fr.extract()?;
        let y: Vec<u8> = y_fr.extract()?;
        let g2: Vec<u8> = g2_gen.extract()?;
        let g2tau: Vec<u8> = g2_tau.extract()?;
        #[cfg(feature = "kzg")]
        {
            kzg_verify_opening_bytes(&c, &p, &z, &y, &g2, &g2tau).map_err(py_err)
        }
        #[cfg(not(feature = "kzg"))]
        {
            Err(PyValueError::new_err("feature 'kzg' not enabled"))
        }
    }

    /// Convenience helpers for callers to introspect ark-serialize sizes.
    #[pyfunction]
    fn sizes(py: Python<'_>) -> PyResult<PyObject> {
        #[cfg(feature = "pairing")]
        {
            use ark_serialize::CanonicalSerialize;
            let mut v = Vec::new();
            G1Affine::generator().serialize_uncompressed(&mut v).unwrap();
            let g1 = v.len();
            v.clear();
            G2Affine::generator().serialize_uncompressed(&mut v).unwrap();
            let g2 = v.len();
            v.clear();
            Fr::from(42u64).serialize_uncompressed(&mut v).unwrap();
            let fr = v.len();

            let d = PyDict::new(py);
            d.set_item("G1Affine_uncompressed", g1)?;
            d.set_item("G2Affine_uncompressed", g2)?;
            d.set_item("Fr_uncompressed", fr)?;
            return Ok(d.into());
        }
        #[cfg(not(feature = "pairing"))]
        {
            Err(PyValueError::new_err("feature 'pairing' not enabled"))
        }
    }

    /// Python module definition.
    #[pymodule]
    fn animica_zk_native(py: Python<'_>, m: &PyModule) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(version_info, m)?)?;
        m.add_function(wrap_pyfunction!(available, m)?)?;
        m.add_function(wrap_pyfunction!(pairing_product_check_bytes_py, m)?)?;
        m.add_function(wrap_pyfunction!(kzg_verify_opening_bytes_py, m)?)?;
        m.add_function(wrap_pyfunction!(sizes, m)?)?;
        // Provide a minimal banner for quick smoke tests
        m.add("BANNER", PyBytes::new(py, b"animica_zk_native: bn254 pairing/kzg accelerators"))?;
        Ok(())
    }
}

// ---- No-pyo3 fallback --------------------------------------------------------

#[cfg(not(feature = "python"))]
#[doc(hidden)]
pub mod no_python {
    //! When built without the `python` feature, this crate exposes only the
    //! Rust functions (if their features are enabled). Importing as a Python
    //! module is unavailable.
    //!
    //! Rust callers can use:
    //!   - `pairing_product_check_bytes` (feature = "pairing")
    //!   - `kzg_verify_opening_bytes` (feature = "kzg")

    // Intentionally empty – Rust APIs are available at crate root.
}

