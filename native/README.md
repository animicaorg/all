# Animica `native/` — high-performance kernels & FFI surfaces

This directory houses the **native, performance-critical** building blocks that power Animica’s node, DA stack, consensus helpers, and SDKs. The code here implements tight inner loops (hashing, erasure coding, proof verification, codecs, crypto handshakes) in memory-safe systems languages and exposes them via small, ergonomic FFI layers to Python, TypeScript/Node, and other runtimes.

Native code is used sparingly and intentionally: when the cost of an operation is proportional to *bytes processed* or *proofs verified*, we move it here and provide a stable ABI.

---

## Contents & layout

native/
├─ rust/                      # Primary implementation language (Rust)
│  ├─ crates/
│  │  ├─ animica-hash/        # BLAKE3, Keccak-256, Poseidon2 (opt-in)
│  │  ├─ animica-nmt/         # Namespaced Merkle Trees (roots & proofs)
│  │  ├─ animica-rs/          # Reed–Solomon erasure coding
│  │  ├─ animica-vdf/         # VDF verification (Wesolowski)
│  │  ├─ animica-pq/          # PQ crypto wrappers (Kyber/Dilithium) via PQClean
│  │  ├─ animica-codec/       # Wire codecs (length-delimited, varint, CBOR)
│  │  └─ animica-miner/       # CPU Θ hash loop (vectorized)
│  └─ Cargo.toml
├─ c/                         # Tiny C shims for a C ABI (dlopen friendliness)
├─ include/                   # C headers for consumers of the shared library
├─ bindings/
│  ├─ python/                 # maturin-based Python wheels (animica_native)
│  ├─ node/                   # napi-rs Node addon (@animica/native)
│  └─ go/                     # cgo stubs (optional)
├─ benches/                   # Microbenchmarks (criterion)
├─ fuzz/                      # cargo-fuzz targets for critical parsers
├─ scripts/                   # Build helpers (cross, wheels, signing)
└─ README.md                  # You are here

> Note: Some subcrates start life as thin wrappers around well-audited libraries (e.g. BLAKE3, reed-solomon-erasure, pqclean). We wrap, specialize, add SIMD dispatching, then stabilize an ABI.

---

## Feature overview

- **Hashing**
  - BLAKE3 with runtime CPU feature detection (SSE2/AVX2/AVX-512 on x86; NEON on ARM).
  - Keccak-256 / SHA3-256 for consensus compatibility.
  - Optional Poseidon2 sponge for zk-friendly hashing (off by default, gated via feature flag).

- **Erasure coding (DA)**
  - Fast Reed–Solomon encode/decode (GF(2^8)) with shard layouts aligned to our DA layer.
  - Streaming interfaces to operate on chunked blobs without extra copies.

- **Namespaced Merkle Trees (NMT)**
  - Root computation over (namespace, leaf) pairs with canonical lexicographic namespace ordering.
  - Proof generation/verification, subtree folding, and batch verification helpers.

- **Post-quantum cryptography**
  - **Kyber KEM** for P2P session bootstrap (handshake encapsulation/decapsulation).
  - **Dilithium** (optional) for permit-style signatures. Implementations sourced through **PQClean** with constant-time bindings and `zeroize` on secrets.

- **VDF verification**
  - Wesolowski verifier over RSA groups (1024/2048 bit moduli) with big-int backends.
  - Deterministic interfaces fit the randomness beacon.

- **Wire codecs**
  - Zero-copy length-delimited frames, varint, and compact CBOR helpers tuned for `msgspec` interop.

- **Miner Θ hash loop (CPU)**
  - A portable baseline kernel with vectorized inner loops and tunable batch sizes,
    used for dev/mining and benchmarking (production miners may swap implementations).

- **FFI surfaces**
  - C ABI dynamic library: `libanimica_native.{so,dylib,dll}` with careful ownership/length contracts.
  - Python wheels (`animica_native`) via maturin.
  - Node addon (`@animica/native`) via napi-rs.

