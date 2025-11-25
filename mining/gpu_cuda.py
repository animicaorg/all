from __future__ import annotations

"""
Animica mining.gpu_cuda
=======================

Optional CUDA backend that scans nonces using a CUDA kernel implementing
single-block SHA3-256(header || mixSeed || nonce_le8) and the acceptance test:

    u = uniform_from_digest(digest)
    accept iff u <= exp(-Theta)

Notes
-----
- Guarded import: if PyCUDA or a CUDA device is unavailable, this backend raises
  DeviceUnavailable at creation (the miner falls back to CPU).
- Single-block constraint: kernel supports inputs up to 136 bytes (SHA3-256 rate).
  If header_len + 32 + 8 > 136, we transparently fall back to CPU for that scan.
- Determinism: results are stable; we return a list of dicts:
    {nonce: int, u: float, d_ratio: float, hash: bytes}
- Safety: any kernel error ⇒ CPU fallback for the call (not the whole process).

Requires: pycuda>=2022.2, a working NVIDIA driver & CUDA runtime.
"""

import math
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# ---------- Optional imports (guarded) ----------
try:
    import pycuda.driver as cuda  # type: ignore
    from pycuda.compiler import SourceModule  # type: ignore
except Exception:  # pragma: no cover
    cuda = None  # type: ignore

# ---------- Local errors & types ----------
try:
    from .errors import DeviceUnavailable  # type: ignore
except Exception:  # pragma: no cover
    class DeviceUnavailable(RuntimeError):
        pass

try:
    from .device import DeviceInfo, DeviceType  # type: ignore
except Exception:  # pragma: no cover
    @dataclass(frozen=True)
    class DeviceInfo:
        type: str
        name: str
        index: int = 0
        vendor: Optional[str] = None
        driver: Optional[str] = None
        compute_units: Optional[int] = None
        memory_bytes: Optional[int] = None
        max_batch: Optional[int] = None
        flags: dict = None  # type: ignore

    class DeviceType(str):
        GPU = "gpu"
        CPU = "cpu"

# Reuse canonical math if available (for CPU fallback equivalence)
try:
    from . import nonce_domain as nd  # type: ignore
    _HAS_ND = True
except Exception:  # pragma: no cover
    _HAS_ND = False

import hashlib  # CPU fallback only


def _nonce_le8(n: int) -> bytes:
    return struct.pack("<Q", n & 0xFFFFFFFFFFFFFFFF)


def _digest_bytes_cpu(header: bytes, mix: bytes, nonce: int) -> bytes:
    if _HAS_ND and hasattr(nd, "digest_header_mix_nonce"):
        return nd.digest_header_mix_nonce(header, mix, nonce)  # type: ignore
    h = hashlib.sha3_256()
    h.update(header)
    h.update(mix)
    h.update(_nonce_le8(nonce))
    return h.digest()


def _uniform_from_digest(d: bytes) -> float:
    if _HAS_ND and hasattr(nd, "uniform_from_digest"):
        return float(nd.uniform_from_digest(d))  # type: ignore
    # Use first 16 bytes as big-endian 128-bit integer; map to (0,1]
    hi = int.from_bytes(d[0:8], "big")
    lo = int.from_bytes(d[8:16], "big")
    u = (hi / 18446744073709551616.0) + ((lo + 1.0) / 340282366920938463463374607431768211456.0)
    return u


def _exp_neg_theta(theta_micro: float) -> float:
    if _HAS_ND and hasattr(nd, "exp_neg_theta"):
        return float(nd.exp_neg_theta(theta_micro))  # type: ignore
    return math.exp(-theta_micro / 1e6)


# ────────────────────────────────────────────────────────────────────────
# CUDA kernel (single-block SHA3-256 + acceptance check)
# ────────────────────────────────────────────────────────────────────────

