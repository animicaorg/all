from __future__ import annotations

"""
Fused CUDA Keccak solo-PoW scanner (8.3.0).

Why a *fused* kernel
--------------------
``mining/gpu_torch_sha3.py`` hashes the real solo pre-image on the GPU with a
batched Keccak built from PyTorch int64 element-wise ops.  It is correct and
device-portable, but each Keccak step (theta/rho/pi/chi/iota) is a *separate*
CUDA launch over a [B, 25] tensor, so the whole search is **memory-bound**: the
5-lane state is streamed to and from VRAM 24*5 times per block, and launch
overhead dominates.  That is why a big card can sit at ~150 W / <50 °C — it is
starved, not saturated.

This module compiles a single hand-written CUDA C kernel where **one thread
hashes one nonce entirely in registers**: the 25-lane Keccak state never leaves
the register file, the 24 rounds are a tight in-thread loop, the big-endian
target compare happens on-thread, and only a single ``atomicMin`` writes back the
smallest winning nonce.  That is compute-dense (ALU-bound) the way an efficient
miner must be, and is the shape an FPGA/ASIC path would also take.

Consensus safety
----------------
Identical to the torch path: this kernel is only a *fast pre-filter*.  Every
nonce it returns is re-hashed on the host with ``hashlib`` against
``Header(nonce).hash()`` and the real target before it is ever returned, so a
miscompiled or buggy kernel can never surface an invalid block — at worst it
finds nothing and the caller falls back to :func:`gpu_torch_sha3.scan_solo`.

Verifiable without a GPU
------------------------
The kernel's integer algorithm is mirrored exactly by the pure-Python
``_ref_*`` functions below (same nonce-splice math, same tiny_sha3 Keccak-f, same
byte-swapped 256-bit compare).  ``python mining/gpu_cuda_sha3.py`` validates that
mirror bit-for-bit against ``hashlib`` and against ``Header.hash()`` on CPU, so
the algorithm is proven correct with no CUDA device present.  At runtime, the
first time the compiled kernel is used it additionally runs an on-device
self-test (plant an easy target, confirm the kernel finds the known nonce and it
host-verifies) before it is trusted for real mining.
"""

import hashlib
import os
from dataclasses import replace
from typing import Optional, Tuple

# Reuse the byte-exact preimage/layout helpers from the torch path so the two
# GPU backends can never diverge on header serialization or the nonce band.
try:  # package import (mining.gpu_cuda_sha3)
    from .gpu_torch_sha3 import (
        NONCE_BAND_HI,
        NONCE_BAND_LO,
        _RC,
        _pad101,
        derive_prefix_suffix,
    )
except ImportError:  # run directly as a script (python mining/gpu_cuda_sha3.py)
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from gpu_torch_sha3 import (  # type: ignore
        NONCE_BAND_HI,
        NONCE_BAND_LO,
        _RC,
        _pad101,
        derive_prefix_suffix,
    )

_MASK64 = (1 << 64) - 1
_SENTINEL = (1 << 64) - 1  # "no winning nonce found"

# --------------------------------------------------------------------------- #
# CUDA C source                                                               #
# --------------------------------------------------------------------------- #
# tiny_sha3 (Saarinen) Keccak-f[1600] round constants / rotation offsets / pi
# lane permutation. keccakf_rndc == mining.gpu_torch_sha3._RC bit-for-bit; we
# emit them from Python below so there is a single source of truth.
_RC_HEX = ", ".join(f"0x{(v & _MASK64):016x}ULL" for v in _RC)

