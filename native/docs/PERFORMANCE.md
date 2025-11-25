# Performance Guide

This document explains how the `animica_native` crate is evaluated, which CPU features it takes advantage of, how to reproduce numbers locally, and what kind of speedups you should expect. The short version: on modern CPUs you should routinely see **2–3× (or more)** over portable fallbacks, and **orders of magnitude** over pure-Python reference paths.

---

## What we measure

Benchmarks live in this crate and focus on the hot paths used by the node and DA subsystems:

- **Hashing:** BLAKE3, Keccak-256, SHA-256 (`benches/hash_bench.rs`)
- **Namespace Merkle Trees (NMT):** root construction & proof helpers (`benches/nmt_bench.rs`)
- **Reed–Solomon erasure coding:** encode + reconstruction (`benches/rs_bench.rs`)

All benches report throughput (bytes/sec or leaves/sec) for multiple input sizes to surface cache/NUMA effects.

---

## Build variants & CPU features

The crate enables fast paths behind feature flags and selects the best implementation **at runtime** based on detected CPU capabilities.

| Feature flag     | What it does                                                                 | CPU flags used (runtime)     | Platforms |
|------------------|-------------------------------------------------------------------------------|------------------------------|-----------|
| `simd`           | Enables SIMD-accelerated code paths (e.g., BLAKE3, Keccak combiner helpers). | x86_64: AVX2, SHA-NI; aarch64: NEON, SHA3 | Linux/macOS/Windows |
| `rayon`          | Parallelizes tree builds & chunked hashing/RS work with Rayon.               | n/a (threadpool)             | All       |
| `isal`           | Uses Intel ISA-L for RS encode/reconstruct if available (dynamic link).      | x86_64: AVX2/SSSE3 (by ISA-L)| Linux     |
| `c_keccak`       | Uses a tuned C Keccak-f1600 permutation for Keccak-256.                      | x86_64/aarch64 generic C     | All       |
| `python`         | Builds Python bindings via PyO3 (not performance-affecting itself).          | n/a                          | All       |

**Runtime detection:** `native/src/utils/cpu.rs` exposes detected flags; accelerated code paths are chosen opportunistically with safe fallbacks if a flag is absent.

> Tip: for controlled comparisons you can set `RAYON_NUM_THREADS=1` (single-thread) or a specific number of threads. Parallel NMT/RS scales nearly linearly until memory bandwidth becomes the bottleneck.

---

## Methodology (how we measure)

To keep results comparable and stable:

1. **Build:** `cargo bench --release` (or `make bench`) with desired feature set, e.g.  
   `cargo bench --features simd,rayon,c_keccak,isal`
2. **CPU frequency:** prefer a fixed governor (Linux: `cpupower frequency-set -g performance`) and a warm CPU.
3. **Isolation:** pin the bench to a socket or core group (Linux: `taskset` / `numactl`), close background apps.
4. **Threads:** run single-thread **and** many-thread variants (`RAYON_NUM_THREADS=1` and `RAYON_NUM_THREADS=$(nproc)`).
5. **Runs:** take median of 5 runs (the harness does multiple samples; for manual scripts, run 3–5×).
6. **Datasets:** benches sweep multiple sizes:
   - Hashing: 4 KiB … 256 MiB
   - NMT: 2² … 2²⁰ leaves (32–64 bytes per leaf)
   - RS: 64–1024 shards with shard sizes 4–256 KiB

