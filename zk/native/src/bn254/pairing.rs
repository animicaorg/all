// Copyright (c) Animica
// SPDX-License-Identifier: Apache-2.0
//
// bn254/pairing.rs — low-level BN254 pairing helpers
//
// These routines back the higher-level APIs exposed from `lib.rs`. They are
// intentionally small, dependency-light wrappers over arkworks that perform:
//
//   * Canonical (ark-serialize) uncompressed deserialization for G1/G2/Fr
//   * Basic sanity checks (non-empty inputs; optional infinity checks)
//   * Multi-pair product pairing: ∏ e(P_i, Q_i) == 1
//
// All items are compiled only when the "pairing" Cargo feature is enabled.

#![allow(clippy::needless_borrow)]

#[cfg(feature = "pairing")]
use ark_bn254::{Bn254, G1Affine, G1Projective, G2Affine, Fr};
#[cfg(feature = "pairing")]
use ark_ec::pairing::Pairing;
#[cfg(feature = "pairing")]
use ark_ec::CurveGroup;
#[cfg(feature = "pairing")]
use ark_serialize::{CanonicalDeserialize, CanonicalSerialize};

#[cfg(feature = "pairing")]
use crate::NativeError;

#[cfg(feature = "pairing")]
#[inline]
pub(crate) fn deser_g1_uncompressed(bytes: &[u8]) -> Result<G1Affine, NativeError> {
    G1Affine::deserialize_uncompressed(bytes)
        .map_err(|e| NativeError::Deserialize(format!("G1Affine: {e}")))
}

#[cfg(feature = "pairing")]
#[inline]
pub(crate) fn deser_g2_uncompressed(bytes: &[u8]) -> Result<G2Affine, NativeError> {
    G2Affine::deserialize_uncompressed(bytes)
        .map_err(|e| NativeError::Deserialize(format!("G2Affine: {e}")))
}

#[cfg(feature = "pairing")]
#[inline]
pub(crate) fn deser_fr_uncompressed(bytes: &[u8]) -> Result<Fr, NativeError> {
    Fr::deserialize_uncompressed(bytes)
        .map_err(|e| NativeError::Deserialize(format!("Fr: {e}")))
}

#[cfg(feature = "pairing")]
#[inline]
pub(crate) fn ser_g1_uncompressed(p: &G1Affine) -> Result<Vec<u8>, NativeError> {
    let mut v = Vec::new();
    p.serialize_uncompressed(&mut v)
        .map_err(|e| NativeError::Internal(format!("G1 serialize: {e}")))?;
    Ok(v)
}

/// Return `true` iff the product pairing across provided pairs equals the GT
/// identity, i.e. `∏ e(P_i, Q_i) == 1`.
///
/// *Inputs*: a slice of `(g1_bytes, g2_bytes)` where each element is the
/// canonical **uncompressed** ark-serialize form of `G1Affine` / `G2Affine`.
///
/// *Safety*: this function does not reject the point-at-infinity explicitly.
/// Callers may choose to forbid it with `forbid_infinity=true` to add a cheap
/// guard against degenerate inputs.
#[cfg(feature = "pairing")]
pub fn product_pairing_check_uncompressed(
    pairs: &[(impl AsRef<[u8]>, impl AsRef<[u8]>)],
    forbid_infinity: bool,
) -> Result<bool, NativeError> {
    if pairs.is_empty() {
        return Err(NativeError::InvalidInput("at least one (G1,G2) pair required".into()));
    }

    let mut prepared: Vec<(G1Affine, G2Affine)> = Vec::with_capacity(pairs.len());
    for (g1b, g2b) in pairs {
        let p = deser_g1_uncompressed(g1b.as_ref())?;
        let q = deser_g2_uncompressed(g2b.as_ref())?;

        if forbid_infinity {
            if p.infinity {
                return Err(NativeError::InvalidInput("G1 point at infinity not allowed".into()));
            }
            if q.infinity {
                return Err(NativeError::InvalidInput("G2 point at infinity not allowed".into()));
            }
        }
        prepared.push((p, q));
    }

    let ml = Bn254::multi_miller_loop(prepared.iter().map(|(p, q)| (p, q)));
    let gt = ml.final_exponentiation();
    Ok(gt.is_one())
}

/// A tiny demo helper used by benches/tests: constructs a trivial equality
/// `e(P, Q) == e(P, Q)` and returns the pairing product check on
/// `[(P,Q), (-P,Q)]` which should be the identity.
///
/// This is intentionally deterministic and uses a fixed generator multiple.
#[cfg(feature = "pairing")]
pub fn pairing_identity_demo() -> bool {
    // P = [42]G1, Q = G2 (generator)
    let p = (G1Affine::generator().into_group() * 42u64).into_affine();
    let q = G2Affine::generator();

    // e(P, Q) * e(-P, Q) == 1
    let neg_p = (-p.into_group()).into_affine();
    let ml = Bn254::multi_miller_loop([(p, q), (neg_p, q)]);
    ml.final_exponentiation().is_one()
}

#[cfg(all(test, feature = "pairing"))]
mod tests {
    use super::*;
    use ark_bn254::{G1Affine, G2Affine};

    #[test]
    fn identity_demo_holds() {
        assert!(pairing_identity_demo());
    }

    #[test]
    fn product_check_true_on_trivial_inverse() {
        // Build a pair that cancels out: (P,Q) and (-P,Q)
        let p = (G1Affine::generator().into_group() * 7u64).into_affine();
        let q = G2Affine::generator();
        let neg_p = (-p.into_group()).into_affine();

        let p_bytes = ser_g1_uncompressed(&p).unwrap();
        let neg_p_bytes = ser_g1_uncompressed(&neg_p).unwrap();

        let mut q_bytes = Vec::new();
        use ark_serialize::CanonicalSerialize;
        q.serialize_uncompressed(&mut q_bytes).unwrap();

        let ok = product_pairing_check_uncompressed(
            &[(p_bytes.as_slice(), q_bytes.as_slice()), (neg_p_bytes.as_slice(), q_bytes.as_slice())],
            /*forbid_infinity=*/ true,
        )
        .unwrap();
        assert!(ok);
    }

    #[test]
    fn rejects_empty_input() {
        let err = product_pairing_check_uncompressed::<(&[u8], &[u8])>(&[], true).unwrap_err();
        assert!(matches!(err, crate::NativeError::InvalidInput(_)));
    }
}