_SRC = r"""
extern "C" {

typedef unsigned long long u64;

__device__ __constant__ u64 RC[24] = { __RC__ };
__device__ __constant__ int ROTC[24] = {
    1,  3,  6,  10, 15, 21, 28, 36, 45, 55, 2,  14,
    27, 41, 56, 8,  25, 43, 62, 18, 39, 61, 20, 44
};
__device__ __constant__ int PILN[24] = {
    10, 7,  11, 17, 18, 3, 5,  16, 8,  21, 24, 4,
    15, 23, 19, 13, 12, 2, 20, 14, 22, 9,  6,  1
};

__device__ __forceinline__ u64 rotl64(u64 x, int r) {
    return (x << r) | (x >> (64 - r));
}

__device__ __forceinline__ u64 bswap64(u64 x) {
    x = ((x & 0x00FF00FF00FF00FFULL) << 8)  | ((x >> 8)  & 0x00FF00FF00FF00FFULL);
    x = ((x & 0x0000FFFF0000FFFFULL) << 16) | ((x >> 16) & 0x0000FFFF0000FFFFULL);
    return (x << 32) | (x >> 32);
}

// Keccak-f[1600] over 25 lanes held in registers (tiny_sha3 structure).
__device__ __forceinline__ void keccakf(u64 st[25]) {
    u64 t, bc[5];
    #pragma unroll 1
    for (int r = 0; r < 24; r++) {
        // Theta
        #pragma unroll
        for (int i = 0; i < 5; i++)
            bc[i] = st[i] ^ st[i + 5] ^ st[i + 10] ^ st[i + 15] ^ st[i + 20];
        #pragma unroll
        for (int i = 0; i < 5; i++) {
            t = bc[(i + 4) % 5] ^ rotl64(bc[(i + 1) % 5], 1);
            #pragma unroll
            for (int j = 0; j < 25; j += 5)
                st[j + i] ^= t;
        }
        // Rho + Pi
        t = st[1];
        #pragma unroll
        for (int i = 0; i < 24; i++) {
            int j = PILN[i];
            bc[0] = st[j];
            st[j] = rotl64(t, ROTC[i]);
            t = bc[0];
        }
        // Chi
        #pragma unroll
        for (int j = 0; j < 25; j += 5) {
            #pragma unroll
            for (int i = 0; i < 5; i++) bc[i] = st[j + i];
            #pragma unroll
            for (int i = 0; i < 5; i++)
                st[j + i] ^= (~bc[(i + 1) % 5]) & bc[(i + 2) % 5];
        }
        // Iota
        st[0] ^= RC[r];
    }
}

/*
 * One thread == one nonce.
 *   tmpl        : padded message as `total_lanes` little-endian u64 lanes,
 *                 with the 4 nonce bytes zeroed (patched per-thread here).
 *   nonce_off   : byte offset of the 4-byte big-endian nonce in the message.
 *   start_nonce : first nonce of this launch; thread i handles start_nonce+i.
 *   count       : number of nonces in this launch.
 *   tgt         : 4 big-endian u64 words of the 256-bit target (MS word first).
 *   band_hi     : exclusive nonce-band ceiling (NONCE_BAND_HI).
 *   found       : out; atomicMin'd to the smallest winning nonce, else sentinel.
 */
__global__ void scan(const u64* __restrict__ tmpl,
                     int total_lanes,
                     int nblk,
                     int nonce_off,
                     u64 start_nonce,
                     u64 count,
                     const u64* __restrict__ tgt,
                     u64 band_hi,
                     u64* found) {
    u64 idx = (u64)blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= count) return;
    u64 nonce = start_nonce + idx;
    if (nonce >= band_hi) return;

    // Splice the nonce: big-endian 4 bytes at `nonce_off` become a little-endian
    // 32-bit value V = bswap32(nonce) laid at bit 8*(nonce_off%8) of lane
    // (nonce_off/8), spilling into the next lane when it straddles a boundary.
    unsigned int n32 = (unsigned int)nonce;
    unsigned int v = ((n32 & 0x000000FFu) << 24) |
                     ((n32 & 0x0000FF00u) << 8)  |
                     ((n32 & 0x00FF0000u) >> 8)  |
                     ((n32 & 0xFF000000u) >> 24);
    u64 vn = (u64)v;
    int q = nonce_off & 7;
    int laneA = nonce_off >> 3;
    int laneB = laneA + 1;
    u64 contribA = (q == 0) ? vn : (vn << (8 * q));
    u64 contribB = (q == 0) ? 0ULL : (vn >> (64 - 8 * q));  // 0 when q<=4 (vn<2^32)

    u64 st[25];
    #pragma unroll
    for (int i = 0; i < 25; i++) st[i] = 0ULL;

    for (int b = 0; b < nblk; b++) {
        int base = b * 17;
        #pragma unroll
        for (int i = 0; i < 17; i++) {
            int g = base + i;
            u64 lane = tmpl[g];
            if (g == laneA) lane |= contribA;
            if (g == laneB) lane |= contribB;
            st[i] ^= lane;
        }
        keccakf(st);
    }

    // 256-bit big-endian compare: H = bswap64(state lanes 0..3), MS word first.
    u64 H0 = bswap64(st[0]);
    u64 H1 = bswap64(st[1]);
    u64 H2 = bswap64(st[2]);
    u64 H3 = bswap64(st[3]);
    u64 T0 = tgt[0], T1 = tgt[1], T2 = tgt[2], T3 = tgt[3];

    bool win = true;               // all-equal => digest == target => (<=) wins
    if      (H0 != T0) win = (H0 < T0);
    else if (H1 != T1) win = (H1 < T1);
    else if (H2 != T2) win = (H2 < T2);
    else if (H3 != T3) win = (H3 < T3);

    if (win) atomicMin(found, nonce);
}

}  // extern "C"
""".replace("__RC__", _RC_HEX)


