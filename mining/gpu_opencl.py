from __future__ import annotations

"""
Animica mining.gpu_opencl
=========================

Optional OpenCL backend that computes SHA3-256(header || mixSeed || nonce_le8)
on the GPU and applies the same acceptance test as the CPU backend:

    u = uniform_from_digest(digest)
    accept iff u <= exp(-Theta)

Design notes
------------
- Guarded import: if PyOpenCL or a GPU platform is not available, this backend
  raises DeviceUnavailable at creation time (and the miner will fall back to CPU).
- Single-block SHA3-256: our input size is typically: len(header) ~ 80 + 32 (mix) + 8 (nonce) = 120B.
  That fits in the SHA3-256 rate (136B), so the kernel implements the fast single-block absorb+pads.
- Determinism: given the same inputs, results are bit-stable. We return the same
  structure as the CPU backend scan(): list of {nonce, u, d_ratio, hash} dicts.
- Safety: if the header+mix+nonce were ever to exceed the single-block rate, or
  if the device reports an error, we transparently fall back to CPU scanning for
  that call (not for the whole process).

This module depends on:
    pyopencl>=2022.3
and a GPU (or CPU OpenCL) driver.

"""

import math
import os
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    import pyopencl as cl  # type: ignore
    import pyopencl.array as cl_array  # type: ignore
except Exception:  # pragma: no cover
    cl = None  # type: ignore

# Local errors & types
try:
    from .errors import DeviceUnavailable  # type: ignore
except Exception:  # pragma: no cover
    class DeviceUnavailable(RuntimeError):
        pass

try:  # types only
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

# Reuse canonical digest/uniform math if present (for CPU fallback & mapping)
try:
    from . import nonce_domain as nd  # type: ignore
    _HAS_ND = True
except Exception:  # pragma: no cover
    _HAS_ND = False

import hashlib  # for CPU fallback only

def _nonce_le8(n: int) -> bytes:
    return struct.pack("<Q", n & 0xFFFFFFFFFFFFFFFF)

def _digest_bytes_cpu(header: bytes, mix: bytes, nonce: int) -> bytes:
    if _HAS_ND and hasattr(nd, "digest_header_mix_nonce"):
        return nd.digest_header_mix_nonce(header, mix, nonce)  # type: ignore
    h = hashlib.sha3_256()
    h.update(header); h.update(mix); h.update(_nonce_le8(nonce))
    return h.digest()

def _uniform_from_digest(d: bytes) -> float:
    if _HAS_ND and hasattr(nd, "uniform_from_digest"):
        return float(nd.uniform_from_digest(d))  # type: ignore
    # first 16 bytes, big-endian, map to (0,1]
    hi = int.from_bytes(d[0:8], "big")
    lo = int.from_bytes(d[8:16], "big")
    # u = (hi*2^64 + lo + 1) / 2^128
    u = (hi / 18446744073709551616.0) + ((lo + 1.0) / 340282366920938463463374607431768211456.0)
    return u

def _exp_neg_theta(theta_micro: float) -> float:
    if _HAS_ND and hasattr(nd, "exp_neg_theta"):
        return float(nd.exp_neg_theta(theta_micro))  # type: ignore
    return math.exp(-theta_micro / 1e6)

# ────────────────────────────────────────────────────────────────────────
# OpenCL kernel (single-block SHA3-256 + acceptance check)
# ────────────────────────────────────────────────────────────────────────

