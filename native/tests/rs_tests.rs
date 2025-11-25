//! Reed–Solomon tests: encode/decode, loss patterns, parity checks.
//!
//! Coverage:
//! - Encode → parity check (syndrome == 0)
//! - Random erasures up to parity budget → reconstruct OK, data round-trips
//! - Exceeding parity budget → reconstruct fails (or verify remains false)
//! - Contiguous-loss patterns at and beyond parity
//! - Parity shard corruption detection
//! - Assorted sizes and (k, m) shard configurations
//!
//! Assumed public API (adjust if your local signatures differ):
//!   animica_native::rs::encode(data: &[u8], data_shards: usize, parity_shards: usize) -> Vec<Vec<u8>>
//!   animica_native::rs::reconstruct(shards: &mut [Option<Vec<u8>>]) -> Result<(), animica_native::error::NativeError>
//!   animica_native::rs::verify(shards: &[Vec<u8>]) -> bool
//!
//! Test helpers come from tests/common.rs (rng_from_env, random_bytes).

mod common;

use animica_native::rs;
use common::{random_bytes, rng_from_env};

fn into_optional(shards: Vec<Vec<u8>>) -> Vec<Option<Vec<u8>>> {
    shards.into_iter().map(Some).collect()
}

fn collect(shards: &[Option<Vec<u8>>]) -> Vec<Vec<u8>> {
    shards.iter().map(|s| s.as_ref().expect("shard missing").clone()).collect()
}

/// Join the first `k` data shards and truncate to original `data_len`.
fn join_data_prefix(shards: &[Vec<u8>], k: usize, data_len: usize) -> Vec<u8> {
    let mut out = Vec::with_capacity(k * shards[0].len());
    for i in 0..k {
        out.extend_from_slice(&shards[i]);
    }
    out.truncate(data_len);
    out
}

fn erase_indices<T>(v: &mut [Option<T>], idxs: &[usize]) {
    for &i in idxs {
        v[i] = None;
    }
}

fn all_equal_size(shards: &[Vec<u8>]) -> bool {
    if shards.is_empty() { return true; }
    let n = shards[0].len();
    shards.iter().all(|s| s.len() == n)
}

#[test]
fn encode_then_verify_various_sizes() {
    let cases = [
        (0usize, 4usize, 2usize),
        (1, 4, 2),
        (37, 5, 3),
        (1024, 10, 4),
        (65_537, 12, 6),
    ];

    for &(len, k, m) in &cases {
        let data = vec![0u8; len];
        let shards = rs::encode(&data, k, m);
        assert_eq!(shards.len(), k + m, "wrong shard count for k={}, m={}", k, m);
        assert!(all_equal_size(&shards), "shards must be equal sized");
        assert!(rs::verify(&shards), "freshly-encoded shards should verify (k={}, m={})", k, m);

        // No-erasures reconstruct is a no-op but should succeed.
        let mut opt = into_optional(shards.clone());
        rs::reconstruct(&mut opt).expect("reconstruct should succeed with no erasures");
        let collected = collect(&opt);
        assert!(rs::verify(&collected), "verify should still pass after reconstruct");
    }
}

#[test]
fn random_erasures_up_to_parity_roundtrip() {
    let mut rng = rng_from_env();
    let len = 12_345usize;
    let k = 10usize;
    let m = 4usize;

    let data = random_bytes(len, &mut rng);
    let shards0 = rs::encode(&data, k, m);
    assert!(rs::verify(&shards0));

    for loss in 0..=m {
        // Run multiple random patterns per loss count
        for _ in 0..8 {
            let mut opt = into_optional(shards0.clone());

            // Randomly erase `loss` distinct indices
            let total = opt.len();
            let mut taken = std::collections::BTreeSet::new();
            while taken.len() < loss {
                taken.insert((rng.next_u64() as usize) % total);
            }
            let idxs: Vec<_> = taken.into_iter().collect();
            erase_indices(&mut opt, &idxs);

            // Attempt reconstruction
            rs::reconstruct(&mut opt).expect("reconstruct should succeed within parity budget");
            let full = collect(&opt);
            assert!(rs::verify(&full), "verify must pass after reconstruction");

            // Re-assemble original data from first k shards and compare prefix
            let round = join_data_prefix(&full, k, len);
            assert_eq!(round, data, "data mismatch after loss={} reconstruction", loss);
        }
    }
}

