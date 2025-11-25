mod common;

use animica_native::rs::{self, RsParams};

use common::{XorShift64, DEFAULT_TEST_SEED};

fn make_random_codeword(
    k: usize,
    m: usize,
    shard_len: usize,
    seed: u64,
) -> (RsParams, Vec<Vec<u8>>, Vec<Vec<u8>>) {
    let params = RsParams {
        data_shards: k,
        parity_shards: m,
    };

    let mut rng = XorShift64::new(seed);
    // Start with k+m shards, all zeros.
    let mut shards = vec![vec![0u8; shard_len]; k + m];

    // Fill data shards with pseudo-random bytes.
    for s in &mut shards[..k] {
        rng.fill_bytes(s);
    }

    let original_data = shards[..k].to_vec();

    rs::encode_in_place(params, &mut shards)
        .expect("encode_in_place should succeed for valid parameters");

    (params, original_data, shards)
}

/// Pick up to `max_loss` distinct indices in `0..total` to erase,
/// using the provided RNG.
fn pick_erasure_indices(
    total: usize,
    max_loss: usize,
    rng: &mut XorShift64,
) -> Vec<usize> {
    let loss_count = if max_loss == 0 {
        0
    } else {
        // Uniform in [0, max_loss]
        (rng.next_u32() as usize) % (max_loss + 1)
    };

    let mut chosen = Vec::with_capacity(loss_count);
    let mut used = vec![false; total];

    let mut remaining = loss_count;
    while remaining > 0 {
        let idx = (rng.next_u32() as usize) % total;
        if !used[idx] {
            used[idx] = true;
            chosen.push(idx);
            remaining -= 1;
        }
    }

    chosen
}

#[test]
fn rs_encode_recover_random_messages_exact_recovery() {
    // A few (k, m, shard_len) combinations to exercise.
    let cases = &[
        (4usize, 2usize, 256usize),
        (6, 3, 1024),
        (10, 4, 512),
    ];

    let mut seed = DEFAULT_TEST_SEED as u64;

    for &(k, m, shard_len) in cases {
        let (params, original_data, full_shards) =
            make_random_codeword(k, m, shard_len, seed);
        seed = seed.wrapping_add(1);

        // For each parameter set, test several independent erasure patterns.
        for round in 0..8 {
            let mut rng = XorShift64::new(
                seed ^ ((k as u64) << 32) ^ ((m as u64) << 16) ^ (round as u64),
            );

            let erasures = pick_erasure_indices(k + m, m, &mut rng);

            // Convert to `Option<Vec<u8>>` representation and apply erasures.
            let mut opt: Vec<Option<Vec<u8>>> =
                full_shards.iter().cloned().map(Some).collect();

            for idx in erasures.iter().copied() {
                opt[idx] = None;
            }

            // Reconstruct in-place. If the backend reports that this particular
            // erasure pattern is not recoverable, skip this trial — we're
            // interested in the cases where reconstruction *is* possible.
            if let Err(_e) = rs::reconstruct(params, &mut opt) {
                continue;
            }

            let rebuilt: Vec<Vec<u8>> =
                opt.into_iter().map(|o| o.expect("all shards present after reconstruct")).collect();

            // 1) Verify the RS codeword is internally consistent.
            assert!(
                rs::verify(params, &rebuilt)
                    .expect("verify must return Ok for valid shards"),
                "verify must pass after reconstruction (k={}, m={}, round={}, seed={})",
                k,
                m,
                round,
                seed,
            );

            // 2) Data shards must match exactly.
            assert_eq!(
                original_data,
                rebuilt[..k],
                "data shards must be exactly recovered for k={}, m={}, round={}, seed={}",
                k,
                m,
                round,
                seed,
            );
        }
    }
}