CUDA_SOURCE = r"""
// Single-block SHA3-256 (rate=136) + acceptance test.
// Grid-stride loop over 'iterations' nonces starting from start_nonce.

extern "C" {

__device__ __forceinline__ unsigned long long rol64(unsigned long long x, unsigned int n) {
    return (x << n) | (x >> (64 - n));
}

__constant__ unsigned long long RC[24] = {
  0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808aULL, 0x8000000080008000ULL,
  0x000000000000808bULL, 0x0000000080000001ULL, 0x8000000080008081ULL, 0x8000000000008009ULL,
  0x000000000000008aULL, 0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000aULL,
  0x000000008000808bULL, 0x800000000000008bULL, 0x8000000000008089ULL, 0x8000000000008003ULL,
  0x8000000000008002ULL, 0x8000000000000080ULL, 0x000000000000800aULL, 0x800000008000000aULL,
  0x8000000080008081ULL, 0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL
};

__device__ __forceinline__ unsigned long long load64_le(const unsigned char* p) {
    return ((unsigned long long)p[0])       |
           ((unsigned long long)p[1] << 8 ) |
           ((unsigned long long)p[2] << 16) |
           ((unsigned long long)p[3] << 24) |
           ((unsigned long long)p[4] << 32) |
           ((unsigned long long)p[5] << 40) |
           ((unsigned long long)p[6] << 48) |
           ((unsigned long long)p[7] << 56);
}

__device__ __forceinline__ void store64_le(unsigned char* p, unsigned long long v) {
    p[0]=(unsigned char)(v);
    p[1]=(unsigned char)(v>>8);
    p[2]=(unsigned char)(v>>16);
    p[3]=(unsigned char)(v>>24);
    p[4]=(unsigned char)(v>>32);
    p[5]=(unsigned char)(v>>40);
    p[6]=(unsigned char)(v>>48);
    p[7]=(unsigned char)(v>>56);
}

__device__ void keccak_f1600(unsigned long long A[25]) {
    const unsigned int r[25] = {
        0,  1, 62, 28, 27,
       36, 44,  6, 55, 20,
        3, 10, 43, 25, 39,
       41, 45, 15, 21,  8,
       18,  2, 61, 56, 14
    };
    const unsigned int p[25] = {
        0,  6, 12, 18, 24,
        3,  9, 10, 16, 22,
        1,  7, 13, 19, 20,
        4,  5, 11, 17, 23,
        2,  8, 14, 15, 21
    };
    #pragma unroll
    for (int ir = 0; ir < 24; ir++) {
        unsigned long long C0 = A[0]^A[5]^A[10]^A[15]^A[20];
        unsigned long long C1 = A[1]^A[6]^A[11]^A[16]^A[21];
        unsigned long long C2 = A[2]^A[7]^A[12]^A[17]^A[22];
        unsigned long long C3 = A[3]^A[8]^A[13]^A[18]^A[23];
        unsigned long long C4 = A[4]^A[9]^A[14]^A[19]^A[24];
        unsigned long long D0 = rol64(C1,1) ^ C3;
        unsigned long long D1 = rol64(C2,1) ^ C4;
        unsigned long long D2 = rol64(C3,1) ^ C0;
        unsigned long long D3 = rol64(C4,1) ^ C1;
        unsigned long long D4 = rol64(C0,1) ^ C2;

        A[0]^=D0; A[5]^=D0; A[10]^=D0; A[15]^=D0; A[20]^=D0;
        A[1]^=D1; A[6]^=D1; A[11]^=D1; A[16]^=D1; A[21]^=D1;
        A[2]^=D2; A[7]^=D2; A[12]^=D2; A[17]^=D2; A[22]^=D2;
        A[3]^=D3; A[8]^=D3; A[13]^=D3; A[18]^=D3; A[23]^=D3;
        A[4]^=D4; A[9]^=D4; A[14]^=D4; A[19]^=D4; A[24]^=D4;

        unsigned long long B[25];
        #pragma unroll
        for (int i = 0; i < 25; i++) {
            B[p[i]] = rol64(A[i], r[i]);
        }
        #pragma unroll
        for (int y = 0; y < 5; y++) {
            int i = 5*y;
            unsigned long long b0=B[i+0], b1=B[i+1], b2=B[i+2], b3=B[i+3], b4=B[i+4];
            A[i+0] = b0 ^ ((~b1) & b2);
            A[i+1] = b1 ^ ((~b2) & b3);
            A[i+2] = b2 ^ ((~b3) & b4);
            A[i+3] = b3 ^ ((~b4) & b0);
            A[i+4] = b4 ^ ((~b0) & b1);
        }
        A[0] ^= RC[ir];
    }
}

__device__ void sha3_256_singleblock(const unsigned char* msg, unsigned int msg_len, unsigned char* out32) {
    // State
    unsigned long long A[25];
    #pragma unroll
    for (int i=0;i<25;i++) A[i]=0ULL;

    // Build padded block (rate=136)
    unsigned char blk[136];
    #pragma unroll
    for (int i=0;i<136;i++) blk[i]=0;
    for (unsigned int i=0;i<msg_len;i++) blk[i]=msg[i];
    blk[msg_len] ^= 0x06;  // pad10*1 start
    blk[135] ^= 0x80;      // pad end

    // Absorb
    #pragma unroll
    for (int i=0;i<17;i++) {
        A[i] ^= load64_le(blk + 8*i);
    }

    keccak_f1600(A);

    // Squeeze 32 bytes
    #pragma unroll
    for (int i=0;i<4;i++) {
        store64_le(out32 + 8*i, A[i]);
    }
}

__global__ void find_hashshares(
    const unsigned char* header,
    unsigned int header_len,
    const unsigned char* mix,          // 32 bytes
    unsigned long long start_nonce,
    double cutoff,                     // exp(-Theta)
    unsigned long long* out_nonces,    // capacity = max_found
    float* out_u,                      // capacity = max_found
    unsigned char* out_hashes,         // capacity = max_found * 32
    unsigned int* counter,             // atomic counter
    unsigned int max_found,
    unsigned int iterations
) {
    if (header_len + 32u + 8u > 136u) return; // single-block guard

    unsigned long long tid = blockIdx.x * (unsigned long long)blockDim.x + threadIdx.x;
    unsigned long long stride = gridDim.x * (unsigned long long)blockDim.x;

    for (unsigned long long i = tid; i < (unsigned long long)iterations; i += stride) {
        unsigned long long nonce = start_nonce + i;

        // Assemble message = header || mix || nonce_le8
        unsigned char msg[136];
        for (unsigned int j=0;j<header_len;j++) msg[j] = header[j];
        for (int j=0;j<32;j++) msg[header_len + j] = mix[j];
        unsigned long long n = nonce;
        msg[header_len+32+0] = (unsigned char)(n);
        msg[header_len+32+1] = (unsigned char)(n>>8);
        msg[header_len+32+2] = (unsigned char)(n>>16);
        msg[header_len+32+3] = (unsigned char)(n>>24);
        msg[header_len+32+4] = (unsigned char)(n>>32);
        msg[header_len+32+5] = (unsigned char)(n>>40);
        msg[header_len+32+6] = (unsigned char)(n>>48);
        msg[header_len+32+7] = (unsigned char)(n>>56);

        unsigned char dig[32];
        sha3_256_singleblock(msg, header_len + 32u + 8u, dig);

        // digest -> u in (0,1], first 16 bytes big-endian
        unsigned long long hi =
            ((unsigned long long)dig[0]<<56)|((unsigned long long)dig[1]<<48)|
            ((unsigned long long)dig[2]<<40)|((unsigned long long)dig[3]<<32)|
            ((unsigned long long)dig[4]<<24)|((unsigned long long)dig[5]<<16)|
            ((unsigned long long)dig[6]<<8 )|((unsigned long long)dig[7]);
        unsigned long long lo =
            ((unsigned long long)dig[8]<<56)|((unsigned long long)dig[9]<<48)|
            ((unsigned long long)dig[10]<<40)|((unsigned long long)dig[11]<<32)|
            ((unsigned long long)dig[12]<<24)|((unsigned long long)dig[13]<<16)|
            ((unsigned long long)dig[14]<<8 )|((unsigned long long)dig[15]);

        double u = ((double)hi / 18446744073709551616.0) +
                   (((double)lo + 1.0) / 340282366920938463463374607431768211456.0);

        if (u <= cutoff) {
            unsigned int idx = atomicAdd(counter, 1u);
            if (idx < max_found) {
                out_nonces[idx] = nonce;
                out_u[idx] = (float)u;
                unsigned char* dst = out_hashes + ((size_t)idx)*32;
                #pragma unroll
                for (int k=0;k<32;k++) dst[k] = dig[k];
            }
        }
    }
}

} // extern "C"
"""