# --------------------------------------------------------------------------- #
# CuPy compilation / availability                                             #
# --------------------------------------------------------------------------- #
_MODULE = None
_KERNEL = None
_DEVICE_OK: Optional[bool] = None  # None=untested, True/False=cached self-test result


def cuda_kernel_available() -> bool:
    """True if CuPy is importable and at least one CUDA device is present.

    Does *not* compile the kernel or run the device self-test — that happens
    lazily on first real use so import stays cheap and side-effect-free.
    """
    if os.getenv("ANIMICA_MINER_CUDA_FUSED", "").strip() in ("0", "false", "no"):
        return False
    try:
        import cupy  # noqa: F401
        return int(cupy.cuda.runtime.getDeviceCount()) > 0
    except Exception:
        return False


def _get_kernel():
    """Compile (once) and return the ``scan`` RawKernel, or raise."""
    global _MODULE, _KERNEL
    if _KERNEL is not None:
        return _KERNEL
    import cupy
    _MODULE = cupy.RawModule(code=_SRC, options=("--std=c++11",))
    _KERNEL = _MODULE.get_function("scan")
    return _KERNEL


# --------------------------------------------------------------------------- #
# Host driver                                                                 #
# --------------------------------------------------------------------------- #
def _template_and_target(header, target_int: int):
    """Build the device-ready inputs shared across a whole scan:
    (template lanes bytes, total_lanes, nblk, nonce_off, target words)."""
    import numpy as np

    prefix, suffix, off = derive_prefix_suffix(header)
    template = _pad101(prefix + b"\x00\x00\x00\x00" + suffix)
    assert len(template) % 136 == 0
    total_lanes = len(template) // 8
    nblk = len(template) // 136
    tmpl = np.frombuffer(template, dtype="<u8").copy()  # little-endian lanes
    tb = int(target_int).to_bytes(32, "big")
    tgt = np.array(
        [int.from_bytes(tb[8 * i:8 * i + 8], "big") for i in range(4)],
        dtype=np.uint64,
    )
    return tmpl, total_lanes, nblk, off, tgt