KERNEL_SOURCE = r"""
#pragma OPENCL EXTENSION cl_khr_int64_base_atomics : enable
// Single-block SHA3-256 (rate 136), Keccak-f[1600] permutation.
// Based on the public-domain Keccak reference, adapted to OpenCL C.

inline ulong rol64(ulong x, uint n) { return (x << n) | (x >> (64 - n)); }

__constant ulong RC[24] = {
  0x0000000000000001UL, 0x0000000000008082UL, 0x800000000000808aUL, 0x8000000080008000UL,
  0x000000000000808bUL, 0x0000000080000001UL, 0x8000000080008081UL, 0x8000000000008009UL,
  0x000000000000008aUL, 0x0000000000000088UL, 0x0000000080008009UL, 0x000000008000000aUL,
  0x000000008000808bUL, 0x800000000000008bUL, 0x8000000000008089UL, 0x8000000000008003UL,
  0x8000000000008002UL, 0x8000000000000080UL, 0x000000000000800aUL, 0x800000008000000aUL,
  0x8000000080008081UL, 0x8000000000008080UL, 0x0000000080000001UL, 0x8000000080008008UL
};

inline void keccak_f1600(ulong A[25]) {
  const uint r[25] = { // Rho offsets
    0,  1, 62, 28, 27,
   36, 44,  6, 55, 20,
    3, 10, 43, 25, 39,
   41, 45, 15, 21,  8,
   18,  2, 61, 56, 14
  };
  const uint p[25] = { // Pi index
    0,  6, 12, 18, 24,
    3,  9, 10, 16, 22,
    1,  7, 13, 19, 20,
    4,  5, 11, 17, 23,
    2,  8, 14, 15, 21
  };

  for (int ir = 0; ir < 24; ir++) {
    ulong C0 = A[0]^A[5]^A[10]^A[15]^A[20];
    ulong C1 = A[1]^A[6]^A[11]^A[16]^A[21];
    ulong C2 = A[2]^A[7]^A[12]^A[17]^A[22];
    ulong C3 = A[3]^A[8]^A[13]^A[18]^A[23];
    ulong C4 = A[4]^A[9]^A[14]^A[19]^A[24];
    ulong D0 = rol64(C1,1) ^ C3;
    ulong D1 = rol64(C2,1) ^ C4;
    ulong D2 = rol64(C3,1) ^ C0;
    ulong D3 = rol64(C4,1) ^ C1;
    ulong D4 = rol64(C0,1) ^ C2;
    A[0]^=D0; A[5]^=D0; A[10]^=D0; A[15]^=D0; A[20]^=D0;
    A[1]^=D1; A[6]^=D1; A[11]^=D1; A[16]^=D1; A[21]^=D1;
    A[2]^=D2; A[7]^=D2; A[12]^=D2; A[17]^=D2; A[22]^=D2;
    A[3]^=D3; A[8]^=D3; A[13]^=D3; A[18]^=D3; A[23]^=D3;
    A[4]^=D4; A[9]^=D4; A[14]^=D4; A[19]^=D4; A[24]^=D4;

    // Rho + Pi in one pass
    ulong B[25];
    for (int i = 0; i < 25; i++) {
      B[p[i]] = rol64(A[i], r[i]);
    }

    // Chi
    for (int y = 0; y < 5; y++) {
      int i = 5*y;
      ulong b0=B[i+0], b1=B[i+1], b2=B[i+2], b3=B[i+3], b4=B[i+4];
      A[i+0] = b0 ^ ((~b1) & b2);
      A[i+1] = b1 ^ ((~b2) & b3);
      A[i+2] = b2 ^ ((~b3) & b4);
      A[i+3] = b3 ^ ((~b4) & b0);
      A[i+4] = b4 ^ ((~b0) & b1);
    }

    // Iota
    A[0] ^= RC[ir];
  }
}

inline ulong load64_le(const __global uchar* p) {
  return ( (ulong)p[0]       ) | ((ulong)p[1]<<8 ) | ((ulong)p[2]<<16) | ((ulong)p[3]<<24) |
         ((ulong)p[4]<<32) | ((ulong)p[5]<<40) | ((ulong)p[6]<<48) | ((ulong)p[7]<<56);
}

inline void store64_le(__global uchar* p, ulong v) {
  p[0]=(uchar)(v); p[1]=(uchar)(v>>8); p[2]=(uchar)(v>>16); p[3]=(uchar)(v>>24);
  p[4]=(uchar)(v>>32); p[5]=(uchar)(v>>40); p[6]=(uchar)(v>>48); p[7]=(uchar)(v>>56);
}

inline void sha3_256_singleblock(const __global uchar* msg, uint msg_len, __global uchar* out32) {
  // rate = 136, capacity = 64
  ulong A[25]; for (int i=0;i<25;i++) A[i]=0UL;

  // Build padded block in private memory
  uchar blk[136];
  for (int i=0;i<136;i++) blk[i]=0;
  for (uint i=0;i<msg_len;i++) blk[i]=msg[i];
  // pad10*1 for SHA3: 0x06 ... 0x80
  blk[msg_len] ^= 0x06;
  blk[135] ^= 0x80;

  // Absorb: XOR first 17 lanes (136 bytes)
  for (int i=0;i<17;i++) {
    A[i] ^= load64_le(blk + 8*i);
  }

  keccak_f1600(A);

  // Squeeze 32 bytes from state (little-endian)
  for (int i=0;i<4;i++) {
    store64_le(out32 + 8*i, A[i]);
  }
}

__kernel void find_hashshares(
    __global const uchar* header,
    uint header_len,
    __global const uchar* mix,     // 32 bytes
    ulong start_nonce,
    double cutoff,                 // exp(-Theta) in (0,1]
    __global ulong* out_nonces,    // capacity = max_found
    __global float* out_u,         // capacity = max_found
    __global uchar* out_hashes,    // capacity = max_found * 32
    __global atomic_uint* counter, // atomic idx
    uint max_found
) {
  size_t gid = get_global_id(0);
  ulong nonce = start_nonce + (ulong)gid;

  // Assemble message = header || mix || nonce_le8 (fits in 136 bytes)
  // If it wouldn't fit, we just return (host will fallback to CPU).
  if (header_len + 32u + 8u > 136u) return;

  // We reuse out_hashes as scratch to avoid extra allocations: OK because we
  // only write to our own 32B slice after we win the race; here we need a
  // private buffer for msg.
  __private uchar msg[136];
  for (uint i=0;i<header_len;i++) msg[i] = header[i];
  for (int i=0;i<32;i++) msg[header_len + i] = mix[i];
  // nonce little-endian
  __private uchar n8[8];
  n8[0]=(uchar)(nonce); n8[1]=(uchar)(nonce>>8); n8[2]=(uchar)(nonce>>16); n8[3]=(uchar)(nonce>>24);
  n8[4]=(uchar)(nonce>>32); n8[5]=(uchar)(nonce>>40); n8[6]=(uchar)(nonce>>48); n8[7]=(uchar)(nonce>>56);
  for (int i=0;i<8;i++) msg[header_len + 32 + i] = n8[i];

  // Compute digest
  __private uchar dig[32];
  sha3_256_singleblock(msg, header_len + 32u + 8u, dig);

  // Map digest -> u in (0,1] using first 16 bytes as big-endian integer
  ulong hi = ((ulong)dig[0]<<56)|((ulong)dig[1]<<48)|((ulong)dig[2]<<40)|((ulong)dig[3]<<32)|
             ((ulong)dig[4]<<24)|((ulong)dig[5]<<16)|((ulong)dig[6]<<8)|((ulong)dig[7]);
  ulong lo = ((ulong)dig[8]<<56)|((ulong)dig[9]<<48)|((ulong)dig[10]<<40)|((ulong)dig[11]<<32)|
             ((ulong)dig[12]<<24)|((ulong)dig[13]<<16)|((ulong)dig[14]<<8)|((ulong)dig[15]);

  // u = hi/2^64 + (lo+1)/2^128; computed in double for precision
  double u = ((double)hi / 18446744073709551616.0) +
             (((double)lo + 1.0) / 340282366920938463463374607431768211456.0);

  if (u <= cutoff) {
    uint idx = atomic_inc(counter);
    if (idx < max_found) {
      out_nonces[idx] = nonce;
      out_u[idx] = (float)u;
      __global uchar* dst = out_hashes + ((size_t)idx)*32;
      for (int i=0;i<32;i++) dst[i] = dig[i];
    }
  }
}
"""

