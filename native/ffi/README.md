# Animica Native — FFI guide (non-Python consumers)

This document explains how to consume the **Animica Native** library from
languages and runtimes other than Python — e.g. **C/C++**, **Go (cgo)**,
**Node.js (ffi/napi)**, and **Java (JNA/JNI)**.

The public C ABI is intentionally small and stable enough for general use.
It exposes:

- **CPU feature detection** (AVX2/SHA-NI/NEON/SHA3)
- **Hashing**: BLAKE3, Keccak-256, SHA-256
- **Reed–Solomon (RS)** encode/reconstruct helpers
- **Namespaced Merkle Tree (NMT)**: root & proof verify
- A single **allocator boundary** via `anm_free` / `anm_rs_free`


---

## Layout & artifacts

- Public header (install this): `native/include/animica_native.h`  
  (mirrors `native/c/include/animica_native.h` in the sources)
- Dynamic/static libraries produced by Cargo in `native/target/{debug,release}`:
  - Linux: `libanimica_native.so`
  - macOS: `libanimica_native.dylib`
  - Windows (MSVC): `animica_native.dll` (+ import lib `animica_native.lib`)
  - Static (optional): `libanimica_native.a`

> Note: Python wheels bundle a Rust extension module; for **non-Python** use,
> build the library via Cargo as shown below.

---

## Build from source (recommended)

From the repository root:

