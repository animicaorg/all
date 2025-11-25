# Keccak-f[1600] C core

This directory contains a compact, FFI-friendly implementation of the Keccak-f[1600] permutation and a small streaming sponge used to expose Keccak/SHA-3 one-shot helpers (e.g. `sha3_256`, `keccak_256`) and XOF-style squeeze.

## Origin

- **Algorithm**: Keccak-f[1600] as specified by the Keccak Team and standardized in **FIPS 202** (SHA-3) and **SP 800-185** (cSHAKE/KMAC).  
- **Implementation**: The code here is an **original, clean-room implementation** written for Animica Native.  
  - Round constants and rotation offsets are those defined in the public specifications.  
  - The structure follows the usual round decomposition (θ, ρ∘π, χ, ι) with lanes held in registers and **24 fully unrolled rounds** for performance.  
  - The sponge “glue” (absorb/finalize/squeeze) follows the **multi-rate padding** rules and accepts a configurable domain-separator byte.

No third-party C source was copied into this directory. Design and constants are, of course, derived from the public Keccak/SHA-3 specifications.

## License

- **Source files**: `keccak1600.c`, `keccak1600.h`  
  Licensed under the **MIT License** (see the repository’s root LICENSE). This permissive license allows static/dynamic linking from Rust, Python, and C/C++ projects.

- **Standards & constants**: The Keccak specification, FIPS 202, rotation tables, and round constants are public and not copyrightable as used here.  
  An attribution note is included out of courtesy to the Keccak Team/NIST.

> For a consolidated view of upstream licenses used elsewhere in the native crate (e.g., BLAKE3, ISA-L), see `native/LICENSE-THIRD-PARTY.md`.

## Endianness

Keccak defines **little-endian** mapping inside each 64-bit lane. This implementation maintains the state as 25×`uint64_t` lanes and overlays the first `rate` bytes as a byte view for absorb/squeeze.

- **Supported, tested architectures**: **little-endian** platforms (e.g., `x86_64`, `aarch64`) — this is the typical case on Linux/macOS/Windows.  
- **Big-endian note**: Because we XOR input directly into the byte view of the lane array, the behavior on big-endian systems would not match Keccak’s little-endian lane mapping. If you need big-endian support, adapt the code to use explicit `load64_le`/`store64_le` helpers (byte-swaps on BE) instead of a raw byte overlay during absorb/squeeze.

If you are unsure of the target, assume little-endian; our CI and test vectors run on LE machines.

## Domain separation & rates

The sponge initializer takes a **rate** and a **domain-separator byte**:

- SHA-3 family:
  - `SHA3-224`: rate = 144 bytes, delim = `0x06`
  - `SHA3-256`: rate = 136 bytes, delim = `0x06`
  - `SHA3-384`: rate = 104 bytes, delim = `0x06`
  - `SHA3-512`: rate = 72 bytes,  delim = `0x06`
- Legacy “Keccak-256” (pre-FIPS variant): rate = 136 bytes, **delim = `0x01`**.
- SHAKE/cSHAKE/KMAC use different domain bytes and, for XOFs, you simply keep squeezing after finalize.

Convenience wrappers (`sha3_256`, `keccak_256`, etc.) are provided and set the correct `(rate, delim)` pairs for you.

## Quick C usage

```c
#include "keccak1600.h"

uint8_t out[32];
const uint8_t *msg = ...;
size_t msg_len = ...;

/* SHA3-256 one-shot */
sha3_256(msg, msg_len, out);

/* Or streaming sponge with explicit domain/rate */
keccak1600_ctx ctx;
keccak1600_init(&ctx, KECCAK_RATE_SHA3_256, KECCAK_DELIM_SHA3);
keccak1600_absorb(&ctx, msg, msg_len);
keccak1600_finalize(&ctx);
keccak1600_squeeze(&ctx, out, 32);

Testing
	•	Verified against NIST SHA-3 and common Keccak-256 test vectors.
	•	In this repository the Rust/Python bindings also exercise the C path to ensure parity with pure-Rust backends.

Performance notes
	•	24 fully unrolled rounds keep lanes in registers and minimize spills.
	•	Favor -O3 -fomit-frame-pointer (Clang/GCC) and LTO for best results.
	•	On modern CPUs, this C path is competitive with portable Rust and acts as a fast fallback when platform SHA-3 intrinsics are not available.

Files
	•	keccak1600.h — public API and constants (rates, domain bytes).
	•	keccak1600.c — permutation, streaming sponge, and one-shot helpers.

⸻

Acknowledgements: Keccak and SHA-3 are the work of Guido Bertoni, Joan Daemen, Michaël Peeters, and Gilles Van Assche; standardized by NIST as FIPS 202.
