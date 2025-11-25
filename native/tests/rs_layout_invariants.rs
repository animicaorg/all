mod common;

use animica_native::rs::{self, RsParams};
use common::{XorShift64, DEFAULT_TEST_SEED};

/// Build a deterministic shard matrix:
/// - `k` data shards
/// - `m` parity shards (initially zeroed; encode_in_place will fill them)
/// - each shard has length `shard_len`
///
/// Data shard bytes are deterministic but simple, so we can later assert
/// they are unchanged by `encode_in_place`.
fn make_deterministic_shards(
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

    for (row_idx, shard) in shards[..k].iter_mut().enumerate() {
        // Fill data shard with a simple deterministic pattern mixed with RNG:
        // byte = (row_idx as u8) ^ random_byte
        let mut tmp = vec![0u8; shard_len];
        rng.fill_bytes(&mut tmp);
        for (b, r) in shard.iter_mut().zip(tmp.iter()) {
            *b = (row_idx as u8) ^ *r;
        }
    }

    (params, shards)
}

/// Helper: all shards must have identical length (or slice empty).
fn all_shards_same_len(shards: &[Vec<u8>]) -> bool {
    if shards.is_empty() {
        return true;
    }
    let n = shards[0].len();
    shards.iter().all(|s| s.len() == n)
}

/// Layout invariant: after calling encode_in_place,
///   - all shards (data and parity) have the same length,
///   - the first k shards (data) retain their contents,
///   - verify() says the layout is a valid codeword.
#[test]
fn layout_preserves_data_shards_and_row_lengths() {
    let cases = &[
        (4usize, 2usize, 64usize),
        (6, 3, 128),
        (10, 4, 255),
    ];

    for (case_idx, &(k, m, shard_len)) in cases.iter().enumerate() {
        let seed = (DEFAULT_TEST_SEED as u64).wrapping_add(case_idx as u64);
        let (params, mut shards) = make_deterministic_shards(k, m, shard_len, seed);

        // Snapshot data shard contents before encoding.
        let before_data = shards[..k].to_vec();

        rs::encode_in_place(params, &mut shards)
            .expect("encode_in_place should succeed for valid layout");

        // 1) All shards must have same length.
        assert!(
            all_shards_same_len(&shards),
            "all shards must have identical length after encode_in_place (k={}, m={})",
            k,
            m
        );
        assert_eq!(
            shards.len(),
            k + m,
            "encode_in_place must not change the number of shards (k={}, m={})",
            k,
            m
        );

        // 2) Data shards (first k) must keep their contents.
        assert_eq!(
            before_data,
            shards[..k],
            "encode_in_place must not modify data shards contents (k={}, m={})",
            k,
            m
        );

        // 3) The resulting shard matrix must be a valid RS codeword.
        let ok = rs::verify(params, &shards)
            .expect("verify must not fail for well-formed shards");
        assert!(
            ok,
            "verify() must accept the encoded layout as a valid codeword (k={}, m={})",
            k,
            m
        );
    }
}

/// Layout invariant: malformed layouts (wrong shard count, mismatched shard
/// lengths) must *not* be accepted as valid by verify().
#[test]
fn layout_rejects_mismatched_counts_and_lengths() {
    let k = 4usize;
    let m = 2usize;
    let shard_len = 64usize;
    let (params, mut shards) =
        make_deterministic_shards(k, m, shard_len, (DEFAULT_TEST_SEED + 42) as u64);

    rs::encode_in_place(params, &mut shards)
        .expect("encode_in_place should succeed for valid layout");

    // Sanity: original layout is valid.
    let ok = rs::verify(params, &shards)
        .expect("verify must not fail for well-formed shards");
    assert!(ok, "baseline encoded layout must be valid before mutation");

    // Case 1: wrong shard count (drop one shard).
    let mut fewer = shards.clone();
    fewer.pop();
    let res = rs::verify(params, &fewer);
    // Either the call errors or returns false; both are acceptable, but a
    // *true* here would be a serious invariant violation.
    assert!(
        res.map(|v| !v).unwrap_or(true),
        "verify() must not accept shard matrices with wrong shard count"
    );

    // Case 2: mismatched lengths (truncate one shard).
    let mut mismatched = shards.clone();
    mismatched[0].truncate(shard_len / 2); // shorter than others
    let res = rs::verify(params, &mismatched);
    assert!(
        res.map(|v| !v).unwrap_or(true),
        "verify() must not accept shard matrices with mismatched shard lengths"
    );
}

/// Layout invariant: the first k shards are always treated as data, and
/// verify() fails if you arbitrarily swap data and parity shards.
#[test]
fn layout_is_sensitive_to_data_parity_indexing() {
    let k = 4usize;
    let m = 2usize;
    let shard_len = 64usize;
    let (params, mut shards) =
        make_deterministic_shards(k, m, shard_len, (DEFAULT_TEST_SEED + 1337) as u64);

    rs::encode_in_place(params, &mut shards)
        .expect("encode_in_place should succeed for valid layout");

    // Baseline: valid codeword.
    assert!(
        rs::verify(params, &shards).expect("verify should not error for baseline"),
        "baseline layout must be valid before swapping"
    );

    // Swap one data shard with one parity shard: this breaks indexing assumptions.
    shards.swap(0, k); // swap first data shard with first parity shard

    let res = rs::verify(params, &shards);
    assert!(
        res.map(|v| !v).unwrap_or(true),
        "verify() must not accept shards when data/parity indexing has been scrambled"
    );
}