---

## Build matrix

We continuously build and test on the following targets:

| OS / Toolchain                         | Target triple                     | Notes                                  |
|---------------------------------------|-----------------------------------|----------------------------------------|
| Linux x86_64 (glibc)                  | `x86_64-unknown-linux-gnu`        | AVX2 autodetect, preferred for CI      |
| Linux aarch64 (glibc)                 | `aarch64-unknown-linux-gnu`       | NEON                                   |
| Linux x86_64 (musl, static-ish)       | `x86_64-unknown-linux-musl`       | for static containers                   |
| Linux aarch64 (musl, static-ish)      | `aarch64-unknown-linux-musl`      |                                        |
| macOS arm64 (Apple Silicon)           | `aarch64-apple-darwin`            | Homebrew LLVM for reproducible builds  |
| macOS x86_64                          | `x86_64-apple-darwin`             |                                        |
| Windows x86_64 (MSVC)                 | `x86_64-pc-windows-msvc`          | Requires VS Build Tools                 |

**Minimum versions**
- Rust `1.75+`
- Python `3.9–3.12` (wheels)
- Node `18+` (napi)
- CMake `3.20+` (if building C extras)
- Go `1.21+` (optional bindings)

---

## Building

### Rust libraries (all crates)

```bash
cd native/rust
cargo build --release --all-features
cargo test  --all-features

Shared library with C ABI

# Produces libanimica_native.{so,dylib,dll} into target/(triple)/release
cargo build -p animica-native-ffi --release
ls target/*/release/libanimica_native.*

Headers live in native/include/. All exported symbols are prefixed with ami_.

Python wheels

# Requires: pipx install maturin  (or pip install maturin)
cd native/bindings/python
maturin build --release -i python3.10
ls target/wheels/

Install locally:

pip install target/wheels/animica_native-*.whl
python -c "import animica_native as an; print(an.hash.blake3(b'hello'))"

Node addon

cd native/bindings/node
# Uses napi-rs & cargo; produces prebuilds when configured in CI
npm install
npm run build
node -e "const n=require('./index.node'); console.log(n.blake3('hello'))"

Cross compilation (Linux → musl)

rustup target add x86_64-unknown-linux-musl
sudo apt-get install musl-tools
RUSTFLAGS='-C target-feature=+avx2' cargo build --release --target x86_64-unknown-linux-musl


⸻

FFI surface (quick reference)

C ABI (header excerpt)

// include/animica_native.h
typedef struct { const uint8_t* ptr; size_t len; } ami_bytes;
typedef struct { uint8_t* ptr; size_t len; } ami_mut_bytes;
typedef struct { int32_t code; const char* msg; } ami_status;

ami_status ami_blake3_hash(ami_bytes input, ami_mut_bytes out32);
ami_status ami_keccak256(ami_bytes input, ami_mut_bytes out32);

ami_status ami_rs_encode(
  const uint8_t* shards, size_t shard_len,
  uint32_t data_shards, uint32_t parity_shards,
  uint8_t* out   /* length = shard_len * parity_shards */
);

void ami_free(void* p); // only free pointers returned by Animica

Python

import animica_native as an
an.hash.blake3(b"abc")          # -> 32-byte digest
an.nmt.root(leaves, namespaces) # -> (root, meta)
an.rs.encode(shards=10, parity=2, chunk=b"...")
an.vdf.verify(input_b, y, pi, params)
an.pq.kyber.seal(pk, b"secret"), an.pq.kyber.open(sk, ct)

Node

import n from '@animica/native'
n.blake3(Buffer.from('abc'))
await n.nmtRoot(leaves, namespaces)


⸻

Performance claims & baselines

Performance is tracked and asserted in CI via the tests/bench/* suite, with canonical baselines in tests/bench/baselines.json. Numbers vary by CPU, flags, and input sizes; we aim to meet or exceed the following reference points:

Kernel (typical payload)	Apple M2 Pro (aarch64)	EPYC 7xx3 (x86_64, AVX2)
BLAKE3 hash throughput	≥ 2.0 GB/s	≥ 3.0 GB/s
Keccak-256 throughput	≥ 600 MB/s	≥ 900 MB/s
RS encode (10+2 shards, 256 KiB)	≥ 800 MB/s	≥ 1.2 GB/s
NMT root (64K leaves, 32B)	≥ 3.0 M leaves/s	≥ 5.0 M leaves/s
VDF verify (RSA-1024, per proof)	≤ 80 µs	≤ 60 µs
Θ miner inner hash loop (dev)	≥ 150 MH/s	≥ 220 MH/s

Source of truth: run python tests/bench/runner.py which records metrics and compares to baselines with guardrails defined in tests/GATES.md. If your change improves performance, update baselines following tests/PERF_METHODS.md.

⸻

Safety notes

Memory & FFI
	•	Rust is the default; unsafe is tightly scoped and audited.
	•	All FFI accepts explicit (ptr, len) pairs; bounds checked before use.
	•	No ownership transfer unless documented; ami_free only frees our allocations.
	•	#[deny(unsafe_op_in_unsafe_fn)], clippy::pedantic in CI.

Crypto hygiene
	•	Secrets are wiped (zeroize) on drop; constant-time operations where applicable.
	•	PQ algorithms come from PQClean reference/optimized impls; we do not invent crypto.
	•	CPU feature dispatch avoids divergent side channels (no data-dependent AVX on secrets).

Determinism
	•	Feature gating avoids non-deterministic sources; SIMD selection is runtime but result-stable.
	•	Endianness is normalized; cross-platform tests run in CI (Linux/macOS/Windows).

DoS resistance
	•	Lengths and shard counts are hard-limited; parsers are fuzzed (cargo-fuzz + atheris).
	•	NMT proof depth and RS parameters validated before allocation.

Licensing & provenance
	•	Third-party crates and C code are recorded in LICENSE-THIRD-PARTY.md and reviewed for compatible licenses.
	•	Some PQ artifacts may be marked “research-grade”—see crate README flags.

⸻

Development workflow
	1.	Edit / add crate under native/rust/crates/*.
	2.	cargo fmt && cargo clippy --all-features -- -D warnings
	3.	cargo test --all-features
	4.	Add/extend criterion benches in benches/, run cargo bench.
	5.	Integrate bindings:
	•	Python: extend bindings/python module, regenerate wheels with maturin build.
	•	Node: extend napi module and npm run build.
	6.	Run repo benches: python tests/bench/runner.py and inspect diffs.
	7.	If acceptable, refresh baselines per tests/PERF_METHODS.md.
	8.	Submit PR with notes on CPU, flags, and benchmark deltas.

⸻

Troubleshooting
	•	Linker errors on macOS (Apple Silicon)
Ensure you’re using Homebrew LLVM (brew install llvm) and export:

export CC=$(brew --prefix llvm)/bin/clang
export CXX=$(brew --prefix llvm)/bin/clang++


	•	Windows MSVC toolchain missing
Install “Desktop development with C++” workload; then:

rustup default stable-x86_64-pc-windows-msvc


	•	Musl cross for static containers
Install musl-tools and set RUSTFLAGS='-C target-feature=+avx2' if your fleet supports it.

⸻

FAQ

Why Rust instead of C/C++?
Memory safety by default, great cryptographic ecosystem, and high-quality tooling (cargo, fuzz, bench). We still expose a C ABI for maximal interoperability.

Why not GPU kernels?
GPU acceleration is orthogonal and may land as optional miners/providers. Core consensus and verification remain CPU-friendly by design.

Can I rely on these ABIs forever?
We follow SemVer. Breaking ABI changes bump the major version and are documented in the crate and binding changelogs.

⸻

License

Dual-licensed under Apache-2.0 and MIT. See LICENSE and LICENSE-THIRD-PARTY.md.

For security reports, see SECURITY.md at the repository root.
