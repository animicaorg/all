// Copyright (c) Animica
// SPDX-License-Identifier: Apache-2.0
//
// Criterion micro-benchmarks for native zk accelerators.
//
// Run examples:
//   cargo bench -p animica_zk_native --features pairing
//   cargo bench -p animica_zk_native --features kzg
//   cargo bench -p animica_zk_native --features "pairing kzg"
//
// Notes:
// * We precompute inputs once per benchmark to avoid measuring setup time.
// * Benchmarks focus on the hot verification paths.

use criterion::{black_box, criterion_group, criterion_main, Criterion};

#[cfg(feature = "pairing")]
use animica_zk_native::pairing_product_check_bytes;
#[cfg(feature = "kzg")]
use animica_zk_native::kzg_verify_opening_bytes;

#[cfg(feature = "pairing")]
use ark_bn254::{G1Affine, G2Affine};
#[cfg(feature = "pairing")]
use ark_serialize::CanonicalSerialize;

#[cfg(feature = "kzg")]
use ark_bn254::{Bn254, Fr};
#[cfg(feature = "kzg")]
use ark_poly::{univariate::DensePolynomial, UVPolynomial};
#[cfg(feature = "kzg")]
use ark_poly_commit::kzg10::{Commitment, KZG10};
#[cfg(feature = "kzg")]
use ark_serialize::CanonicalSerialize;

#[cfg(feature = "pairing")]
fn make_pairing_pairs_bytes() -> Vec<(Vec<u8>, Vec<u8>)> {
    // Build a cancelling product: e(P,Q) * e(-P,Q) == 1
    let p = (G1Affine::generator().into_group() * 42u64).into_affine();
    let neg_p = (-p.into_group()).into_affine();
    let q = G2Affine::generator();

    let mut p_b = Vec::new();
    let mut np_b = Vec::new();
    let mut q_b = Vec::new();
    p.serialize_uncompressed(&mut p_b).unwrap();
    neg_p.serialize_uncompressed(&mut np_b).unwrap();
    q.serialize_uncompressed(&mut q_b).unwrap();

    vec![(p_b.clone(), q_b.clone()), (np_b, q_b)]
}

#[cfg(feature = "kzg")]
fn make_kzg_bytes() -> (Vec<u8>, Vec<u8>, Vec<u8>, Vec<u8>, Vec<u8>, Vec<u8>) {
    // Create a small SRS, polynomial, and proof once. We benchmark only verify.
    let mut rng = rand::thread_rng();
    let max_degree = 32usize;
    let pp = KZG10::<Bn254>::setup(max_degree, &mut rng).unwrap();
    let (ck, vk) = KZG10::<Bn254>::trim(&pp, max_degree).unwrap();

    let poly = DensePolynomial::from_coefficients_slice(&[
        Fr::from(3u64),
        Fr::from(2u64),
        Fr::from(0u64),
        Fr::from(5u64),
    ]);

    // Commit and open at z
    let (comm, _r) = KZG10::<Bn254>::commit(&ck, &poly, None, None).unwrap();
    let z = Fr::from(7u64);
    let y = poly.evaluate(&z);
    let proof = KZG10::<Bn254>::open(&ck, &poly, z, None, None).unwrap();

    // Serialize to canonical uncompressed bytes
    let ser_g1 = |g: &ark_bn254::G1Affine| {
        let mut v = Vec::new();
        g.serialize_uncompressed(&mut v).unwrap();
        v
    };
    let ser_g2 = |g: &ark_bn254::G2Affine| {
        let mut v = Vec::new();
        g.serialize_uncompressed(&mut v).unwrap();
        v
    };
    let ser_fr = |x: &Fr| {
        let mut v = Vec::new();
        x.serialize_uncompressed(&mut v).unwrap();
        v
    };

    let c_b = ser_g1(&match comm { Commitment(p) => p });
    let pi_b = ser_g1(&proof.0);
    let z_b = ser_fr(&z);
    let y_b = ser_fr(&y);
    let h_b = ser_g2(&vk.h);
    let h_tau_b = ser_g2(&vk.beta_h);

    (c_b, pi_b, z_b, y_b, h_b, h_tau_b)
}

fn bench_pairing(c: &mut Criterion) {
    #[cfg(feature = "pairing")]
    {
        let pairs = make_pairing_pairs_bytes();
        c.bench_function("bn254_pairing_product_check(2 pairs)", |b| {
            b.iter(|| {
                let ok = pairing_product_check_bytes(black_box(&pairs)).unwrap();
                black_box(ok)
            })
        });
    }
    #[cfg(not(feature = "pairing"))]
    {
        let _ = c; // no-op
    }
}

fn bench_kzg(c: &mut Criterion) {
    #[cfg(feature = "kzg")]
    {
        let (c_b, pi_b, z_b, y_b, h_b, h_tau_b) = make_kzg_bytes();
        // Sanity: verify once (should be true)
        let ok_once = kzg_verify_opening_bytes(
            &c_b, &pi_b, &z_b, &y_b, &h_b, &h_tau_b,
        )
        .unwrap();
        assert!(ok_once, "pre-check failed");

        c.bench_function("kzg_bn254_verify_single_opening", |b| {
            b.iter(|| {
                let ok = kzg_verify_opening_bytes(
                    black_box(&c_b),
                    black_box(&pi_b),
                    black_box(&z_b),
                    black_box(&y_b),
                    black_box(&h_b),
                    black_box(&h_tau_b),
                )
                .unwrap();
                black_box(ok)
            })
        });
    }
    #[cfg(not(feature = "kzg"))]
    {
        let _ = c; // no-op
    }
}

criterion_group!(benches, bench_pairing, bench_kzg);
criterion_main!(benches);
