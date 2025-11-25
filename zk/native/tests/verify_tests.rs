// Copyright (c) Animica
// SPDX-License-Identifier: Apache-2.0
//
// Integration tests for animica_zk_native (Rust side).
//
// Run locally:
//   cargo test -p animica_zk_native --features pairing
//   cargo test -p animica_zk_native --features kzg
//   cargo test -p animica_zk_native --features "pairing kzg"
//
// These tests exercise the public Rust APIs exposed by the crate. Python
// bindings (feature = "python") are intentionally not required here.

#[cfg(feature = "pairing")]
mod pairing_tests {
    use animica_zk_native::{pairing_product_check_bytes, NativeError};
    use ark_bn254::{G1Affine, G2Affine};
    use ark_ec::CurveGroup;
    use ark_serialize::CanonicalSerialize;

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

    #[test]
    fn product_check_true_on_inverse_pair() {
        // e(P,Q) * e(-P,Q) == 1
        let p = (G1Affine::generator().into_group() * 42u64).into_affine();
        let q = G2Affine::generator();
        let neg_p = (-p.into_group()).into_affine();

        let pairs = vec![(ser_g1(&p), ser_g2(&q)), (ser_g1(&neg_p), ser_g2(&q))];
        let ok = pairing_product_check_bytes(&pairs).expect("pairing check should not error");
        assert!(ok, "pairing product should be identity");
    }

    #[test]
    fn product_check_rejects_empty_input() {
        let pairs: Vec<(Vec<u8>, Vec<u8>)> = vec![];
        let err = pairing_product_check_bytes(&pairs).unwrap_err();
        match err {
            NativeError::InvalidInput(msg) => assert!(msg.contains("at least one")),
            other => panic!("unexpected error: {other:?}"),
        }
    }
}

#[cfg(feature = "kzg")]
mod kzg_tests {
    use animica_zk_native::{kzg_verify_opening_bytes, NativeError};
    use ark_bn254::{Bn254, Fr, G1Affine, G2Affine};
    use ark_poly::{univariate::DensePolynomial, UVPolynomial};
    use ark_poly_commit::kzg10::{Commitment, KZG10};
    use ark_serialize::CanonicalSerialize;

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
    fn kzg_roundtrip_matches_arkworks() {
        // Build SRS and polynomial; use arkworks to open, then verify with our helper.
        let mut rng = rand::thread_rng();
        let max_degree = 32usize;
        let pp = KZG10::<Bn254>::setup(max_degree, &mut rng).unwrap();
        let (ck, vk) = KZG10::<Bn254>::trim(&pp, max_degree).unwrap();

        // p(X) = 3 + 2X + 5X^3
        let poly = DensePolynomial::from_coefficients_slice(&[
            Fr::from(3u64),
            Fr::from(2u64),
            Fr::from(0u64),
            Fr::from(5u64),
        ]);

        // Commit & open
        let (comm, _r) = KZG10::<Bn254>::commit(&ck, &poly, None, None).unwrap();
        let z = Fr::from(7u64);
        let y = poly.evaluate(&z);
        let proof = KZG10::<Bn254>::open(&ck, &poly, z, None, None).unwrap();

        // Serialize canonical uncompressed
        let commit_bytes = ser_g1(&match comm { Commitment(p) => p });
        let proof_bytes = ser_g1(&proof.0);
        let z_bytes = ser_fr(&z);
        let y_bytes = ser_fr(&y);
        let h_bytes = ser_g2(&vk.h);
        let h_tau_bytes = ser_g2(&vk.beta_h);

        // Should verify true
        let ok = kzg_verify_opening_bytes(
            &commit_bytes,
            &proof_bytes,
            &z_bytes,
            &y_bytes,
            &h_bytes,
            &h_tau_bytes,
        )
        .expect("verify should not error");
        assert!(ok, "valid KZG proof should verify");

        // Negative: wrong y
        let y_bad = Fr::from(123456u64);
        let ok_bad = kzg_verify_opening_bytes(
            &commit_bytes,
            &proof_bytes,
            &z_bytes,
            &ser_fr(&y_bad),
            &h_bytes,
            &h_tau_bytes,
        )
        .expect("verify should not error");
        assert!(!ok_bad, "mismatched evaluation must fail");
    }

    #[test]
    fn kzg_deserialize_error_on_garbage() {
        // Feed obviously invalid (too short) bytes to trigger a deserialize error.
        let bogus = vec![0u8; 8];
        let err = kzg_verify_opening_bytes(&bogus, &bogus, &bogus, &bogus, &bogus, &bogus)
            .unwrap_err();
        match err {
            NativeError::Deserialize(_) => { /* expected */ }
            other => panic!("expected deserialize error, got {other:?}"),
        }
    }
}