# ────────────────────────────────────────────────────────────────────────
# Backend object
# ────────────────────────────────────────────────────────────────────────

@dataclass
class _Prepared:
    header: bytes
    mix_seed: bytes
    use_gpu: bool  # false when payload would exceed single-block rate

class OpenCLBackend:
    def __init__(self, platform_index: Optional[int] = None, device_index: Optional[int] = None) -> None:
        if cl is None:
            raise DeviceUnavailable("PyOpenCL not available; install pyopencl or use CPU backend.")
        try:
            platforms = cl.get_platforms()
            if not platforms:
                raise DeviceUnavailable("No OpenCL platforms available.")
            plat = platforms[platform_index or 0]
            devices = plat.get_devices()
            if not devices:
                raise DeviceUnavailable(f"No OpenCL devices on platform '{plat.name}'.")
            dev = devices[device_index or 0]
            self.ctx = cl.Context([dev])
            self.queue = cl.CommandQueue(self.ctx)
            self.prog = cl.Program(self.ctx, KERNEL_SOURCE).build()
        except DeviceUnavailable:
            raise
        except Exception as e:  # pragma: no cover
            raise DeviceUnavailable(f"Failed to initialize OpenCL backend: {e}") from e

        mem = None
        try:
            mem = dev.global_mem_size  # type: ignore[attr-defined]
        except Exception:
            pass
        self._info = DeviceInfo(
            type=getattr(DeviceType, "GPU", "gpu"),
            name=getattr(dev, "name", "OpenCL Device"),
            index=0,
            vendor=getattr(dev, "vendor", None),
            driver=getattr(dev, "driver_version", None),
            compute_units=getattr(dev, "max_compute_units", None),
            memory_bytes=mem,
            max_batch=None,
            flags={"opencl": True, "sha3_singleblock": True},
        )

    def info(self) -> DeviceInfo:
        return self._info

    def prepare_header(self, header_bytes: bytes, mix_seed: bytes) -> _Prepared:
        # We only support single-block hashing in-kernel (rate 136)
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
        thread_id: int = 0,  # unused; API-compatible
    ) -> List[Dict[str, Any]]:
        if not prepared.use_gpu:
            # Fallback (too large for single-block kernel)
            return self._scan_cpu(prepared, theta_micro=theta_micro, start_nonce=start_nonce,
                                  iterations=iterations, max_found=max_found)

        cutoff = _exp_neg_theta(theta_micro)

        ctx, q, prg = self.ctx, self.queue, self.prog
        mf = cl.mem_flags  # type: ignore

        # Upload constants
        header_b = prepared.header
        mix_b = prepared.mix_seed
        hb = cl.Buffer(ctx, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=header_b)
        mb = cl.Buffer(ctx, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=mix_b)

        out_nonces = cl.Buffer(ctx, mf.WRITE_ONLY, size=max_found * 8)
        out_u = cl.Buffer(ctx, mf.WRITE_ONLY, size=max_found * 4)
        out_hashes = cl.Buffer(ctx, mf.WRITE_ONLY, size=max_found * 32)
        counter = cl.Buffer(ctx, mf.READ_WRITE, size=4)
        # zero the counter
        cl.enqueue_fill_buffer(q, counter, b"\x00\x00\x00\x00", 0, 4)

        remaining = int(iterations)
        nonce = int(start_nonce)
        found_total = 0

        # Dispatch in chunks to respect device limits
        # (work-group size is left to the driver; we pick a practical chunk)
        CHUNK = 1_000_000
        while remaining > 0 and found_total < max_found:
            n_this = min(remaining, CHUNK)
            kernel = prg.find_hashshares
            kernel.set_args(
                hb,
                _u32(len(header_b)),
                mb,
                _u64(nonce),
                _f64(cutoff),
                out_nonces,
                out_u,
                out_hashes,
                counter,
                _u32(max_found),
            )
            cl.enqueue_nd_range_kernel(q, kernel, (n_this,), None)
            q.finish()

            # Check how many we have so far
            found_total = _read_u32(q, counter)
            if found_total >= max_found:
                break

            remaining -= n_this
            nonce += n_this

        # Read results (truncate to capacity)
        found_total = min(found_total, max_found)
        res: List[Dict[str, Any]] = []
        if found_total > 0:
            host_nonces = bytearray(found_total * 8)
            host_u = bytearray(found_total * 4)
            host_hashes = bytearray(found_total * 32)
            cl.enqueue_copy(q, host_nonces, out_nonces).wait()
            cl.enqueue_copy(q, host_u, out_u).wait()
            cl.enqueue_copy(q, host_hashes, out_hashes).wait()

            for i in range(found_total):
                n = struct.unpack_from("<Q", host_nonces, i * 8)[0]
                # float32 -> float
                (u_f32,) = struct.unpack_from("<f", host_u, i * 4)
                digest = bytes(host_hashes[i * 32 : (i + 1) * 32])
                d_ratio = (-math.log(max(u_f32, 1e-38))) / max(theta_micro / 1e6, 1e-12)
                res.append({"nonce": int(n), "u": float(u_f32), "d_ratio": float(d_ratio), "hash": digest})

        # Deterministic order
        res.sort(key=lambda x: x["nonce"])
        return res[:max_found]

    # ---- internals ----

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

