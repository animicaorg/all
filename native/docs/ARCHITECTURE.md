# `animica_native` Architecture

This document maps the internal modules, public APIs, feature flags, and the safety invariants that govern the `animica_native` crate. The crate provides high-performance hashing, Namespace Merkle Tree (NMT) construction/verification, and Reed–Solomon (RS) erasure coding, with optional Python bindings via PyO3.

---

## High-level Overview

- **Core goals**
  - Provide fast, safe, and portable primitives for hashing, NMT, and erasure coding.
  - Select optimal implementations at **runtime** based on CPU features.
  - Expose a minimal, ergonomic API to Rust and Python consumers.
- **Design pillars**
  - **Modularity:** Clear separation of algorithm families (`hash`, `nmt`, `rs`).
  - **Zero-copy edges:** Shared buffer views where possible; immutable inputs.
  - **Defensive checks:** Lengths, bounds, and shape invariants are validated early.
  - **Graceful fallback:** If a fast path is unavailable, portable code paths are used.

---

## Module Diagram

animica_native
├── src/lib.rs                 # Crate root: error map, feature gates, re-exports
├── src/error.rs               # NativeError and conversions to Python errors
├── src/utils
│   ├── mod.rs
│   ├── bytes.rs               # Zero-copy views, alignment/hex helpers
│   ├── cpu.rs                 # Runtime CPU flag detection
│   └── rayon_pool.rs          # Threadpool bootstrap/guard (feature: rayon)
├── src/hash
│   ├── mod.rs                 # HashFn trait, domain-separation tags
│   ├── blake3.rs              # BLAKE3 (parallel-friendly)
│   ├── keccak.rs              # Keccak-256; +C backend (feature: c_keccak)
│   ├── sha256.rs              # SHA-256; SHA-NI when available
│   └── bench_api.rs           # Thin perf API used by benches/tests
├── src/nmt
│   ├── mod.rs                 # Public API: nmt_root, open, verify
│   ├── types.rs               # NamespaceId, Leaf, Proof types
│   ├── hashers.rs             # Namespace-aware hash combiners (L/R)
│   ├── tree.rs                # Iterative bottom-up build
│   ├── verify.rs              # Proof verification (incl. range proofs)
│   ├── encode.rs              # Leaf encoding (len-prefix, namespaces)
│   └── parallel.rs            # Work-splitting strategies (feature: rayon)
├── src/rs
│   ├── mod.rs                 # Public API: encode, reconstruct, verify
│   ├── codec.rs               # reed-solomon-erasure wrapper
│   ├── isal.rs                # ISA-L backend (feature: isal; Linux/x86_64)
│   ├── layout.rs              # Shard layout, padding, alignment
│   └── verify.rs              # Parity and syndrome helpers
├── src/py                     # Python bindings (feature: python)
│   ├── mod.rs                 # #[pymodule] + submodule exports
│   ├── hash.rs                # blake3_hash, keccak256, sha256
│   ├── nmt.rs                 # nmt_root, nmt_verify
│   ├── rs.rs                  # rs_encode, rs_reconstruct
│   └── utils.rs               # Buffer conversions, zero-copy views
└── c/
├── keccak/keccak1600.c    # Tuned C permutation (+ header)
└── isal/                  # Notes on linking system ISA-L

benches/                       # Criterion benches (hash, nmt, rs)
tests/                         # Rust unit/integration tests
tests_py/                      # Python tests vs reference implementations

**Key relationships**

- `nmt::*` depends on `hash::*` for leaf/internal node hashing.
- `rs::*` optionally uses `isal` backend; otherwise pure Rust codec.
- `py::*` exposes curated APIs from `hash`, `nmt`, and `rs` with input validation and zero-copy buffers (when safe).

---

## Public APIs (Rust)

### Hashing
- `hash::blake3::hash(bytes) -> [u8; 32]`
- `hash::keccak::hash(bytes) -> [u8; 32]` (fast path if `c_keccak`)
- `hash::sha256::hash(bytes) -> [u8; 32]`
- Streaming interfaces are provided where relevant for large inputs.

