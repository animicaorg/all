# Animica Native — Third-Party Notices

This document lists third-party software that may be included, linked, or used at build/run time by **animica_native** (the Rust crate that backs Animica’s high-performance primitives). It summarizes licenses and upstream sources for visibility and compliance. Exact versions are recorded in `Cargo.lock`; optional components are gated by crate features.

> **NOTE:** This file is an informational summary. For authoritative terms, consult each upstream project’s LICENSE file and website. Some projects offer multiple licenses (e.g., “Apache-2.0 OR MIT”); downstream usage is under one of those terms. Some projects also include patent grants or notices that apply as written in their licenses.

---

## How this file is maintained

- **Authoritative inventory:** `Cargo.lock` (Rust deps) and your OS/package manager (C toolchains).
- **Regenerate suggestions (Rust deps):**
  - With [`cargo-about`](https://github.com/EmbarkStudios/cargo-about):
    ```bash
    cargo install cargo-about
    cargo about generate > native/LICENSE-THIRD-PARTY.generated.md
    ```
  - With [`cargo-license`](https://github.com/onur/cargo-license):
    ```bash
    cargo install cargo-license
    cargo license --json > native/cargo-licenses.json
    ```
- **C/ASM libs** (when features are enabled) are vendored/submoduled or linked from the system. Keep their LICENSE files alongside the source or provide pointers below.

---

## Components (overview)

| Component | Purpose (where used) | Feature Gate | License (SPDX) | Upstream |
|---|---|---:|---|---|
| **BLAKE3** (`blake3` Rust crate) | Fast hash / Merkle-ish mixing | `simd` (perf), default enabled | `Apache-2.0 OR MIT` | https://github.com/BLAKE3-team/BLAKE3 |
| **Rayon** (`rayon`) | Parallel iterators for multicore | `rayon` | `Apache-2.0 OR MIT` | https://github.com/rayon-rs/rayon |
| **Intel ISA-L** (`isa-l`) | Reed-Solomon / erasure coding accel | `isal` | `BSD-3-Clause` | https://github.com/intel/isa-l |
| **XKCP (Keccak Code Package)** | C SHA-3/Keccak reference (optional) | `c_keccak` | `CC0-1.0` | https://github.com/XKCP/XKCP |
| **RustCrypto `sha3`** | Pure-Rust SHA-3 fallback | (no special gate) | `Apache-2.0 OR MIT` | https://github.com/RustCrypto/hashes |
| **cpufeatures** | Runtime CPU feature detection | (transitive) | `Apache-2.0 OR MIT` | https://github.com/RustCrypto/utils |
| **cfg-if** | Conditional compilation helper | (transitive) | `Apache-2.0 OR MIT` | https://github.com/alexcrichton/cfg-if |
| **once_cell** | Lazy/static init utilities | (transitive) | `Apache-2.0 OR MIT` | https://github.com/matklad/once_cell |
| **crossbeam** | Lock-free/concurrency utils | (possible transitive) | `Apache-2.0 OR MIT` | https://github.com/crossbeam-rs/crossbeam |
| **thiserror** | Error derivations | (dev/runtime) | `Apache-2.0 OR MIT` | https://github.com/dtolnay/thiserror |
| **cc** | Build C/C++ code from `build.rs` | (build-time) | `Apache-2.0 OR MIT` | https://github.com/alexcrichton/cc-rs |

> The exact dependency graph may vary across platforms, feature sets (`simd`, `rayon`, `isal`, `c_keccak`, `python`), and compiler versions. Use the regeneration steps above to produce a precise, machine-readable report for your build.

---

## Notices (selected upstreams)

### BLAKE3 (Rust)
- **Crate:** `blake3` — official Rust implementation.
- **License:** `Apache-2.0 OR MIT`
- **Source:** https://github.com/BLAKE3-team/BLAKE3  
- **Notes:** SIMD acceleration is auto-selected at build/run time when supported by the target CPU. If your distribution policies require a single license, you may treat dual-licensed crates as being under **either** MIT **or** Apache-2.0 (choose one).

### Intel ISA-L
- **Project:** Intelligent Storage Acceleration Library
- **License:** `BSD-3-Clause`
- **Source:** https://github.com/intel/isa-l  
- **Notes:** Used for high-throughput Reed-Solomon and related primitives when `feature="isal"` is enabled. The upstream repository ships a `LICENSE` file containing the BSD-3-Clause terms. Ensure the license file is included if you redistribute the library in binary form.

### XKCP — Keccak Code Package (optional)
- **Project:** Keccak/SHA-3 reference and optimized code
- **License:** `CC0-1.0`
- **Source:** https://github.com/XKCP/XKCP  
- **Notes:** Linked when `feature="c_keccak"` is enabled for testing/compat. CC0 dedicates the work to the public domain to the extent possible, with a fallback permissive license.

### RustCrypto `sha3`
- **Crate:** `sha3`
- **License:** `Apache-2.0 OR MIT`
- **Source:** https://github.com/RustCrypto/hashes  
- **Notes:** Pure-Rust fallback/portable path when no C backend is selected.

### Rayon
- **Crate:** `rayon`
- **License:** `Apache-2.0 OR MIT`
- **Source:** https://github.com/rayon-rs/rayon  
- **Notes:** Enabled via `feature="rayon"` to parallelize compute-heavy operations.

---

## Practical guidance

- **Binary redistribution:** Include this file, plus each upstream license file when your distribution bundles static artifacts or shared objects from these projects.
- **Feature audit:** For a given release artifact, record the enabled feature set:  
  `FEATURES="<comma-separated>"` and `TARGET="<triple>"`. This makes the dependency/license set deterministic.
- **System-provided libraries:** If you **dynamically** link to distro packages (e.g., `libisal.so`), your binary still depends on the upstream license; verify your distro’s packaging carries the same license text.
- **Patents:** Some licenses include explicit patent grants (e.g., Apache-2.0). Where relevant, downstream use is subject to those terms.

---

## Attribution summary (by license family)

- **Apache-2.0 OR MIT (dual):** BLAKE3 (Rust), Rayon, RustCrypto `sha3`, `cpufeatures`, `cfg-if`, `once_cell`, `crossbeam`, `thiserror`, `cc`.
- **BSD-3-Clause:** Intel ISA-L.
- **CC0-1.0:** XKCP (Keccak).

For any components not listed here but present in your `Cargo.lock` or vendor tree, consult their respective repositories and include their notices in your distribution package.

---

_Last updated: YYYY-MM-DD. Regenerate before each release to capture exact versions and any licensing changes._