def scan_solo_cuda(
    header,
    target_int: int,
    *,
    start_nonce: int,
    iterations: int,
    device: str = "cuda",
    batch_size: int = 0,
    stats: Optional[dict] = None,
) -> Tuple[Optional[int], Optional[bytes]]:
    """Fused-CUDA drop-in for :func:`gpu_torch_sha3.scan_solo` with the same
    signature and the same host re-verification guarantee.

    Returns (nonce, digest) for the smallest winning nonce in
    ``[start_nonce, start_nonce+iterations)`` (clamped to the band), or
    (None, None).  Raises on a compile/launch failure so the caller can fall
    back to the torch scanner; a returned nonce is always node-acceptable.
    """
    global _DEVICE_OK
    import cupy
    import numpy as np

    fn = _get_kernel()

    # One-time on-device self-test: prove the compiled kernel finds a known
    # planted solution on THIS GPU before we trust it for real search.
    if _DEVICE_OK is None:
        _DEVICE_OK = _device_selftest(header)
    if not _DEVICE_OK:
        raise RuntimeError("fused CUDA kernel failed on-device self-test")

    tmpl, total_lanes, nblk, off, tgt = _template_and_target(header, target_int)
    tmpl_dev = cupy.asarray(tmpl)
    tgt_dev = cupy.asarray(tgt)
    found_dev = cupy.empty((1,), dtype=cupy.uint64)

    lo = max(int(start_nonce), NONCE_BAND_LO)
    hi = min(int(start_nonce) + int(iterations), NONCE_BAND_HI)

    # Chunk the window so each launch is bounded (and grid dims stay legal).
    chunk = int(os.getenv("ANIMICA_MINER_GPU_BATCH", "0") or 0)
    if chunk <= 0:
        chunk = 1 << 24  # 16.7M nonces/launch
    if batch_size and batch_size > 0:
        chunk = int(batch_size)
    block = 256
    scanned = 0
    n = lo
    while n < hi:
        c = min(chunk, hi - n)
        found_dev.fill(np.uint64(_SENTINEL))
        grid = (c + block - 1) // block
        fn(
            (grid,), (block,),
            (
                tmpl_dev,
                np.int32(total_lanes),
                np.int32(nblk),
                np.int32(off),
                np.uint64(n),
                np.uint64(c),
                tgt_dev,
                np.uint64(NONCE_BAND_HI),
                found_dev,
            ),
        )
        cupy.cuda.runtime.deviceSynchronize()
        scanned += c
        v = int(found_dev.get()[0])
        if v != _SENTINEL:
            # Host re-verification against the canonical node digest.
            digest = replace(header, nonce=v).hash()
            if int.from_bytes(digest, "big") <= int(target_int):
                if stats is not None:
                    stats["hashes"] = stats.get("hashes", 0) + scanned
                return v, digest
            # Kernel/host disagreement (must not happen): keep scanning safely.
        n += c

    if stats is not None:
        stats["hashes"] = stats.get("hashes", 0) + scanned
    return None, None


def _device_selftest(header) -> bool:
    """Run the compiled kernel against a planted easy target on the real device
    and confirm it finds the known nonce (and it host-verifies). Cached."""
    try:
        n0 = NONCE_BAND_LO + 12345
        planted = int.from_bytes(replace(header, nonce=n0).hash(), "big")
        # Use a tiny explicit window/batch so this is a couple of microseconds.
        nonce, digest = _scan_once(
            header, planted, lo=NONCE_BAND_LO, hi=NONCE_BAND_LO + 40000
        )
        if nonce is None or digest is None:
            return False
        if nonce != n0:
            # A *smaller* nonce may also satisfy the (easy) planted target; accept
            # any nonce that genuinely host-verifies at or below it.
            if int.from_bytes(digest, "big") > planted:
                return False
        return digest == replace(header, nonce=nonce).hash()
    except Exception:
        return False


def _scan_once(header, target_int: int, *, lo: int, hi: int):
    """Single-window kernel launch used by the device self-test (bypasses the
    self-test guard to avoid recursion)."""
    import cupy
    import numpy as np

    fn = _get_kernel()
    tmpl, total_lanes, nblk, off, tgt = _template_and_target(header, target_int)
    tmpl_dev = cupy.asarray(tmpl)
    tgt_dev = cupy.asarray(tgt)
    found_dev = cupy.empty((1,), dtype=cupy.uint64)
    found_dev.fill(np.uint64(_SENTINEL))
    c = hi - lo
    block = 256
    grid = (c + block - 1) // block
    fn(
        (grid,), (block,),
        (
            tmpl_dev, np.int32(total_lanes), np.int32(nblk), np.int32(off),
            np.uint64(lo), np.uint64(c), tgt_dev, np.uint64(NONCE_BAND_HI),
            found_dev,
        ),
    )
    cupy.cuda.runtime.deviceSynchronize()
    v = int(found_dev.get()[0])
    if v == _SENTINEL:
        return None, None
    digest = replace(header, nonce=v).hash()
    if int.from_bytes(digest, "big") <= int(target_int):
        return v, digest
    return None, None