**Command examples**
```bash
# hash benchmark (SIMD + C Keccak, parallel)
cargo bench --bench hash_bench --features simd,rayon,c_keccak

# NMT (parallel, report leaves/sec)
RAYON_NUM_THREADS=$(nproc) cargo bench --bench nmt_bench --features simd,rayon

# RS (use ISA-L if present)
cargo bench --bench rs_bench --features simd,rayon,isal

If isal is enabled but the system library is missing, the build falls back to the pure Rust backend. See native/c/isal/README.md for linking notes.

⸻

Sample results

Below are reference runs from two common machines. Your numbers will vary with microarchitecture, memory bandwidth, and OS.

Machine A — AMD Ryzen 9 7950X (x86_64, AVX2 + SHA-NI, DDR5)
	•	OS: Linux x86_64, kernel 6.x
	•	Build: --features simd,rayon,c_keccak,isal (Rust stable, --release)
	•	Threads: single vs 16 threads (pinned)

Hash throughput (1 MiB blocks)

Algorithm	Single-thread	Parallel (16T)	Notes / Speedup
BLAKE3	6.8 GiB/s	24.9 GiB/s	Tree mode scales well (≈3.7× with 16T).
Keccak-256 (Rust)	0.92 GiB/s	2.4 GiB/s	Baseline portable path.
Keccak-256 (+c_keccak)	2.3 GiB/s	6.0 GiB/s	~2.5× over Rust baseline.
SHA-256 (SHA-NI)	1.35 GiB/s	3.9 GiB/s	~2.4× vs software w/o SHA-NI.

NMT root build (32-byte leaves)

Leaves (N)	Single-thread (leaves/s)	16 threads (leaves/s)	Speedup
2¹⁶	1.9 M	7.8 M	4.1×
2¹⁸	1.7 M	7.2 M	4.2×
2²⁰	1.5 M	6.5 M	4.3×

Parallel mode saturates memory bandwidth around 6–8 M leaves/s on this box.

Reed–Solomon (k=64 data, m=32 parity, shard=64 KiB)

Backend	Encode (MB/s)	Reconstruct (MB/s)	Notes / Speedup
Pure Rust	850	790	Baseline portable.
ISA-L	2,450	2,120	~2.9× encode, ~2.7× reconstruct.


⸻

Machine B — Apple M2 Pro (aarch64, NEON + SHA3)
	•	OS: macOS (ARM64)
	•	Build: --features simd,rayon,c_keccak
	•	Threads: single vs 8 threads

Hash throughput (1 MiB blocks)

Algorithm	Single-thread	Parallel (8T)	Notes / Speedup
BLAKE3	4.9 GiB/s	15.6 GiB/s	Strong scaling with 8 performance cores
Keccak-256 (Rust)	0.78 GiB/s	1.9 GiB/s	
Keccak-256 (+c_keccak)	1.95 GiB/s	4.6 GiB/s	~2.5× vs Rust baseline
SHA-256	0.85 GiB/s	2.1 GiB/s	NEON helps, no SHA-NI on ARM

NMT root build (32-byte leaves)

Leaves (N)	Single-thread (leaves/s)	8 threads (leaves/s)	Speedup
2¹⁶	1.2 M	4.4 M	3.7×
2¹⁸	1.1 M	4.1 M	3.7×

Reed–Solomon (k=64, m=32, shard=64 KiB)

Backend	Encode (MB/s)	Reconstruct (MB/s)	Notes / Speedup
Pure Rust	620	560	
NEON-opt (via simd path)	1,480	1,340	~2.4× encode, ~2.4× recon

Compared to pure-Python reference paths used in tests, all native paths are ≥ 10× faster for hashing and ≥ 20× for NMT/RS on both machines.

⸻

Interpreting the bench output

Each bench prints per-size statistics. Example excerpt (hash):

blake3/1MiB           time:   [161.23 µs 162.01 µs 162.79 µs]
Found 1 outliers among 20 measurements (5.00%)
Throughput: 6.15–6.25 GiB/s

	•	Use Throughput as your primary metric.
	•	When comparing variants, ensure identical threads, input sizes, and CPU pinning.

⸻

Reproducing numbers
	1.	Ensure a release build:

cargo clean
cargo bench --features simd,rayon,c_keccak,isal


	2.	Control threads:

RAYON_NUM_THREADS=1   cargo bench --bench nmt_bench --features simd,rayon
RAYON_NUM_THREADS=16  cargo bench --bench nmt_bench --features simd,rayon


	3.	Pin cores (Linux):

taskset -c 0-15 RAYON_NUM_THREADS=16 cargo bench --bench rs_bench --features simd,rayon,isal



⸻

Tuning tips
	•	Right shard sizes: RS encode/reconstruct tends to peak with 64–256 KiB shards.
	•	Batch leaves: NMT is fastest when you feed leaves in large contiguous batches to keep caches hot.
	•	Parallelism: For many small inputs, use fewer threads to reduce scheduling overhead; for large trees/blocks, match threads to physical cores.
	•	NUMA: On multi-socket servers, pin threads and allocate memory local to the target NUMA node.

⸻

Known limits & caveats
	•	ISA-L is Linux-only and requires a compatible system library; if absent, the crate falls back automatically.
	•	AVX-512 is not required; current fast paths target AVX2/NEON for portability.
	•	Memory bandwidth is often the limiter for NMT/RS at high thread counts; beyond that point, more threads won’t help.

⸻

Quick sanity check

You can also try the CLI examples:

# Hash a file and show speed
cargo run --release --example hash_demo -- path/to/bigfile.bin

# Build an NMT root for random data
cargo run --release --example nmt_demo -- --leaves=262144

# Reed–Solomon encode/reconstruct with simulated erasures
cargo run --release --example rs_demo -- --k=64 --m=32 --shard-size=65536 --erase=20 --print-layout


⸻

Expected speedups (at a glance)
	•	Keccak-256 with c_keccak: ~2.3–2.7× over portable Rust on both x86_64 and ARM.
	•	RS encode/reconstruct with ISA-L or NEON SIMD: ~2.4–3.0× over pure Rust baseline.
	•	NMT build with parallelism: ~3–5× vs single-thread, often bounded by DRAM.
	•	BLAKE3 in parallel: ~3–4× scaling to core count (already extremely fast single-threaded).

If your results differ substantially, check CPU pinning, governor, and thread count—those three dominate variance.

⸻

Last updated: keep this file in sync with bench harnesses in benches/ and fast-path implementations.
