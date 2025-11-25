mod common;

use animica_native::rs::{self, RsParams};
use common::{XorShift64, DEFAULT_TEST_SEED};

fn make_random_codeword(
    k: usize,
    m: usize,
    shard_len: usize,
    seed: u64,
) -> (RsParams, Vec<Vec<u8>>) {
    let params = RsParams {
        data_shards: k,
        parity_shards: m,
    };

    let mut rng = XorShift64::new(seed);
    let mut shards = vec![vec![0u8; shard_len]; k + m];

    // Fill data shards with pseudo-random bytes.
    for s in &mut shards[..k] {
        rng.fill_bytes(s);
    }

    rs::encode_in_place(params, &mut shards)
        .expect("encode_in_place should succeed for valid parameters");

    (params, shards)
}

#[test]
fn reconstruct_fails_when_all_shards_are_missing() {
    let k = 4usize;
    let m = 2usize;
    let shard_len = 256usize;

    let (params, shards) = make_random_codeword(k, m, shard_len, DEFAULT_TEST_SEED as u64);

    // Convert to Option<Vec<u8>> and then erase *all* shards.
    let mut opt: Vec<Option<Vec<u8>>> =
        shards.into_iter().map(Some).collect();
    for s in &mut opt {
        *s = None;
    }

    let res = rs::reconstruct(params, &mut opt);
    assert!(
        res.is_err(),
        "reconstruct must fail when all shards are missing (k={}, m={})",
        k,
        m
    );
}

#[test]
fn reconstruct_fails_when_fewer_than_data_shards_remain() {
    let k = 4usize;
    let m = 2usize;
    let shard_len = 256usize;

    let (params, shards) = make_random_codeword(k, m, shard_len, (DEFAULT_TEST_SEED + 1) as u64);

    // Start with all shards present.
    let mut opt: Vec<Option<Vec<u8>>> =
        shards.into_iter().map(Some).collect();

    // Erase enough shards that < k remain (e.g. leave only 3 out of 6).
    // Here we just keep indices 0, 1, 2 and erase the rest.
    for idx in 3..opt.len() {
        opt[idx] = None;
    }

    let present = opt.iter().filter(|s| s.is_some()).count();
    assert!(
        present < params.data_shards,
        "test setup bug: present={} should be < data_shards={}",
        present,
        params.data_shards
    );

    let res = rs::reconstruct(params, &mut opt);
    assert!(
        res.is_err(),
        "reconstruct must fail when present < data_shards (present={}, k={}, m={})",
        present,
        k,
        m
    );
}