# --------------------------------------------------------------------------- #
# Pure-Python reference mirror (validates the kernel algorithm with no GPU)    #
# --------------------------------------------------------------------------- #
_ROTC = [1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14,
         27, 41, 56, 8, 25, 43, 62, 18, 39, 61, 20, 44]
_PILN = [10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4,
         15, 23, 19, 13, 12, 2, 20, 14, 22, 9, 6, 1]


def _rotl64(x: int, r: int) -> int:
    x &= _MASK64
    return ((x << r) | (x >> (64 - r))) & _MASK64


def _keccakf_ref(st: list) -> list:
    """tiny_sha3 Keccak-f[1600] mirror of the CUDA ``keccakf`` (exact op order)."""
    for r in range(24):
        bc = [st[i] ^ st[i + 5] ^ st[i + 10] ^ st[i + 15] ^ st[i + 20] for i in range(5)]
        for i in range(5):
            t = bc[(i + 4) % 5] ^ _rotl64(bc[(i + 1) % 5], 1)
            for j in range(0, 25, 5):
                st[j + i] ^= t
        t = st[1]
        for i in range(24):
            j = _PILN[i]
            b0 = st[j]
            st[j] = _rotl64(t, _ROTC[i])
            t = b0
        for j in range(0, 25, 5):
            row = [st[j + i] for i in range(5)]
            for i in range(5):
                st[j + i] = row[i] ^ ((~row[(i + 1) % 5] & _MASK64) & row[(i + 2) % 5])
        st[0] ^= (_RC[r] & _MASK64)
        for i in range(25):
            st[i] &= _MASK64
    return st


def _sha3_256_ref(msg: bytes) -> bytes:
    """SHA3-256 via the same lane packing / squeeze the kernel uses."""
    padded = _pad101(msg)
    st = [0] * 25
    for off in range(0, len(padded), 136):
        block = padded[off:off + 136]
        for i in range(17):
            st[i] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        st = _keccakf_ref(st)
    out = b"".join(int(st[i] & _MASK64).to_bytes(8, "little") for i in range(4))
    return out


def _bswap32(x: int) -> int:
    return int.from_bytes((x & 0xFFFFFFFF).to_bytes(4, "little"), "big")


def _bswap64(x: int) -> int:
    return int.from_bytes((x & _MASK64).to_bytes(8, "little"), "big")


def _ref_scan_smallest(header, target_int: int, *, lo: int, hi: int) -> Optional[int]:
    """Exact CPU mirror of the CUDA ``scan`` kernel: same nonce splice, same
    Keccak, same byte-swapped 256-bit compare. Returns the smallest winning
    nonce in ``[lo, hi)`` or None."""
    prefix, suffix, off = derive_prefix_suffix(header)
    template = _pad101(prefix + b"\x00\x00\x00\x00" + suffix)
    total_lanes = len(template) // 8
    nblk = len(template) // 136
    tmpl = [int.from_bytes(template[8 * i:8 * i + 8], "little") for i in range(total_lanes)]
    tb = int(target_int).to_bytes(32, "big")
    T = [int.from_bytes(tb[8 * i:8 * i + 8], "big") for i in range(4)]
    q = off & 7
    laneA = off >> 3
    laneB = laneA + 1
    lo = max(int(lo), NONCE_BAND_LO)
    hi = min(int(hi), NONCE_BAND_HI)
    for nonce in range(lo, hi):
        v = _bswap32(nonce)
        contribA = v if q == 0 else ((v << (8 * q)) & _MASK64)
        contribB = 0 if q == 0 else ((v >> (64 - 8 * q)) & _MASK64)
        st = [0] * 25
        for b in range(nblk):
            base = b * 17
            for i in range(17):
                g = base + i
                lane = tmpl[g]
                if g == laneA:
                    lane |= contribA
                if g == laneB:
                    lane |= contribB
                st[i] ^= lane
            st = _keccakf_ref(st)
        H = [_bswap64(st[i]) for i in range(4)]
        win = True
        for i in range(4):
            if H[i] != T[i]:
                win = H[i] < T[i]
                break
        if win:
            return nonce
    return None