# ────────────────────────────────────────────────────────────────────────
# Backend object
# ────────────────────────────────────────────────────────────────────────

@dataclass
class _Prepared:
    header: bytes
    mix_seed: bytes
    use_gpu: bool  # false when it would exceed single-block rate


class CUDABackend:
    def __init__(self, device_index: int | None = None) -> None:
        if cuda is None:
            raise DeviceUnavailable("PyCUDA not available; install pycuda or use CPU backend.")
        try:
            cuda.init()
            ndev = cuda.Device.count()
            if ndev <= 0:
                raise DeviceUnavailable("No CUDA devices available.")
            dev = cuda.Device(device_index or 0)
            self._dev = dev
            self._ctx = dev.make_context()
            self._mod = SourceModule(CUDA_SOURCE, no_extern_c=True, options=["-O3"])
            self._func = self._mod.get_function("find_hashshares")
        except DeviceUnavailable:
            raise
        except Exception as e:  # pragma: no cover
            # In case of partial init, try to pop context
            try:
                cuda.Context.pop()
            except Exception:
                pass
            raise DeviceUnavailable(f"Failed to initialize CUDA backend: {e}") from e

        # Build device info
        mem = None
        try:
            mem = dev.total_memory()
        except Exception:
            pass
        self._info = DeviceInfo(
            type=getattr(DeviceType, "GPU", "gpu"),
            name=str(dev.name()),
            index=int(device_index or 0),
            vendor="NVIDIA",
            driver=None,
            compute_units=None,
            memory_bytes=mem,
            max_batch=None,
            flags={"cuda": True, "sha3_singleblock": True},
        )

    def __del__(self) -> None:  # pragma: no cover
        try:
            self._ctx.pop()
        except Exception:
            pass

    def info(self) -> DeviceInfo:
        return self._info

    def prepare_header(self, header_bytes: bytes, mix_seed: bytes) -> _Prepared:
        use_gpu = (len(header_bytes) + 32 + 8) <= 136
        return _Prepared(header=bytes(header_bytes), mix_seed=bytes(mix_seed), use_gpu=use_gpu)

    def scan(
        self,
        prepared: _Prepared,
        *,
        theta_micro: float,
        start_nonce: int,
        iterations: int,
        max_found: int = 1,
        thread_id: int = 0,  # API compat, unused
    ) -> List[Dict[str, Any]]:
        if not prepared.use_gpu:
            return self._scan_cpu(prepared, theta_micro=theta_micro, start_nonce=start_nonce,
                                  iterations=iterations, max_found=max_found)

        cutoff = _exp_neg_theta(theta_micro)

        header_b = prepared.header
        mix_b = prepared.mix_seed

        # Device buffers
        d_header = cuda.mem_alloc(len(header_b))
        d_mix = cuda.mem_alloc(len(mix_b))
        d_out_nonces = cuda.mem_alloc(max_found * 8)
        d_out_u = cuda.mem_alloc(max_found * 4)
        d_out_hashes = cuda.mem_alloc(max_found * 32)
        d_counter = cuda.mem_alloc(4)

        # Upload constants & zero counter
        cuda.memcpy_htod(d_header, header_b)
        cuda.memcpy_htod(d_mix, mix_b)
        cuda.memcpy_htod(d_counter, b"\x00\x00\x00\x00")

        # Launch
        BLOCK = 256
        # Let grid cover iterations reasonably; grid-stride loop handles the rest
        GRID = min(max(1, (iterations + BLOCK - 1) // BLOCK), 65535)

        try:
            self._func(
                d_header,
                _u32(len(header_b)),
                d_mix,
                _u64(start_nonce),
                _f64(cutoff),
                d_out_nonces,
                d_out_u,
                d_out_hashes,
                d_counter,
                _u32(max_found),
                _u32(iterations),
                block=(BLOCK, 1, 1),
                grid=(GRID, 1, 1),
            )
            cuda.Context.synchronize()
        except Exception:
            # Kernel failed → CPU fallback for this call
            return self._scan_cpu(prepared, theta_micro=theta_micro, start_nonce=start_nonce,
                                  iterations=iterations, max_found=max_found)

        # Read back
        counter_bytes = bytearray(4)
        cuda.memcpy_dtoh(counter_bytes, d_counter)
        found_total = min(struct.unpack_from("<I", counter_bytes, 0)[0], max_found)

        res: List[Dict[str, Any]] = []
        if found_total > 0:
            host_nonces = bytearray(found_total * 8)
            host_u = bytearray(found_total * 4)
            host_hashes = bytearray(found_total * 32)
            cuda.memcpy_dtoh(host_nonces, d_out_nonces)
            cuda.memcpy_dtoh(host_u, d_out_u)
            cuda.memcpy_dtoh(host_hashes, d_out_hashes)
            for i in range(found_total):
                (nonce,) = struct.unpack_from("<Q", host_nonces, i * 8)
                (u_f32,) = struct.unpack_from("<f", host_u, i * 4)
                digest = bytes(host_hashes[i * 32 : (i + 1) * 32])
                d_ratio = (-math.log(max(u_f32, 1e-38))) / max(theta_micro / 1e6, 1e-12)
                res.append({"nonce": int(nonce), "u": float(u_f32), "d_ratio": float(d_ratio), "hash": digest})

        res.sort(key=lambda x: x["nonce"])
        return res[:max_found]

    # ---- CPU fallback ----
    def _scan_cpu(
        self,
        prepared: _Prepared,
        *,
        theta_micro: float,
        start_nonce: int,
        iterations: int,
        max_found: int,
    ) -> List[Dict[str, Any]]:
        cutoff = _exp_neg_theta(theta_micro)
        out: List[Dict[str, Any]] = []
        for i in range(iterations):
            if max_found > 0 and len(out) >= max_found:
                break
            nonce = start_nonce + i
            d = _digest_bytes_cpu(prepared.header, prepared.mix_seed, nonce)
            u = _uniform_from_digest(d)
            if u <= cutoff:
                d_ratio = (-math.log(u)) / max(theta_micro / 1e6, 1e-12)
                out.append({"nonce": nonce, "u": float(u), "d_ratio": float(d_ratio), "hash": d})
        return out


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────

def _u32(x: int) -> int:
    # PyCUDA will marshal ints by value; keep helper for symmetry
    return int(x & 0xFFFFFFFF)


def _u64(x: int) -> int:
    return int(x & 0xFFFFFFFFFFFFFFFF)


def _f64(x: float) -> float:
    return float(x)


# ────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────

def list_devices() -> List[DeviceInfo]:
    """Enumerate CUDA devices (best effort)."""
    infos: List[DeviceInfo] = []
    if cuda is None:
        return infos
    try:
        cuda.init()
        for i in range(cuda.Device.count()):
            d = cuda.Device(i)
            mem = None
            try:
                mem = d.total_memory()
            except Exception:
                pass
            infos.append(
                DeviceInfo(
                    type=getattr(DeviceType, "GPU", "gpu"),
                    name=str(d.name()),
                    index=i,
                    vendor="NVIDIA",
                    driver=None,
                    compute_units=None,
                    memory_bytes=mem,
                    max_batch=None,
                    flags={"cuda": True},
                )
            )
    except Exception:
        pass
    return infos


def create(**opts: Any) -> CUDABackend:
    """
    Create a CUDA backend.

    Options:
      device_index: int = 0
    """
    return CUDABackend(device_index=opts.get("device_index", 0))


# Diagnostics
if __name__ == "__main__":  # pragma: no cover
    try:
        dev = create()
        print("[gpu_cuda] Device:", dev.info())
        hdr = b"\x00" * 80
        mix = b"\x11" * 32
        prep = dev.prepare_header(hdr, mix)
        res = dev.scan(prep, theta_micro=200000.0, start_nonce=0, iterations=500000, max_found=3)
        for r in res:
            print("  nonce=", r["nonce"], "u=", r["u"], "d_ratio=", r["d_ratio"])
    except Exception as e:
        print("[gpu_cuda] Not available:", e)