#[test]
fn contiguous_loss_patterns() {
    let len = 50_000usize;
    let k = 8usize;
    let m = 6usize; // robust to half the total shards lost
    let data = (0..len).map(|i| (i as u8).wrapping_mul(7)).collect::<Vec<_>>();

    let shards = rs::encode(&data, k, m);
    assert!(rs::verify(&shards));

    // Contiguous run of exactly m losses → reconstruct ok
    {
        let mut opt = into_optional(shards.clone());
        let start = 2usize; // not aligned to boundaries on purpose
        let loss = m;
        let idxs: Vec<_> = (start..start + loss).collect();
        erase_indices(&mut opt, &idxs);
        rs::reconstruct(&mut opt).expect("reconstruct should succeed at parity limit (contiguous)");
        let full = collect(&opt);
        assert!(rs::verify(&full));
        let round = join_data_prefix(&full, k, len);
        assert_eq!(round, data);
    }

    // Contiguous run of m+1 losses → must fail (or at least fail verify)
    {
        let mut opt = into_optional(shards.clone());
        let start = 1usize;
        let loss = m + 1;
        let idxs: Vec<_> = (start..start + loss).collect();
        erase_indices(&mut opt, &idxs);

        let rec_ok = rs::reconstruct(&mut opt).is_ok();
        if rec_ok {
            // Even if reconstruct returns Ok (backend heuristics), verify must fail.
            let full = collect(&opt);
            assert!(!rs::verify(&full), "verify should fail when losses exceed parity");
        }
    }
}

#[test]
fn exceed_parity_random_should_fail() {
    let mut rng = rng_from_env();
    let len = 4096usize;
    let k = 6usize;
    let m = 3usize;
    let data = random_bytes(len, &mut rng);
    let shards = rs::encode(&data, k, m);
    assert!(rs::verify(&shards));

    // Remove m+1 random distinct shards
    let mut opt = into_optional(shards.clone());
    let total = opt.len();
    let mut taken = std::collections::BTreeSet::new();
    while taken.len() < (m + 1) {
        taken.insert((rng.next_u64() as usize) % total);
    }
    let idxs: Vec<_> = taken.into_iter().collect();
    erase_indices(&mut opt, &idxs);

    let rec = rs::reconstruct(&mut opt);
    if rec.is_ok() {
        let full = collect(&opt);
        assert!(
            !rs::verify(&full),
            "verify must fail if more than parity shards are erased"
        );
    }
}

#[test]
fn parity_corruption_detection() {
    let len = 10_000usize;
    let k = 12usize;
    let m = 4usize;
    let data = (0..len).map(|i| (i as u8 ^ 0x5a)).collect::<Vec<_>>();
    let mut shards = rs::encode(&data, k, m);
    assert!(rs::verify(&shards));

    // Flip a bit in one parity shard (choose the last shard which is parity)
    let parity_index = k; // first parity shard
    shards[parity_index][0] ^= 0x01;

    // Verify should fail with corrupted parity present
    assert!(
        !rs::verify(&shards),
        "verify should detect parity corruption when all shards are present"
    );

    // But if we treat that corrupted parity as 'erased' and reconstruct, we should recover
    let mut opt = shards.into_iter().map(Some).collect::<Vec<_>>();
    opt[parity_index] = None; // treat as missing
    rs::reconstruct(&mut opt).expect("reconstruct should recover a single missing parity shard");
    let full = collect(&opt);
    assert!(rs::verify(&full), "after reconstruct, verify should pass");

    let round = join_data_prefix(&full, k, len);
    assert_eq!(round, data, "data should round-trip after parity repair");
}

#[test]
fn uneven_data_size_padding_consistency() {
    // Data size that is not a multiple of shard size to exercise padding/truncation logic.
    let len = 7777usize;
    let k = 5usize;
    let m = 3usize;
    let data = (0..len).map(|i| (i as u8).wrapping_mul(3).wrapping_add(1)).collect::<Vec<_>>();
    let shards = rs::encode(&data, k, m);
    assert!(all_equal_size(&shards));
    assert!(rs::verify(&shards));

    // Round-trip without erasures
    let round = join_data_prefix(&shards, k, len);
    assert_eq!(round, data);

    // Now drop 3 shards (== m) at scattered indices and recover
    let mut opt = into_optional(shards.clone());
    erase_indices(&mut opt, &[0, k + 1, k + 2]); // both data and parity erased
    rs::reconstruct(&mut opt).expect("reconstruct should succeed at exact parity budget");
    let full = collect(&opt);
    assert!(rs::verify(&full));
    let round2 = join_data_prefix(&full, k, len);
    assert_eq!(round2, data);
}

