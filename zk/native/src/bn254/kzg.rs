// Copyright (c) Animica
// SPDX-License-Identifier: Apache-2.0
//
// bn254/kzg.rs — minimal KZG (KZG10) single-opening verification helpers
//
// This module provides a thin, dependency-light verifier that matches the
// simple pairing check used by KZG10 over BN254:
//
//   Check: e(C - y·G1, H) == e(π,  H^τ - z·H)
//
// where:
//   - C ∈ G1 is the commitment
//   - π ∈ G1 is the opening proof at point z
//   - y ∈ Fr is the claimed evaluation at z
//   - H ∈ G2 is the SRS generator in G2
//   - H^τ ∈ G2 is the "tau" power in G2
//
// All points and scalars are encoded/decoded using ark-serialize canonical
// **uncompressed** formats.
//
// Note: Degree bounds and polynomial soundness are out-of-scope in this tiny
// verifier; callers must ensure the SRS and commitments correspond to the same
// setup. This is intended as a fast-path inner check.
//
// Build: compiled only with Cargo feature `kzg`.

#![allow(clippy::needless_borrow)]

#[cfg(feature = "kzg")]
use ark_bn254::{Bn254, G1Affine, G1Projective, G2Affine, G2Projective, Fr};
#[cfg(feature = "kzg")]
use ark_ec::pairing::Pairing;
#[cfg(feature = "kzg")]
use ark_ec::CurveGroup;
#[cfg(feature = "kzg")]
use ark_serialize::{CanonicalDeserialize, CanonicalSerialize};
#[cfg(feature = "kzg")]
use ark_ff::PrimeField;

#[cfg(feature = "kzg")]
use crate::NativeError;

#[cfg(feature = "kzg")]
#[inline]
fn deser_g1_uncompressed(bytes: &[u8]) -> Result<G1Affine, NativeError> {
    G1Affine::deserialize_uncompressed(bytes)
        .map_err(|e| NativeError::Deserialize(format!("G1Affine: {e}")))
}

#[cfg(feature = "kzg")]
#[inline]
fn deser_g2_uncompressed(bytes: &[u8]) -> Result<G2Affine, NativeError> {
    G2Affine::deserialize_uncompressed(bytes)
        .map_err(|e| NativeError::Deserialize(format!("G2Affine: {e}")))
}

#[cfg(feature = "kzg")]
#[inline]
fn deser_fr_uncompressed(bytes: &[u8]) -> Result<Fr, NativeError> {
    Fr::deserialize_uncompressed(bytes)
        .map_err(|e| NativeError::Deserialize(format!("Fr: {e}")))
}

/// Minimal single-opening verification using canonical uncompressed encodings.
///
/// * `commit_g1` — G1 commitment bytes
/// * `proof_g1`  — G1 proof bytes
/// * `z_fr`      — opening point (Fr) bytes
/// * `y_fr`      — evaluation at z (Fr) bytes
/// * `g2_gen`    — SRS G2 generator (H) bytes
/// * `g2_tau`    — SRS tau power in G2 (H^τ) bytes
///
/// Returns `Ok(true)` if the pairing equation holds.
#[cfg(feature = "kzg")]
pub fn verify_single_opening_uncompressed(
    commit_g1: impl AsRef<[u8]>,
    proof_g1: impl AsRef<[u8]>,
    z_fr: impl AsRef<[u8]>,
    y_fr: impl AsRef<[u8]>,
    g2_gen: impl AsRef<[u8]>,
    g2_tau: impl AsRef<[u8]>,
    forbid_infinity: bool,
) -> Result<bool, NativeError> {
    // Deserialize inputs.
    let c = deser_g1_uncompressed(commit_g1.as_ref())?;
    let pi = deser_g1_uncompressed(proof_g1.as_ref())?;
    let z = deser_fr_uncompressed(z_fr.as_ref())?;
    let y = deser_fr_uncompressed(y_fr.as_ref())?;
    let h = deser_g2_uncompressed(g2_gen.as_ref())?;
    let h_tau = deser_g2_uncompressed(g2_tau.as_ref())?;

    if forbid_infinity {
        if c.infinity || pi.infinity || h.infinity || h_tau.infinity {
            return Err(NativeError::InvalidInput("points at infinity not allowed".into()));
        }
    }

    // Compute: C - y·G1
    let g1_gen = G1Affine::generator();
    let y_g1: G1Projective = g1_gen.mul_bigint(y.into_bigint());
    let c_minus_y = (c.into_group() - y_g1).into_affine();

    // Compute: H^τ - z·H
    let z_h: G2Projective = h.mul_bigint(z.into_bigint());
    let h_tau_minus_z = (h_tau.into_group() - z_h).into_affine();

    // Pairing check
    let lhs = Bn254::pairing(c_minus_y, h);
    let rhs = Bn254::pairing(pi, h_tau_minus_z);
    Ok(lhs == rhs)
}

