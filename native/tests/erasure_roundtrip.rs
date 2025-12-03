mod common;

use animica_native::rs::{self, bench_api, RsParams};
use common::{choose_k_of_n, XorShift64, DEFAULT_TEST_SEED};

const DATA_SHARDS: usize = 64;
const PARITY_SHARDS: usize = 64;
const SHARD_SIZE: usize = 4096;
const MAX_ERASURE_PERCENT: usize = 40; // drop up to 40% of shards
const PAYLOAD_LEN: usize = 200_000; // non-multiple of shard len

fn payload_to_shards(payload: &[u8]) -> Vec<Vec<u8>> {
    let mut shards = Vec::with_capacity(DATA_SHARDS);
    let mut offset = 0usize;
    for _ in 0..DATA_SHARDS {
        let take = (payload.len().saturating_sub(offset)).min(SHARD_SIZE);
        let mut shard = vec![0u8; SHARD_SIZE];
        let end = offset + take;
        shard[..take].copy_from_slice(&payload[offset..end]);
        offset = end;
        shards.push(shard);
    }
    shards
}

fn shards_to_payload(shards: &[Vec<u8>]) -> Vec<u8> {
    let mut out = Vec::with_capacity(DATA_SHARDS * SHARD_SIZE);
    for shard in shards.iter().take(DATA_SHARDS) {
        out.extend_from_slice(shard);
    }
    out.truncate(PAYLOAD_LEN);
    out
}

#[test]
fn blob_encode_recover_with_random_losses() {
    let mut rng = XorShift64::new(DEFAULT_TEST_SEED ^ 0xEC0D_1A77);
    let mut payload = vec![0u8; PAYLOAD_LEN];
    rng.fill_bytes(&mut payload);

    let data_shards = payload_to_shards(&payload);

    let shards = bench_api::encode(&data_shards, PARITY_SHARDS);
    let params = RsParams {
        data_shards: DATA_SHARDS,
        parity_shards: PARITY_SHARDS,
    };

    let total_shards = params.total();
    let max_losses = ((total_shards * MAX_ERASURE_PERCENT) / 100).min(PARITY_SHARDS);
    assert!(max_losses > 0);

    for round in 0..8 {
        let mut trial_rng = XorShift64::new(DEFAULT_TEST_SEED.wrapping_add(round as u64));
        let loss_count = (trial_rng.next_u32() as usize) % (max_losses + 1);
        let losses = choose_k_of_n(total_shards, loss_count, &mut trial_rng);

        let mut maybe_shards: Vec<Option<Vec<u8>>> = shards.iter().cloned().map(Some).collect();
        for idx in losses {
            maybe_shards[idx] = None;
        }

        rs::reconstruct(params, &mut maybe_shards)
            .expect("reconstruct should succeed with up to 50% erasures");

        let rebuilt: Vec<Vec<u8>> = maybe_shards
            .into_iter()
            .map(|o| o.expect("all shards must be present after reconstruct"))
            .collect();

        let rebuilt_payload = shards_to_payload(&rebuilt);

        assert_eq!(payload, rebuilt_payload, "round {round} should round-trip");
        assert!(
            rs::verify(params, &rebuilt).expect("verify should accept reconstructed shards"),
            "round {round} reconstructed shards must verify"
        );
    }
}