# --------------------------------------------------------------------------- #
# Self-test (no GPU required)                                                  #
# --------------------------------------------------------------------------- #
def _selftest() -> None:  # pragma: no cover - invoked via __main__
    import secrets
    import sys
    sys.path.insert(0, "/root/animica")
    from core.types.header import Header

    # 1) Reference Keccak == hashlib for random-length messages.
    n_ok = 0
    for _ in range(300):
        L = secrets.randbelow(600) + 1
        m = secrets.token_bytes(L)
        assert _sha3_256_ref(m) == hashlib.sha3_256(m).digest(), L
        n_ok += 1
    print(f"[1] ref keccak vs hashlib: {n_ok} random messages OK")

    def mkhdr():
        tok = secrets.token_bytes
        return Header(
            v=1, chainId=1, height=987654, parentHash=tok(32),
            timestamp=1_700_123_456, stateRoot=tok(32), txsRoot=tok(32),
            receiptsRoot=tok(32), proofsRoot=tok(32), daRoot=tok(32),
            mixSeed=tok(32), poiesPolicyRoot=tok(32), pqAlgPolicyRoot=tok(32),
            thetaMicro=16_000_000, workType=0, nonce=0, extra=b"cuda-selftest",
        )

    # 2) Spliced digest (kernel mirror) == Header(nonce).hash() for many nonces.
    #    _sha3_256_ref pads internally, so splice into the RAW (unpadded) message.
    hdr = mkhdr()
    prefix, suffix, off = derive_prefix_suffix(hdr)
    raw = bytearray(prefix + b"\x00\x00\x00\x00" + suffix)
    n_ok = 0
    for _ in range(400):
        nn = secrets.randbelow(NONCE_BAND_HI - NONCE_BAND_LO) + NONCE_BAND_LO
        m = bytearray(raw)
        m[off:off + 4] = nn.to_bytes(4, "big")
        assert _sha3_256_ref(bytes(m)) == replace(hdr, nonce=nn).hash(), nn
        n_ok += 1
    print(f"[2] kernel-mirror splice == Header.hash(): {n_ok} nonces OK")

    # 3) The kernel-mirror scan finds a planted solution and it host-verifies,
    #    exactly matching the compare the CUDA kernel performs.
    for trial in range(5):
        hdr = mkhdr()
        n0 = NONCE_BAND_LO + secrets.randbelow(30000)
        planted = int.from_bytes(replace(hdr, nonce=n0).hash(), "big")
        got = _ref_scan_smallest(hdr, planted, lo=NONCE_BAND_LO, hi=NONCE_BAND_LO + 40000)
        assert got is not None, "mirror scan failed to find planted solution"
        assert int.from_bytes(replace(hdr, nonce=got).hash(), "big") <= planted, got
        # The mirror returns the *smallest* winner; it must be <= the planted one.
        assert got <= n0, (got, n0)
    print("[3] kernel-mirror scan finds planted solution (smallest, host-verified) OK")

    # 4) Cross-check the mirror's compare against a brute-force hashlib scan on a
    #    small window: same smallest-winning nonce for a moderate target.
    hdr = mkhdr()
    window_lo, window_hi = NONCE_BAND_LO, NONCE_BAND_LO + 6000
    # Pick a target that ~1 in a few hundred nonces beats, so the window has hits.
    tgt = (1 << 256) // 300
    mirror = _ref_scan_smallest(hdr, tgt, lo=window_lo, hi=window_hi)
    brute = None
    for nn in range(window_lo, window_hi):
        if int.from_bytes(replace(hdr, nonce=nn).hash(), "big") <= tgt:
            brute = nn
            break
    assert mirror == brute, (mirror, brute)
    print(f"[4] mirror vs brute-force hashlib smallest-winner match: nonce={mirror} OK")

    print("ALL CUDA-MIRROR SELFTESTS PASSED (kernel algorithm proven on CPU)")


if __name__ == "__main__":
    _selftest()
