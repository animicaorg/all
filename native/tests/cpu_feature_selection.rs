//! CPU feature / backend selection sanity tests for animica_native hashing.
//!
//! We don't assert which backend (AVX2 vs portable, etc.) is used — that's
//! platform/build dependent. Instead we assert that *whatever* backend is
//! selected, the public hashing API is:
//!   - deterministic
//!   - thread-safe (stable under concurrent use)
//!   - not accidentally wired to the same function for different algorithms.

mod common;

use std::sync::{Arc, Barrier};
use std::thread;

use animica_native::{blake3_hash, sha3_256_hash};
use animica_native::hash::Digest32;
use common::{random_bytes, rng_from_env};

/// Helper: hash a message with both algorithms.
fn hash_pair(msg: &[u8]) -> (Digest32, Digest32) {
    let h_sha3 = sha3_256_hash(msg);
    let h_blake = blake3_hash(msg);
    (h_sha3, h_blake)
}

/// 1) Stress backend selection by hashing from multiple threads and requiring
/// that all threads observe the same outputs as a single-threaded "golden" run.
#[test]
fn cpu_feature_selection_is_thread_safe_and_deterministic() {
    // Build a small corpus of messages with varying sizes.
    let mut rng = rng_from_env();
    let mut msgs: Vec<Vec<u8>> = Vec::new();

    // Some fixed messages for readability/debugging.
    msgs.push(b"".to_vec());
    msgs.push(b"animica-cpu-feature-selection".to_vec());
    msgs.push(b"the quick brown fox jumps over the lazy dog".to_vec());

    // Add a few random messages of random lengths up to 4 KiB.
    for _ in 0..8 {
        let len = (rng.next_u64() as usize) % 4096;
        msgs.push(random_bytes(len, &mut rng));
    }

    // Golden hashes computed single-threaded.
    let golden: Vec<(Digest32, Digest32)> =
        msgs.iter().map(|m| hash_pair(m)).collect();

    let msgs = Arc::new(msgs);
    let golden = Arc::new(golden);

    let workers = 8usize;
    let barrier = Arc::new(Barrier::new(workers));

    let mut handles = Vec::with_capacity(workers);

    for worker_id in 0..workers {
        let msgs = Arc::clone(&msgs);
        let golden = Arc::clone(&golden);
        let barrier = Arc::clone(&barrier);

        let handle = thread::spawn(move || {
            // Ensure all threads start hashing at roughly the same time.
            barrier.wait();

            for (idx, msg) in msgs.iter().enumerate() {
                let (sha3_g, blake_g) = golden[idx];
                let (sha3_t, blake_t) = hash_pair(msg);

                assert_eq!(
                    sha3_g, sha3_t,
                    "sha3_256_hash result must be stable across threads \
                     (worker={}, msg_idx={})",
                    worker_id, idx
                );
                assert_eq!(
                    blake_g, blake_t,
                    "blake3_hash result must be stable across threads \
                     (worker={}, msg_idx={})",
                    worker_id, idx
                );
            }
        });

        handles.push(handle);
    }

    for h in handles {
        h.join().expect("worker thread panicked");
    }
}

/// 2) Sanity check: for a few fixed inputs, the two different algorithms should
/// not collapse to the same digest. This helps catch wiring mistakes where
/// both functions might accidentally call the same backend.
#[test]
fn distinct_hash_algorithms_produce_distinct_digests_for_typical_inputs() {
    let samples: &[&[u8]] = &[
        b"animica-backend-test",
        b"cpu-feature-selection",
        b"the quick brown fox jumps over the lazy dog",
        b"another test vector",
    ];

    for msg in samples {
        let h_sha3 = sha3_256_hash(msg);
        let h_blake = blake3_hash(msg);
        assert_ne!(
            h_sha3, h_blake,
            "sha3_256_hash and blake3_hash should produce different digests \
             for msg {:?}",
            std::str::from_utf8(msg).unwrap_or("<non-utf8>")
        );
    }
}
