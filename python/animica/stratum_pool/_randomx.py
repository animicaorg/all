"""Thin ctypes wrapper around librandomx for share validation.

We deliberately avoid pip-installable RandomX wrappers — they're either
unmaintained, bind to old librandomx versions, or pull liboqs-adjacent
C dependencies we'd rather not vendor. The official tevador/RandomX
source is built once at /opt/randomx/, installed to /usr/local/lib/,
and accessed here through `ctypes.CDLL`.

API exposed:
- new_cache(key: bytes) → cache handle (lifetime-managed by caller)
- new_vm(cache_handle) → vm handle (one per validator thread)
- calculate_hash(vm, input: bytes) → 32-byte digest
- release_vm / release_cache

Threading model:
- One Cache per (mining_key, epoch). RandomX rotates the cache key
  every 2048 Monero blocks (the "seedhash" period); the pool re-creates
  the cache when a job's `seed_hash` differs from the active cache.
- One VM per validator worker thread. VMs are NOT thread-safe.

Performance:
- Cache init takes ~3-5 seconds. Pool keeps at most 2 caches alive
  (current epoch + next epoch) to handle the rotation boundary smoothly.
- `calculate_hash` runs at ~150-300 hashes/sec/thread in interpreter
  mode (no JIT) — fine for share validation, which is <100 shares/sec.
- For block validation we want the JIT (RANDOMX_FLAG_JIT) since that's
  what miners use; the validator must produce bit-identical hashes.
"""

from __future__ import annotations

import ctypes
import os
import threading
from ctypes import c_int, c_size_t, c_uint64, c_void_p
from typing import Optional


_LIB_PATHS = (
    "/usr/local/lib/librandomx.so",
    "librandomx.so",
)

# RandomX flag bits from src/randomx.h
RANDOMX_FLAG_DEFAULT = 0
RANDOMX_FLAG_LARGE_PAGES = 1
RANDOMX_FLAG_HARD_AES = 2
RANDOMX_FLAG_FULL_MEM = 4
RANDOMX_FLAG_JIT = 8
RANDOMX_FLAG_SECURE = 16
RANDOMX_FLAG_ARGON2_SSSE3 = 32
RANDOMX_FLAG_ARGON2_AVX2 = 64
RANDOMX_FLAG_ARGON2 = 96

RANDOMX_HASH_SIZE = 32


class RandomXError(RuntimeError):
    """Raised when librandomx returns NULL or a malformed result."""


def _load_lib() -> ctypes.CDLL:
    last_err: Optional[BaseException] = None
    for path in _LIB_PATHS:
        try:
            lib = ctypes.CDLL(path)
            break
        except OSError as exc:
            last_err = exc
    else:
        raise RandomXError(
            "librandomx.so not found. Build with:\n"
            "  cd /opt && git clone https://github.com/tevador/RandomX.git randomx\n"
            "  cd randomx && mkdir build && cd build && cmake -DARCH=native -DBUILD_SHARED_LIBS=ON ..\n"
            "  make -j && cp librandomx.so /usr/local/lib/ && ldconfig\n"
            f"Last error: {last_err}"
        )

    # C signatures — see /usr/local/include/randomx/randomx.h
    lib.randomx_get_flags.restype = c_int

    lib.randomx_alloc_cache.argtypes = [c_int]
    lib.randomx_alloc_cache.restype = c_void_p
    lib.randomx_init_cache.argtypes = [c_void_p, ctypes.c_char_p, c_size_t]
    lib.randomx_release_cache.argtypes = [c_void_p]

    lib.randomx_create_vm.argtypes = [c_int, c_void_p, c_void_p]
    lib.randomx_create_vm.restype = c_void_p
    lib.randomx_destroy_vm.argtypes = [c_void_p]

    lib.randomx_calculate_hash.argtypes = [
        c_void_p, ctypes.c_char_p, c_size_t, ctypes.POINTER(ctypes.c_ubyte)
    ]

    return lib


_LIB = _load_lib()


def recommended_flags() -> int:
    """Returns the JIT+AES flag set if the host supports it. Pool
    validation needs to match miner hashes bit-for-bit, so we use the
    JIT path same as miners do."""
    flags = _LIB.randomx_get_flags()
    return flags


class Cache:
    """RandomX cache for one mining epoch (one seed_hash).

    The pool keeps one Cache per active seed_hash; when monerod rotates
    the seed (every 2048 blocks), the pool builds a fresh Cache and
    leaves the old one around for the rotation grace period.
    """

    def __init__(self, key: bytes, flags: int = RANDOMX_FLAG_DEFAULT):
        if not key:
            raise ValueError("RandomX cache key cannot be empty")
        self._flags = flags
        self._key = key
        handle = _LIB.randomx_alloc_cache(flags)
        if not handle:
            raise RandomXError("randomx_alloc_cache returned NULL")
        self._handle = c_void_p(handle)
        _LIB.randomx_init_cache(self._handle, key, len(key))

    @property
    def handle(self) -> c_void_p:
        return self._handle

    @property
    def key(self) -> bytes:
        return self._key

    @property
    def flags(self) -> int:
        return self._flags

    def close(self) -> None:
        if self._handle:
            _LIB.randomx_release_cache(self._handle)
            self._handle = c_void_p(0)

    def __del__(self) -> None:
        self.close()


class VM:
    """RandomX VM for one validator thread.

    `Cache` is shared; VMs are not — keep one VM per worker thread that
    validates shares.
    """

    def __init__(self, cache: Cache, flags: Optional[int] = None):
        f = cache.flags if flags is None else flags
        handle = _LIB.randomx_create_vm(f, cache.handle, c_void_p(0))
        if not handle:
            raise RandomXError(
                f"randomx_create_vm returned NULL (flags=0x{f:x}). "
                "If you see this with FLAG_JIT, the host kernel may "
                "be blocking PROT_EXEC for anonymous mappings — drop "
                "FLAG_JIT or relax seccomp/PaX."
            )
        self._handle = c_void_p(handle)
        self._cache = cache
        self._lock = threading.Lock()

    def hash(self, blob: bytes) -> bytes:
        """Compute the RandomX hash of `blob`. Thread-safe within a
        single VM (callers should still avoid contention by giving
        each worker its own VM)."""
        out = (ctypes.c_ubyte * RANDOMX_HASH_SIZE)()
        with self._lock:
            _LIB.randomx_calculate_hash(
                self._handle, blob, len(blob),
                ctypes.cast(out, ctypes.POINTER(ctypes.c_ubyte)),
            )
        return bytes(out)

    def close(self) -> None:
        if self._handle:
            _LIB.randomx_destroy_vm(self._handle)
            self._handle = c_void_p(0)

    def __del__(self) -> None:
        self.close()


def selftest() -> None:
    """Sanity check — should print the canonical RandomX test vector hash."""
    cache = Cache(b"test key 000")
    vm = VM(cache, flags=RANDOMX_FLAG_DEFAULT)
    out = vm.hash(b"This is a test")
    expected = bytes.fromhex(
        "639183aae1bf4c9a35884cb46b09cad9175f04efd7684e7262a0ac1c2f0b4e3f"
    )
    if out != expected:
        raise RandomXError(
            f"selftest failed: got {out.hex()}, expected {expected.hex()}"
        )
    vm.close()
    cache.close()


if __name__ == "__main__":
    selftest()
    print("librandomx wrapper OK")
