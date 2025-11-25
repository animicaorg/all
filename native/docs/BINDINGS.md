# Python Bindings (`animica_native`)

This document covers Python usage, supported wheel/OS/CPU matrix, manylinux notes, and tips for building from source. The Python package name published to PyPI follows the standard dash style:

- **Install:** `pip install animica-native`
- **Import:** `import animica_native`

The bindings are implemented with [PyO3] and expose a small, safe, high-performance surface over the native Rust library.

> TL;DR  
> - One-shot hashing (BLAKE3/Keccak/SHA-256)  
> - Namespace Merkle Tree (NMT) root and proof verification  
> - Reed–Solomon (RS) encode + reconstruct  
> - Zero-copy inputs where possible; deterministic outputs; GIL released around heavy work

---

## Quickstart

```python
import animica_native as an

# Hashing
digest = an.hash.blake3(b"hello world")         # 32-byte digest
print(digest.hex())

# NMT: compute a root from (namespace, leaf) pairs
namespaces = [b"\x00\x00\x00\x00\x00\x00\x00\x01", b"\x00\x00\x00\x00\x00\x00\x00\x02"]
leaves     = [b"alpha",                           b"beta"]
root = an.nmt.root(leaves=leaves, namespaces=namespaces)
print(root.hex())

# RS: encode 4 data shards with 2 parity, drop one, and reconstruct
data = [b"A"*1024, b"B"*1024, b"C"*1024, b"D"*1024]
parity = an.rs.encode(data_shards=data, parity_shards=2)  # returns [p0, p1]

# Simulate losses: lose shard 1 and parity 0
shards = [data[0], None, data[2], data[3], None, parity[1]]
recovered = an.rs.reconstruct(shards)  # returns fully reconstructed [d0,d1,d2,d3,p0,p1]
assert recovered[1] == b"B"*1024

API Overview
	•	an.hash.blake3(data: bytes|bytearray|memoryview) -> bytes
	•	an.hash.keccak256(data: bytes|bytearray|memoryview) -> bytes
	•	an.hash.sha256(data: bytes|bytearray|memoryview) -> bytes
	•	an.nmt.root(leaves: Sequence[bytes], namespaces: Sequence[bytes]) -> bytes
	•	Namespaces: fixed-width 8 bytes (big-endian). Length mismatch or wrong width raises RuntimeError.
	•	Ordering matters for range semantics; provide leaves in ascending namespace order.
	•	an.nmt.verify(proof: bytes, leaf: bytes, namespace: bytes, root: bytes) -> bool
	•	Verifies a single-leaf inclusion or a contiguous namespace range depending on the proof encoding.
	•	Returns False for malformed or mismatched proofs (never throws on adversarial data).
	•	an.rs.encode(data_shards: List[bytes], parity_shards: int) -> List[bytes]
	•	All data shards must be equal length and non-empty.
	•	an.rs.reconstruct(shards: List[Optional[bytes]]) -> List[bytes]
	•	Accepts a list of length k + m with None for erased shards.
	•	Errors if erasures exceed parity or shard shapes differ.

Performance hints
	•	Passing a memoryview(mmap_obj) avoids extra allocations for large inputs.
	•	Long-running, parallel sections release the GIL; you can run other Python tasks/threads concurrently.

⸻

Wheel / Platform Matrix

Prebuilt wheels are published for the most common targets.

OS	Arch	Wheel Tag	Python	Notes
Linux (glibc)	x86_64	manylinux2014_x86_64	3.9–3.12	AVX2/SHA-NI auto-detected at runtime; ISA-L optional
Linux (glibc)	aarch64	manylinux2014_aarch64	3.9–3.12	NEON/SHA3 probed at runtime
Linux (musl)	x86_64	musllinux_1_2_x86_64	3.9–3.12	For Alpine; no glibc required
macOS 11+	universal2	macosx_11_0_universal2	3.9–3.12	Single wheel for Apple Silicon + Intel
Windows	x86_64	win_amd64	3.9–3.12	MSVC toolchain; SIMD auto-selection

Notes
	•	Wheels are built with feature set: python,simd,rayon,c_keccak by default.
	•	On Linux x86_64, a second wheel variant may be provided with ISA-L enabled when feasible. If the dynamic loader cannot satisfy ISA-L at runtime, the code automatically falls back to the pure-Rust RS path.
	•	If you’re on a newer distro with recent glibc, you may also see manylinux_2_28 wheels. Use these when manylinux2014 is too old for your toolchain constraints.

⸻

Manylinux & musllinux Details

manylinux2014 vs manylinux_2_28
	•	manylinux2014 maximizes compatibility with older glibc baselines.
	•	manylinux_2_28 offers newer toolchains and can yield slightly faster codegen on modern servers. Choose based on your deployment fleet.

musllinux (Alpine)
	•	Use musllinux_1_2_* wheels on Alpine Linux to avoid glibc shims.
	•	If the index installs a manylinux wheel on Alpine by mistake, pin:
pip install --only-binary=:all: --platform musllinux_1_2_x86_64 animica-native

GPU/ISA Dependencies
	•	The crate does not depend on GPU libraries.
	•	ISA-L (if present) is dynamically loaded on Linux/x86_64; absence triggers a safe fallback.

⸻

Zero-Copy & Memory Semantics
	•	Inputs accept bytes, bytearray, or any object exposing the buffer protocol (memoryview, mmap).
	•	Outputs are returned as owned bytes to keep lifetimes clear and avoid dangling views across the FFI boundary.
	•	Internally, the library may use aligned reads and SIMD kernels; unaligned buffers are copied into aligned scratch only when strictly necessary.

⸻

Concurrency
	•	Heavy operations (e.g., NMT level reductions, RS encoding on large matrices) release the GIL.
	•	Parallelism is controlled by the native runtime (Rayon). You can tune threads via:
	•	Environment variable: RAYON_NUM_THREADS=<int>
	•	Defaults to a sensible value based on logical cores.
	•	Outputs are deterministic regardless of thread count.

⸻

Examples

1) Hash a large file without copies

import mmap, os, animica_native as an

with open("dataset.bin", "rb") as f:
    mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    try:
        d_blake3 = an.hash.blake3(memoryview(mm))
        d_sha256 = an.hash.sha256(memoryview(mm))
        print(d_blake3.hex(), d_sha256.hex())
    finally:
        mm.close()

2) NMT root for a stream of (ns, chunk) pairs

import animica_native as an

def to_ns(i: int) -> bytes:
    return i.to_bytes(8, "big")

chunks = [b"shard-000", b"shard-001", b"shard-002", b"shard-003"]
names  = [to_ns(i) for i in range(len(chunks))]

root = an.nmt.root(chunks, names)
print("nmt_root:", root.hex())

3) RS encode and reconstruct

import animica_native as an

k, m, size = 6, 3, 4096
data = [bytes([i])*size for i in range(k)]
parity = an.rs.encode(data, parity_shards=m)   # returns m parity shards

# Lose two data shards and one parity
shards = [data[0], None, data[2], None, data[4], data[5], parity[0], None, parity[2]]
recovered = an.rs.reconstruct(shards)


⸻

Error Handling & Diagnostics

All exceptions are surfaced as RuntimeError with a clear message. Common cases:
	•	InvalidInput: mismatched shard sizes; NMT namespace width not 8; empty arrays.
	•	BackendUnavailable: ISA-L requested by the wheel but unavailable at runtime (auto-fallback happens; you’ll rarely see this).
	•	ComputationFailed: RS reconstruction with too many erasures; corrupted proof data.

When debugging performance, verify your Python actually loads the accelerated wheel (not a source build with reduced features). The following sanity check can help:

import animica_native as an, platform
print("Platform:", platform.platform())
print("Hasher sample:", an.hash.blake3(b"").hex())


⸻

Building From Source

Prereqs
	•	Rust toolchain (stable), maturin>=1.5
	•	A C compiler (for the optimized Keccak path on all platforms; and for Windows MSVC)
	•	Python 3.9–3.12 headers (python3-dev on many distros)

Local dev (editable install)

pip install maturin
# Build with optimized defaults:
maturin develop --features "python,simd,rayon,c_keccak"
# Optionally enable ISA-L where supported:
maturin develop --features "python,simd,rayon,c_keccak,isal"

Build wheels

# Build a wheel for your current platform:
maturin build --release --features "python,simd,rayon,c_keccak"

# Manylinux (x86_64) inside official container:
docker run --rm -v $PWD:/io quay.io/pypa/manylinux2014_x86_64 \
  bash -lc "pipx install maturin && cd /io && maturin build --release \
            --features 'python,simd,rayon,c_keccak,isal' --manylinux 2014"

musllinux (Alpine)

Use quay.io/pypa/musllinux_1_2_x86_64 to produce musl wheels:

docker run --rm -v $PWD:/io quay.io/pypa/musllinux_1_2_x86_64 \
  sh -lc "pipx install maturin && cd /io && maturin build --release \
          --features 'python,simd,rayon,c_keccak'"

Tip: If you need to pass extra Cargo flags, you can use MATURIN_EXTRA_ARGS='-Zbuild-std' or set RUSTFLAGS, but this is typically unnecessary.

⸻

Versioning & Compatibility
	•	Semantic Versioning: Minor versions may add APIs; patch versions fix bugs/perf with no breaking changes.
	•	ABI: Wheels are built per-Python (abi3 is not used, due to feature gating and SIMD specializations).
	•	Determinism: For identical inputs, outputs are stable across platforms and feature sets.

⸻

FAQ

Q: Why a 32-byte digest?
A: All provided hash functions return 256-bit outputs for simplicity and common usage. BLAKE3’s XOF can yield longer digests in native Rust, but Python exposes the common 32-byte fixed length.

Q: Can I stream hashing?
A: The Python API currently exposes one-shot hashing. For multi-GB files, use mmap + memoryview as shown above to avoid extra copies.

Q: Does RS handle arbitrary shard sizes?
A: Yes, shards must be equal length and non-empty. The encoder chooses a layout optimized for SIMD; misaligned sources may incur an internal copy for alignment.

Q: How do I limit CPU usage?
A: Set RAYON_NUM_THREADS to cap the pool, e.g. RAYON_NUM_THREADS=4 python your_script.py.

Q: ImportError on Alpine?
A: Install the musllinux wheel variant (see above), or build from source in the musl container.

⸻

Security Notes
	•	The bindings accept untrusted inputs and are hardened to reject malformed data without panicking.
	•	The crypto primitives rely on respected upstream implementations; however, full system-wide constant-time guarantees are not claimed (common caveat for high-level runtimes).

⸻

Support
	•	File issues with OS/arch, Python version, wheel tag, and a minimal repro.
	•	Include pip debug --verbose output and platform.platform() to speed up triage.

