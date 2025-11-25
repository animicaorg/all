// Benchmark: BLAKE3, Keccak-256, and SHA-256 throughput across input sizes.
//
// Usage:
//   cargo bench --bench hash_bench
//
// Notes:
// - This uses Criterion for statistically robust measurements (warmup, outlier
//   detection, slope/mean confidence intervals). Ensure `criterion` is in
//   [dev-dependencies] of native/Cargo.toml.
// - The functions are taken from the crate's bench API so the same backends
//   (SIMD, C fast-paths) exercised in production are measured here.
// - Throughput is reported as bytes/sec per algorithm and size.
//
// Example Cargo.toml (dev-deps snippet):
// [dev-dependencies]
// criterion = "0.5"
//
// Optional feature toggles (compile-time):
//   --features simd       : enable SIMD-accelerated code paths where available
//   --features c_keccak   : enable the C keccak-f1600 fast path
//   --features rayon      : allow parallel hashing for large inputs (if used)
//
// Examples:
//   cargo bench --bench hash_bench
//   cargo bench --bench hash_bench --features c_keccak
//   RUSTFLAGS="-C target-cpu=native" cargo bench --bench hash_bench

use animica_native::hash::bench_api::{blake3_hash, keccak256_hash, sha256_hash};
use criterion::{black_box, criterion_group, criterion_main, BatchSize, Criterion, Throughput};

/// Sizes to sweep over (bytes). Tuned to cover cache lines up to large buffers.
/// Feel free to adjust via RUSTFLAGS env or edit if you need denser sampling.
const SIZES: &[usize] = &[
    64,         // small message
    256,        // ~ L1 sweet spot
    1024,       // 1 KiB
    8 * 1024,   // 8 KiB
    64 * 1024,  // 64 KiB
    256 * 1024, // 256 KiB
    1 * 1024 * 1024, // 1 MiB
    8 * 1024 * 1024, // 8 MiB (large streaming case)
];

/// Deterministic, low-overhead filler; diverse-enough to avoid silly edge cases.
/// We avoid `rand` to keep bench dependencies lean and reproducible.
fn make_data(len: usize) -> Vec<u8> {
    let mut v = vec![0u8; len];
    // XorShift32-style generator; deterministic, cheap, and good enough for benches.
    let mut x: u32 = 0x9E3779B9u32 ^ (len as u32);
    for chunk in v.chunks_mut(4) {
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        let bytes = x.to_le_bytes();
        let n = chunk.len();
        chunk.copy_from_slice(&bytes[..n]);
    }
    v
}

fn bench_hashes(c: &mut Criterion) {
    for &size in SIZES {
        // For large inputs, measuring allocation on every iter can dominate.
        // We therefore provide two styles:
        // 1) Fixed buffer reused across iterations (default).
        // 2) Batched fresh buffer (to include copy/alloc if you want it).
        //
        // Toggle which you prefer by flipping the commented blocks below.

        let mut group = c.benchmark_group(format!("hashes/{}B", size));
        group.throughput(Throughput::Bytes(size as u64));

        // --- Style 1: Fixed buffer (recommended for pure hash throughput) ---
        let data = make_data(size);

        group.bench_function("blake3", |b| {
            b.iter(|| {
                let out = blake3_hash(black_box(&data));
                black_box(out);
            })
        });

        group.bench_function("keccak256", |b| {
            b.iter(|| {
                let out = keccak256_hash(black_box(&data));
                black_box(out);
            })
        });

        group.bench_function("sha256", |b| {
            b.iter(|| {
                let out = sha256_hash(black_box(&data));
                black_box(out);
            })
        });

        // --- Style 2: Batched fresh buffer per iteration (disabled by default) ---
        // group.bench_function("blake3_batched", |b| {
        //     b.iter_batched(
        //         || make_data(size),
        //         |buf| { black_box(blake3_hash(black_box(&buf))); },
        //         BatchSize::LargeInput,
        //     )
        // });
        //
        // group.bench_function("keccak256_batched", |b| {
        //     b.iter_batched(
        //         || make_data(size),
        //         |buf| { black_box(keccak256_hash(black_box(&buf))); },
        //         BatchSize::LargeInput,
        //     )
        // });
        //
        // group.bench_function("sha256_batched", |b| {
        //     b.iter_batched(
        //         || make_data(size),
        //         |buf| { black_box(sha256_hash(black_box(&buf))); },
        //         BatchSize::LargeInput,
        //     )
        // });

        group.finish();
    }
}

criterion_group! {
    name = benches;
    config = {
        // Slightly longer warmup for large sizes; adjust if your CI is noisy.
        let mut c = Criterion::default()
            .warm_up_time(std::time::Duration::from_secs(3))
            .measurement_time(std::time::Duration::from_secs(8))
            .sample_size(60);
        c
    };
    targets = bench_hashes
}
criterion_main!(benches);

// --- crate bench API (expected) -------------------------------------------------
// In `native/src/hash/bench_api.rs`, the following signatures are assumed:
//
// pub fn blake3_hash(input: &[u8]) -> [u8; 32];
// pub fn keccak256_hash(input: &[u8]) -> [u8; 32];
// pub fn sha256_hash(input: &[u8]) -> [u8; 32];
//
// The intent is to route to the same internal backends as production code,
// ensuring feature flags and runtime CPU detection are exercised by the bench.
// --------------------------------------------------------------------------------