#[cfg(all(test, feature = "kzg"))]
mod tests {
    use super::*;
    use ark_bn254::Bn254;
    use ark_poly::{univariate::DensePolynomial, UVPolynomial};
    use ark_poly_commit::kzg10::{Commitment, KZG10};
    use rand::thread_rng;

    fn ser_g1(p: &G1Affine) -> Vec<u8> {
        let mut v = Vec::new();
        p.serialize_uncompressed(&mut v).unwrap();
        v
    }
    fn ser_g2(p: &G2Affine) -> Vec<u8> {
        let mut v = Vec::new();
        p.serialize_uncompressed(&mut v).unwrap();
        v
    }
    fn ser_fr(x: &Fr) -> Vec<u8> {
        let mut v = Vec::new();
        x.serialize_uncompressed(&mut v).unwrap();
        v
    }

    #[test]
    fn kzg_roundtrip_matches_pairing_check() {
        // Build a small SRS and polynomial; generate a real proof using arkworks,
        // then verify with our minimal pairing equation.
        let mut rng = thread_rng();
        let max_degree = 32;
        let pp = KZG10::<Bn254>::setup(max_degree, &mut rng).unwrap();
        let (ck, vk) = KZG10::<Bn254>::trim(&pp, max_degree).unwrap();

        // p(X) = 3 + 2X + 5X^3
        let poly = DensePolynomial::from_coefficients_slice(&[
            Fr::from(3u64),
            Fr::from(2u64),
            Fr::from(0u64),
            Fr::from(5u64),
        ]);

        // Commit
        let (comm, _rand) = KZG10::<Bn254>::commit(&ck, &poly, None, None).unwrap();

        // Choose a random z and compute evaluation
        let z = Fr::from(7u64);
        let y = poly.evaluate(&z);

        // Open and check via arkworks (ground truth)
        let proof = KZG10::<Bn254>::open(&ck, &poly, z, None, None).unwrap();
        let ok_ark = KZG10::<Bn254>::check(&vk, &comm, z, y, &proof).unwrap();
        assert!(ok_ark, "arkworks KZG10 check should pass");

        // Extract bytes to feed our verifier:
        let commit_bytes = ser_g1(&match comm { Commitment(p) => p });
        let proof_bytes = ser_g1(&proof.0);
        let z_bytes = ser_fr(&z);
        let y_bytes = ser_fr(&y);
        // Verifier key exposes H and H^τ in G2
        let h_bytes = ser_g2(&vk.h);
        let h_tau_bytes = ser_g2(&vk.beta_h);

        let ok = verify_single_opening_uncompressed(
            &commit_bytes,
            &proof_bytes,
            &z_bytes,
            &y_bytes,
            &h_bytes,
            &h_tau_bytes,
            true,
        )
        .unwrap();
        assert!(ok, "minimal pairing check should match arkworks result");

        // Negative test: tweak y
        let y_bad = Fr::from(123456u64);
        let y_bad_bytes = ser_fr(&y_bad);
        let ok_bad = verify_single_opening_uncompressed(
            &commit_bytes,
            &proof_bytes,
            &z_bytes,
            &y_bad_bytes,
            &h_bytes,
            &h_tau_bytes,
            true,
        )
        .unwrap();
        assert!(!ok_bad, "mismatched evaluation must fail");
    }
}
