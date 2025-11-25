// Benchmark: Namespace Merkle Tree (NMT) root construction
// - Measures leaves/sec and total root time across (#leaves, leaf_size)
// - Compares serial vs parallel builders (if 'rayon' feature is enabled)
//
// Run:
//   cargo bench --bench nmt_bench
//   cargo bench --bench nmt_bench --features rayon
//
// Notes:
// - Uses Criterion for robust statistics.
// - We pre-encode leaves once to exclude encoding overhead from root building.
// - Throughput is reported as Elements (leaves) per second.
// - If the crate is built without `rayon`, only the serial benchmark runs.
//
// Expected bench API (mirrors production internals):
//   mod animica_native::nmt::bench_api {
//       use super::*;
//       pub fn encode_leaf(ns: [u8; 8], payload: &[u8]) -> Vec<u8>;
//       pub fn root_serial(encoded_leaves: &[Vec<u8>]) -> [u8; 32];
//       #[cfg(feature = "rayon")]
//       pub fn root_parallel(encoded_leaves: &[Vec<u8>]) -> [u8; 32];
//   }
//
// If your crate exposes slightly different names, adjust the `use` block below.

use criterion::{black_box, criterion_group, criterion_main, Criterion, Throughput};

// ---- bench parameters ---------------------------------------------------------

/// Leaf payload sizes to sweep (bytes), excluding namespace/len prefixes.
const LEAF_SIZES: &[usize] = &[
    32,         // tiny
    256,        // small
    1024,       // 1 KiB
    4096,       // 4 KiB
];

/// Number of leaves per tree. Cover small to moderately large trees.
const LEAF_COUNTS: &[usize] = &[
    256,        // 2^8
    1024,       // 2^10
    4096,       // 2^12
    16384,      // 2^14
];

/// Deterministic namespace for this run; vary if you want cross-namespace perf.
const NS: [u8; 8] = *b"benchNMT";

#[allow(unused_imports)]
use animica_native::nmt::bench_api::{
    encode_leaf,
    root_serial,
    #[cfg(feature = "rayon")]
    root_parallel,
};

// ---- helpers ------------------------------------------------------------------

/// Deterministic, cheap filler (no rand dep) so results are reproducible.
fn make_payload(i: usize, size: usize) -> Vec<u8> {
    let mut v = vec![0u8; size];
    let mut x: u32 = 0x9E3779B9u32 ^ (i as u32) ^ (size as u32);
    for chunk in v.chunks_mut(4) {
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        let b = x.to_le_bytes();
        let n = chunk.len();
        chunk.copy_from_slice(&b[..n]);
    }
    v
}

/// Pre-encode all leaves once so the benchmark isolates root-building work.
fn build_encoded_leaves(count: usize, leaf_size: usize) -> Vec<Vec<u8>> {
    (0..count)
        .map(|i| {
            let payload = make_payload(i, leaf_size);
            encode_leaf(NS, &payload)
        })
        .collect()
}

// ---- benches ------------------------------------------------------------------

fn bench_nmt_roots(c: &mut Criterion) {
    for &n in LEAF_COUNTS {
        for &sz in LEAF_SIZES {
            // Prepare input set once per (n, sz).
            let encoded = build_encoded_leaves(n, sz);

            // Group name conveys cardinality & size; throughput is in leaves/sec.
            let mut group = c.benchmark_group(format!("nmt/{n}x{sz}B"));
            group.throughput(Throughput::Elements(n as u64));

            // --- Serial root ---
            group.bench_function("root_serial", |b| {
                b.iter(|| {
                    let root = root_serial(black_box(&encoded));
                    black_box(root);
                })
            });

            // --- Parallel root (if available) ---
            #[cfg(feature = "rayon")]
            {
                group.bench_function("root_parallel", |b| {
                    b.iter(|| {
                        let root = root_parallel(black_box(&encoded));
                        black_box(root);
                    })
                });
            }

            group.finish();
        }
    }
}

criterion_group! {
    name = benches;
    config = {
        // Slightly longer windows for large trees.
        let mut c = Criterion::default()
            .warm_up_time(std::time::Duration::from_secs(3))
            .measurement_time(std::time::Duration::from_secs(8))
            .sample_size(50);
        c
    };
    targets = bench_nmt_roots
}
criterion_main!(benches);

// -------------------------------------------------------------------------------
// Hints for analysis:
// - Compare root_parallel vs root_serial speedup as (#leaves, leaf_size) grows.
// - Parallelism overhead can dominate at small n/sizes; expect crossover points.
// - For CI w/ noisy neighbors, consider pinning CPU and disabling turbo.
//   Example: taskset -c 0-7 cargo bench --features rayon
// -------------------------------------------------------------------------------