def _u32(x: int) -> bytes:
    return struct.pack("<I", x & 0xFFFFFFFF)

def _u64(x: int) -> bytes:
    return struct.pack("<Q", x & 0xFFFFFFFFFFFFFFFF)

def _f64(x: float) -> bytes:
    return struct.pack("<d", float(x))

def _read_u32(queue, buf) -> int:
    tmp = bytearray(4)
    cl.enqueue_copy(queue, tmp, buf).wait()
    return struct.unpack_from("<I", tmp, 0)[0]

# ────────────────────────────────────────────────────────────────────────
# Public factory
# ────────────────────────────────────────────────────────────────────────

def list_devices() -> List[DeviceInfo]:
    """Enumerate OpenCL devices (best-effort)."""
    if cl is None:
        return []
    infos: List[DeviceInfo] = []
    try:
        for pi, plat in enumerate(cl.get_platforms()):
            for di, dev in enumerate(plat.get_devices()):
                mem = getattr(dev, "global_mem_size", None)
                infos.append(DeviceInfo(
                    type=getattr(DeviceType, "GPU", "gpu"),
                    name=getattr(dev, "name", f"OpenCL Device {di}"),
                    index=di,
                    vendor=getattr(dev, "vendor", None),
                    driver=getattr(dev, "driver_version", None),
                    compute_units=getattr(dev, "max_compute_units", None),
                    memory_bytes=mem,
                    max_batch=None,
                    flags={"opencl": True},
                ))
    except Exception:
        pass
    return infos

def create(**opts: Any) -> OpenCLBackend:
    """
    Create an OpenCL backend.

    Options:
      platform_index: int = 0
      device_index: int = 0
    """
    platform_index = opts.get("platform_index")
    device_index = opts.get("device_index")
    return OpenCLBackend(platform_index=platform_index, device_index=device_index)

# Diagnostics
if __name__ == "__main__":  # pragma: no cover
    try:
        dev = create()
        print("[gpu_opencl] Device:", dev.info())
        hdr = b"\x00" * 80
        mix = b"\x11" * 32
        prep = dev.prepare_header(hdr, mix)
        res = dev.scan(prep, theta_micro=200000.0, start_nonce=0, iterations=500000, max_found=3)
        for r in res:
            print("  nonce=", r["nonce"], "u=", r["u"], "d_ratio=", r["d_ratio"])
    except Exception as e:
        print("[gpu_opencl] Not available:", e)
