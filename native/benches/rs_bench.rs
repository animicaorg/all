// Benchmark: Reed–Solomon encode & reconstruct throughput
// - Reports MB/s across shard sizes and (data, parity) shard counts
// - Measures two key ops:
//     1) encode: build parity shards from data shards
//     2) reconstruct: recover from erasures (up to parity_shards)
// - Optional fast paths via features: `simd`, `rayon`, `isal`
//
// Run:
//   cargo bench --bench rs_bench
//   cargo bench --bench rs_bench --features "simd rayon"
//   cargo bench --bench rs_bench --features "isal"      # if ISA-L is available
//
// Notes:
// - We keep the benchmark deterministic and avoid RNG deps.
// - Throughput uses Criterion::Throughput::Bytes over TOTAL DATA BYTES
//   (data_shards * shard_size). This matches "useful payload" rate.
//
// Expected bench API (thin wrappers around the public RS module):
//   mod animica_native::rs::bench_api {
//       /// Encode parity for given data shards (each shard equal length).
//       /// Returns full shard vector [data..., parity...], length = data+parity.
//       pub fn encode(data_shards: &[Vec<u8>], parity_shards: usize) -> Vec<Vec<u8>>;
//
//       /// Reconstructs missing shards in-place. `None` entries represent erasures.
//       /// Returns true on success; panics/returns false if unrecoverable.
//       pub fn reconstruct_in_place(shards: &mut [Option<Vec<u8>>]) -> bool;
//   }
//
// If your API differs slightly, adjust the `use` and callsites below.

use criterion::{black_box, criterion_group, criterion_main, Criterion, Throughput};

#[allow(unused_imports)]
use animica_native::rs::bench_api::{encode, reconstruct_in_place};

// ---- bench matrix -------------------------------------------------------------

/// Bytes per shard (payload only; each shard is equal length).
const SHARD_SIZES: &[usize] = &[
    16 * 1024,   // 16 KiB  — latency-oriented
    64 * 1024,   // 64 KiB  — common moderate setting
    256 * 1024,  // 256 KiB — throughput-oriented
];

/// Number of data shards to sweep.
const DATA_SHARDS: &[usize] = &[
    4, 8, 10, 12, 16,
];

/// Candidate parity counts; combos where parity >= data are skipped.
const PARITY_SHARDS: &[usize] = &[
    2, 4, 6, 8,
];

/// Erasure patterns to try per (d, p); values beyond p are clamped.
const ERASURE_COUNTS: &[usize] = &[
    1, 2, 3, // light loss (<=3)
    usize::MAX, // special: clamp to p (max feasible erasures)
];

// ---- helpers ------------------------------------------------------------------

fn fill_shard(idx: usize, shard_size: usize) -> Vec<u8> {
    // Fast, deterministic filler (Xorshift-like), no external RNG.
    let mut v = vec![0u8; shard_size];
    let mut x: u32 = 0xC0FEBABEu32 ^ (idx as u32) ^ (shard_size as u32);
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

/// Build `d` data shards (Vec<Vec<u8>>) each of `shard_size` bytes.
fn make_data_shards(d: usize, shard_size: usize) -> Vec<Vec<u8>> {
    (0..d).map(|i| fill_shard(i, shard_size)).collect()
}

/// Convert full shard set into `Option<Vec<u8>>` with `e` erasures
/// across data shards first (then parity if needed), for reconstruction bench.
fn with_erasures(mut full: Vec<Vec<u8>>, d: usize, mut e: usize) -> Vec<Option<Vec<u8>>> {
    let total = full.len();
    let mut out: Vec<Option<Vec<u8>>> = full.drain(..).map(Some).collect();

    // Erase from data region first
    let data_region = 0..d.min(total);
    for i in data_region {
        if e == 0 { break; }
        out[i] = None;
        e -= 1;
    }
    // If still need erasures, erase from parity region
    for i in d..total {
        if e == 0 { break; }
        out[i] = None;
        e -= 1;
    }
    out
}

// ---- benches ------------------------------------------------------------------

fn bench_rs_encode_reconstruct(c: &mut Criterion) {
    for &d in DATA_SHARDS {
        for &p in PARITY_SHARDS {
            if p >= d { continue; } // skip invalid / worst-case-heavy combos
            for &sz in SHARD_SIZES {
                let bytes_total = (d * sz) as u64;
                let bench_group_name = format!("rs/d{d}+p{p}@{sz}B");

                // Prepare data shards once.
                let data = make_data_shards(d, sz);

                let mut group = c.benchmark_group(bench_group_name);
                group.throughput(Throughput::Bytes(bytes_total));

                // --- Encode parity ---
                group.bench_function("encode", |b| {
                    b.iter(|| {
                        // Fresh encode per iter (simulate "new stripe" workload)
                        let shards = encode(black_box(&data), black_box(p));
                        black_box(shards);
                    });
                });

                // Precompute a baseline full set to start reconstruction from.
                let full = encode(&data, p); // data + parity, all present

                // --- Reconstruct from erasures (various loss levels) ---
                for &e_req in ERASURE_COUNTS {
                    // clamp to feasible erasure budget
                    let e = e_req.min(p);
                    if e == 0 { continue; }

                    let label = format!("reconstruct/erase{e}");
                    group.bench_function(&label, |b| {
                        b.iter(|| {
                            // For each iter, create a fresh missing pattern to avoid caching luck.
                            let mut missing = with_erasures(full.clone(), d, e);
                            let ok = reconstruct_in_place(black_box(&mut missing));
                            black_box(ok);
                        });
                    });
                }

                group.finish();
            }
        }
    }
}

criterion_group! {
    name = benches;
    config = {
        // Slightly longer windows to stabilize MB/s for larger stripes.
        let mut c = Criterion::default()
            .warm_up_time(std::time::Duration::from_secs(3))
            .measurement_time(std::time::Duration::from_secs(10))
            .sample_size(40);
        c
    };
    targets = bench_rs_encode_reconstruct
}
criterion_main!(benches);

// -------------------------------------------------------------------------------
// Interpreting results:
// - Encode MB/s should increase with shard size and benefit from SIMD/ISA-L.
// - Reconstruct cost grows with erasures (syndrome solve + parity math).
// - Expect best speedups when `--features simd` or `--features isal` are enabled.
// - On NUMA/HT systems, pin cores to reduce variance:
//     taskset -c 0-7 cargo bench --bench rs_bench --features "simd rayon"
// -------------------------------------------------------------------------------