```bash
cd native
# Fast, portable build (no extra backends)
cargo build --release

# Enable useful features:
#  - simd: optimized paths
#  - rayon: parallel NMT/RS where beneficial
#  - c_keccak: switch Keccak to the tuned C backend
cargo build --release --features "simd,rayon,c_keccak"

# (Linux, optional) ISA-L backend for RS (requires system isa-l dev headers)
cargo build --release --features "simd,rayon,isal"

Artifacts land in native/target/release/.

⸻

Version & ABI surface
	•	Version macros in the header:
	•	ANM_NATIVE_VERSION_MAJOR, ..._MINOR, ..._PATCH
	•	Runtime string: const char* anm_version_string(void);
	•	Status codes: anm_status (ANM_OK, ANM_ERR_INVALID_ARG, ANM_ERR_UNSUPPORTED, ANM_ERR_NOMEM, ANM_ERR_INTERNAL)

Thread-safety: All functions are pure/stateless unless noted. They can be
called concurrently. RS reconstruction mutates your shard array in-place by
filling missing entries (it will allocate and assign new buffers).

Memory ownership: Any buffer allocated by this library must be released via
anm_free() or anm_rs_free() as documented. Do not mix allocators.

⸻

Linking & runtime search paths

Linux (clang/gcc)

# Compile your C/C++ code
cc -I./native/include -c my_app.c -o my_app.o

# Link against the shared lib built by Cargo
cc my_app.o -L./native/target/release -lanimica_native -o my_app

# At runtime, locate the .so (choose one):
export LD_LIBRARY_PATH="$(pwd)/native/target/release:$LD_LIBRARY_PATH"
# or embed rpath at link time:
cc my_app.o -Wl,-rpath,'$ORIGIN/../native/target/release' \
   -L./native/target/release -lanimica_native -o my_app

macOS (clang)

cc -I./native/include -c my_app.c -o my_app.o
cc my_app.o -L./native/target/release -lanimica_native -o my_app
export DYLD_LIBRARY_PATH="$(pwd)/native/target/release:$DYLD_LIBRARY_PATH"
# or prefer @rpath:
cc my_app.o -Wl,-rpath,@executable_path/../native/target/release \
   -L./native/target/release -lanimica_native -o my_app

Windows (MSVC)

REM Build (PowerShell or Developer Command Prompt)
cd native
cargo build --release

REM Compile & link (adjust paths)
cl /I native\include my_app.c native\target\release\animica_native.lib
REM Ensure animica_native.dll is on PATH at runtime
set PATH=%CD%\native\target\release;%PATH%


⸻

C: minimal examples

Hashing (Keccak-256)

#include <stdio.h>
#include <string.h>
#include "animica_native.h"

int main(void) {
  const char *msg = "hello animica";
  uint8_t out[32];
  if (anm_keccak256((const uint8_t*)msg, strlen(msg), out) != ANM_OK) {
    fprintf(stderr, "keccak failed\n");
    return 1;
  }
  for (int i=0;i<32;i++) printf("%02x", out[i]);
  printf("\n");
  return 0;
}

CPU feature detection

#include <stdio.h>
#include "animica_native.h"

int main(void) {
  anm_cpu_flags f = {0};
  anm_cpu_detect(&f);
  printf("AVX2=%d SHA_NI=%d NEON=%d SHA3=%d\n",
         f.avx2, f.sha_ni, f.neon, f.sha3);
  return 0;
}

Reed–Solomon (encode + free)

#include <stdlib.h>
#include <string.h>
#include "animica_native.h"

int main(void) {
  const uint32_t k = 8, m = 4;
  const uint8_t data[] = "some payload bytes ...";
  uint8_t **shards = NULL;
  size_t shard_len = 0;

  anm_status s = anm_rs_encode_alloc(data, sizeof(data)-1, k, m, &shards, &shard_len);
  if (s != ANM_OK) return 1;

  /* simulate loss */
  free(shards[2]); shards[2] = NULL;
  free(shards[9]); shards[9] = NULL;

  /* reconstruct in-place */
  if (anm_rs_reconstruct(shards, k, m, shard_len) != ANM_OK) return 2;

  /* clean up */
  anm_rs_free(shards, k + m);
  return 0;
}

NMT (root)

#include <string.h>
#include "animica_native.h"

static const uint8_t NS[ANM_NMT_NS_LEN] = {0,0,0,0,0,0,0,1};

int main(void) {
  uint8_t root[32];
  const char *a = "leaf A";
  const char *b = "leaf B";

  anm_nmt_leaf leaves[2] = {
    { NS, (const uint8_t*)a, (size_t)strlen(a) },
    { NS, (const uint8_t*)b, (size_t)strlen(b) },
  };
  if (anm_nmt_root(leaves, 2, root) != ANM_OK) return 1;
  return 0;
}


⸻

Go (cgo) quickstart

Create a tiny CGO wrapper (animica.go):

package animica

/*
#cgo CFLAGS: -I${SRCDIR}/../native/include
#cgo LDFLAGS: -L${SRCDIR}/../native/target/release -lanimica_native
#include <stdlib.h>
#include <stdint.h>
#include "animica_native.h"
*/
import "C"
import "unsafe"

func Keccak256(msg []byte) ([32]byte, error) {
    var out [32]byte
    var rc = C.anm_keccak256((*C.uchar)(unsafe.Pointer(&msg[0])), C.size_t(len(msg)),
                             (*C.uchar)(unsafe.Pointer(&out[0])))
    if rc != C.ANM_OK {
        return out, errFrom(rc)
    }
    return out, nil
}

func errFrom(rc C.anm_status) error {
    switch rc {
    case C.ANM_ERR_INVALID_ARG: return fmt.Errorf("invalid argument")
    case C.ANM_ERR_UNSUPPORTED: return fmt.Errorf("unsupported")
    case C.ANM_ERR_NOMEM:       return fmt.Errorf("out of memory")
    default:                    return fmt.Errorf("internal error (%d)", int(rc))
    }
}

Build environment:

# Ensure the Rust library is built and discoverable
( cd native && cargo build --release --features "simd,c_keccak" )
export CGO_LDFLAGS="-L$(pwd)/native/target/release"
export CGO_CFLAGS="-I$(pwd)/native/include"
go build ./...

At runtime, set LD_LIBRARY_PATH/DYLD_LIBRARY_PATH/PATH as noted earlier.

⸻

Node.js options
	•	ffi-napi (simplest): call the C ABI directly
	•	N-API native addon: write a thin C++ binding layer (best perf/ergonomics)

Using ffi-napi (example)

// npm i ffi-napi ref-napi
import ffi from 'ffi-napi';
import ref from 'ref-napi';

const u8 = ref.types.uchar;
const u8ptr = ref.refType(u8);
const size_t = ref.types.size_t;
const status_t = ref.types.int;

const lib = ffi.Library('./native/target/release/libanimica_native', {
  'anm_keccak256': [ status_t, [ u8ptr, size_t, u8ptr ] ],
});

const msg = Buffer.from('hello animica');
const out = Buffer.alloc(32);
const rc = lib.anm_keccak256(msg, msg.length, out);
if (rc !== 0) throw new Error(`keccak failed ${rc}`);
console.log(out.toString('hex'));

On Windows, the library base name is animica_native (no lib prefix).

⸻

Java options

Prefer JNA (no C glue), or write explicit JNI.

JNA mapping (Keccak-256)

public interface AnimicaNative extends com.sun.jna.Library {
  AnimicaNative INSTANCE = com.sun.jna.Native.load("animica_native", AnimicaNative.class);
  int anm_keccak256(byte[] data, long len, byte[] out32);
}

// Usage:
byte[] out = new byte[32];
int rc = AnimicaNative.INSTANCE.anm_keccak256(msg, msg.length, out);
if (rc != 0) throw new RuntimeException("keccak failed rc=" + rc);

Configure jna.library.path or put the shared library on the system path.

⸻

Feature flags & performance
	•	simd — enables runtime-gated SIMD fast paths (BLAKE3, NMT layout)
	•	rayon — parallel execution for tree builds/RS operations on large inputs
	•	c_keccak — uses highly-tuned C permutation (often faster on x86/aarch64)
	•	isal (Linux) — Intel ISA-L Reed–Solomon backend for peak throughput

Features do not change the ABI surface. They only affect internal code paths.

⸻

Safety & correctness notes
	•	Allocator boundary: Free with anm_free/anm_rs_free only.
	•	Shard lengths: For RS, compute once via anm_rs_expected_shard_len.
	•	Constant-time: Hashing is constant-time wrt input; RS/NMT are not
side-channel hardened (not needed for most uses).
	•	Untrusted inputs: NMT proofs are validated; malformed data returns
ANM_ERR_INVALID_ARG or ANM_ERR_UNSUPPORTED.
	•	Threading: The library may create worker threads if built with rayon.

⸻

Troubleshooting
	•	undefined reference: anm_*
Ensure -L… -lanimica_native during link and the library path at runtime.
	•	dlopen: image not found / DLL not found
Check LD_LIBRARY_PATH (Linux), DYLD_LIBRARY_PATH (macOS), PATH (Windows).
	•	ABI mismatch after upgrade
Check anm_version_string() and rebuild your bindings.
	•	ISA-L not found
Either install isa-l dev pkg or build without --features isal.

⸻

License

Animica Native is MIT-licensed. Third-party licenses are listed under
native/LICENSE-THIRD-PARTY.md.

⸻

Appendix: exported C symbols (summary)

/* Version & status */
const char *anm_version_string(void);
void anm_free(void *ptr);

/* CPU flags */
typedef struct { uint8_t avx2, sha_ni, neon, sha3; uint8_t _rsvd[4]; } anm_cpu_flags;
void anm_cpu_detect(anm_cpu_flags *out);

/* Hashing */
anm_status anm_blake3(const uint8_t*, size_t, uint8_t[32]);
anm_status anm_keccak256(const uint8_t*, size_t, uint8_t[32]);
anm_status anm_sha256(const uint8_t*, size_t, uint8_t[32]);

/* RS */
size_t anm_rs_expected_shard_len(size_t data_len, uint32_t k);
anm_status anm_rs_encode_alloc(const uint8_t*, size_t, uint32_t, uint32_t, uint8_t***, size_t*);
anm_status anm_rs_reconstruct(uint8_t **shards, uint32_t k, uint32_t m, size_t shard_len);
void anm_rs_free(uint8_t **shards, uint32_t total);

/* NMT */
#define ANM_NMT_NS_LEN 8
typedef struct { const uint8_t *ns; const uint8_t *data; size_t data_len; } anm_nmt_leaf;
anm_status anm_nmt_root(const anm_nmt_leaf *leaves, size_t n, uint8_t out32[32]);
anm_status anm_nmt_verify(const uint8_t *proof, size_t proof_len,
                          const uint8_t ns[ANM_NMT_NS_LEN],
                          const uint8_t *data, size_t data_len,
                          const uint8_t root32[32]);