### NMT
- `nmt::nmt_root(leaves: &[Leaf]) -> [u8; 32]`
- `nmt::open(leaves, range) -> Proof`
- `nmt::verify(proof, leaf_or_range, root) -> bool`

### Reed–Solomon
- `rs::encode(data: &mut [Shard], k: usize, m: usize)`
- `rs::reconstruct(shards: &mut [Option<Shard>], k: usize, m: usize) -> Result<()>`
- `rs::verify(shards, k, m) -> bool`

> Types like `Shard` are layout-aware slices with alignment checks to enable SIMD loads/stores.

---

## Python Bindings (feature: `python`)

The Python package `animica_native` exposes:
- `hash.blake3(data: bytes) -> bytes`
- `hash.keccak256(data: bytes) -> bytes`
- `hash.sha256(data: bytes) -> bytes`
- `nmt.root(leaves: Sequence[bytes], namespaces: Sequence[bytes]) -> bytes`
- `nmt.verify(proof: bytes, leaf: bytes, ns: bytes, root: bytes) -> bool`
- `rs.encode(data_shards: List[bytes], parity_shards: int) -> List[bytes]`
- `rs.reconstruct(shards: List[Optional[bytes]]) -> List[bytes]`

Bindings use **zero-copy** conversions when receiving `bytes`/`bytearray`/`memoryview`, but only expose **owned** `bytes` on return to keep lifetimes simple and safe.

---

## Feature Flags & Runtime Selection

| Cargo Feature | Purpose                                                  | Runtime check(s)                            |
|---------------|----------------------------------------------------------|---------------------------------------------|
| `simd`        | Enable SIMD-optimized kernels and helpers                | x86_64: AVX2, SHA-NI; aarch64: NEON/SHA3    |
| `rayon`       | Parallel NMT/tree hashing & RS via Rayon                 | Thread count via `RAYON_NUM_THREADS`        |
| `isal`        | Bind to Intel ISA-L for RS encode/reconstruct (Linux)    | Dynamic link presence + AVX/SSSE3           |
| `c_keccak`    | Use tuned C Keccak-f1600 permutation                     | Always available when compiled              |
| `python`      | Build PyO3 bindings and `_animica_native` module         | N/A                                         |

**Flow:** `utils::cpu` probes at initialization => `hash`, `nmt`, `rs` pick best implementation for the current machine. Fast paths are always **optional**; absence falls back to portable code.

---

## Data Flow & Execution Patterns

### Hashing

[bytes input] –> [chunking (if BLAKE3)] –> [SIMD kernels or portable] –> [digest 32B]

- BLAKE3 uses a tree mode that scales with threads when `rayon` is enabled.
- Keccak uses the tuned C permutation when `c_keccak` is enabled.

### NMT (root build)

[encoded leaves] –(level-by-level reduce)–> [namespace-aware combine] –> [root]
│                                            ▲
└─ encode.rs (len-prefix + ns)               │
hashers.rs (min/max ns, left/right)

- Iterative bottom-up build to reduce recursion and improve cache locality.
- `parallel.rs` batches levels across threads (bounded by DRAM bandwidth).

### RS (encode/reconstruct)

[data shards] –(GF ops via codec or ISA-L)–> [parity shards]
[erased shards?] –(syndrome & solve)–> [reconstructed shards]

- Shard layout chosen to fit cache lines and SIMD lanes.
- ISA-L backend replaces core GF ops when available.

---

## Safety Invariants

### General
- **No UB** in safe APIs: all exported functions validate lengths, shapes, and alignment before touching buffers.
- **Bounds checks** for every slice/index; dangerous paths are confined to small, reviewed `unsafe` blocks.
- **No panics** across FFI: internal panics are caught and mapped to `NativeError` → Python `RuntimeError`.

### Hashing
- Accepts any byte length (including zero).
- Streaming contexts finalize exactly once; re-use after finalize is rejected.
- Domain separation (DS) tags are fixed per usage (e.g., NMT internal nodes) to avoid cross-context collisions.

### NMT
- **Leaf encoding** is canonical: `len || namespace || data`. Any deviation invalidates proofs.
- **Left/right combiner** encodes min/max namespaces with the hash to ensure correct range semantics:
  - `ns_min(parent) = min(ns_min(left), ns_min(right))`
  - `ns_max(parent) = max(ns_max(left), ns_max(right))`
- **Range proofs** require the proven interval to be **contiguous** in namespace order.
- **Determinism:** Given identical leaves and namespace ordering, the root is stable across platforms/feature sets.
- **Structural checks:** Trees must be full-size at each level; odd nodes are re-combined deterministically (documented in `tree.rs`).

### Reed–Solomon
- **Parameters:** `k > 0`, `m ≥ 1`, total shards = `k + m`, shard size > 0 and consistent.
- **Alignment:** Shard buffers must be contiguous and equally sized; alignment helpers reject misaligned inputs when needed by SIMD paths.
- **Reconstruction:** Fails with a clear error if erasures exceed parity or the system is underdetermined.
- **Verification:** Parity/syndrome checks ensure shard integrity prior to return.

### Python FFI
- **Lifetimes:** Inputs are borrowed as readonly views; outputs are newly allocated `bytes`.
- **Exceptions:** All errors surface as `RuntimeError` with structured messages.
- **Threading:** GIL is released around long-running parallel operations (Rayon) to avoid blocking.

---

## Error Handling

- Internal errors use `NativeError` with categories:
  - `InvalidInput` (length/shape/param)
  - `BackendUnavailable` (e.g., ISA-L not found)
  - `ComputationFailed` (e.g., RS reconstruction impossible)
- Mapped to idiomatic `Result<T, NativeError>` in Rust; to `RuntimeError` in Python.

---

## Testing, Fuzzing, and Benchmarks

- **Rust tests:** `tests/hash_tests.rs`, `tests/nmt_tests.rs`, `tests/rs_tests.rs`
- **Python parity:** `tests_py/test_hash.py`, `tests_py/test_nmt.py`, `tests_py/test_rs.py` (compares against pure-Python references)
- **Benches:** `benches/hash_bench.rs`, `benches/nmt_bench.rs`, `benches/rs_bench.rs`
- **Fuzz (repo-wide):** Wire formats and proof envelopes are fuzzed separately to harden decoders.

---

## Concurrency & Parallelism

- `rayon_pool` initializes a global pool (idempotent).
- Long operations (NMT levels, RS block ops) are chunked to minimize contention.
- **Deterministic outputs** are guaranteed regardless of thread counts; only performance changes.

---

## Portability & Backends

- **x86_64:** AVX2, SHA-NI probed at runtime; ISA-L optional.
- **aarch64:** NEON and SHA3 probed; no external RS library dependency required.
- **OS support:** Linux/macOS/Windows for core features; ISA-L path is Linux-only.

---

## Security Considerations

- **Timing:** Crypto functions rely on upstream libraries (BLAKE3, SHA2, Keccak). While they reduce data-dependent branches, the crate does **not** claim full constant-time behavior at system scale (due to caches, threading).
- **Untrusted input:** All verifiers (NMT proofs, RS shard sets) treat inputs as hostile; malformed inputs return errors rather than panics.
- **Memory zeroization:** Large temporary buffers are dropped normally; explicit zeroization is not guaranteed unless noted (consider for future hardening).

---

## Extensibility Notes

- New hash algorithms plug in via `HashFn` trait and DS tagging in `hash::mod`.
- NMT variant strategies (e.g., different namespace widths) can be introduced behind feature flags and type wrappers in `types.rs`.
- Additional RS backends (e.g., GPU) can be gated similarly to `isal`.

---

## Quick Reference: Choosing a Build

- **Max portability:** `--features simd,rayon,c_keccak`
- **Linux servers (x86_64) with ISA-L:** `--features simd,rayon,c_keccak,isal`
- **Python wheels:** build via `maturin` with `--features python,simd,rayon,c_keccak[,isal]`

See `native/docs/PERFORMANCE.md` for empirical speedups and reproduction steps.

---
