from __future__ import annotations

"""
Animica mining.gpu_metal
========================

Optional Metal (macOS) backend that scans nonces using a Metal compute kernel
implementing single-block SHA3-256(header || mixSeed || nonce_le8) and acceptance:

    u = uniform_from_digest(digest)
    accept iff u <= exp(-Theta)

Notes
-----
- Guarded import: requires macOS + PyObjC (pyobjc-core, pyobjc-framework-Metal).
  If unavailable, creating this backend raises DeviceUnavailable and the miner
  will automatically fall back to CPU.
- Single-block constraint: kernel supports inputs up to 136 bytes (SHA3-256 rate).
  If header_len + 32 + 8 > 136, we transparently fall back to CPU for that scan.
- Determinism: outputs are stable and returned as:
    {nonce: int, u: float, d_ratio: float, hash: bytes}
- Safety: any Metal error ⇒ CPU fallback for the call (not the whole process).

Install (on macOS):
    python3 -m pip install pyobjc-core pyobjc-framework-Metal
"""

import math
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# ---------- Optional imports (guarded) ----------
try:  # Only present on macOS with PyObjC installed
    import platform
    if platform.system() == "Darwin":
        import Metal  # type: ignore
        _HAS_METAL = True
    else:
        Metal = None  # type: ignore
        _HAS_METAL = False
except Exception:  # pragma: no cover
    Metal = None  # type: ignore
    _HAS_METAL = False

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
# Metal Shading Language kernel (single-block SHA3-256 + acceptance check)
# ────────────────────────────────────────────────────────────────────────

MSL_SOURCE = r"""
#include <metal_stdlib>
using namespace metal;

constant ulong RC[24] = {
  0x0000000000000001ul, 0x0000000000008082ul, 0x800000000000808aul, 0x8000000080008000ul,
  0x000000000000808bul, 0x0000000080000001ul, 0x8000000080008081ul, 0x8000000000008009ul,
  0x000000000000008aul, 0x0000000000000088ul, 0x0000000080008009ul, 0x000000008000000aul,
  0x000000008000808bul, 0x800000000000008bul, 0x8000000000008089ul, 0x8000000000008003ul,
  0x8000000000008002ul, 0x8000000000000080ul, 0x000000000000800aul, 0x800000008000000aul,
  0x8000000080008081ul, 0x8000000000008080ul, 0x0000000080000001ul, 0x8000000080008008ul
};

inline ulong rol64(ulong x, uint n) { return (x << n) | (x >> (64 - n)); }

inline ulong load64_le(const device uchar* p) {
    return (ulong)p[0]       |
           ((ulong)p[1] << 8 ) |
           ((ulong)p[2] << 16) |
           ((ulong)p[3] << 24) |
           ((ulong)p[4] << 32) |
           ((ulong)p[5] << 40) |
           ((ulong)p[6] << 48) |
           ((ulong)p[7] << 56);
}

inline void store64_le(device uchar* p, ulong v) {
    p[0]=(uchar)(v);
    p[1]=(uchar)(v>>8);
    p[2]=(uchar)(v>>16);
    p[3]=(uchar)(v>>24);
    p[4]=(uchar)(v>>32);
    p[5]=(uchar)(v>>40);
    p[6]=(uchar)(v>>48);
    p[7]=(uchar)(v>>56);
}

inline void keccak_f1600(thread ulong A[25]) {
    const uint r[25] = {
        0,  1, 62, 28, 27,
       36, 44,  6, 55, 20,
        3, 10, 43, 25, 39,
       41, 45, 15, 21,  8,
       18,  2, 61, 56, 14
    };
    const uint p[25] = {
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

        ulong B[25];
        for (int i = 0; i < 25; i++) {
            B[p[i]] = rol64(A[i], r[i]);
        }
        for (int y = 0; y < 5; y++) {
            int i = 5*y;
            ulong b0=B[i+0], b1=B[i+1], b2=B[i+2], b3=B[i+3], b4=B[i+4];
            A[i+0] = b0 ^ ((~b1) & b2);
            A[i+1] = b1 ^ ((~b2) & b3);
            A[i+2] = b2 ^ ((~b3) & b4);
            A[i+3] = b3 ^ ((~b4) & b0);
            A[i+4] = b4 ^ ((~b0) & b1);
        }
        A[0] ^= RC[ir];
    }
}

inline void sha3_256_singleblock(const device uchar* msg, uint msg_len, thread uchar* out32) {
    thread ulong A[25];
    for (int i=0;i<25;i++) A[i]=0ul;

    uchar blk[136];
    for (int i=0;i<136;i++) blk[i]=0;
    for (uint i=0;i<msg_len;i++) blk[i]=msg[i];
    blk[msg_len] ^= 0x06;
    blk[135] ^= 0x80;

    for (int i=0;i<17;i++) {
        A[i] ^= load64_le((const device uchar*)(blk + 8*i));
    }

    keccak_f1600(A);

    for (int i=0;i<4;i++) {
        store64_le((device uchar*)(out32 + 8*i), A[i]);
    }
}

kernel void find_hashshares(
    device const uchar* header         [[ buffer(0) ]],
    constant uint&      header_len     [[ buffer(1) ]],
    device const uchar* mix            [[ buffer(2) ]],
    constant ulong&     start_nonce    [[ buffer(3) ]],
    constant float&     cutoff         [[ buffer(4) ]],
    device ulong*       out_nonces     [[ buffer(5) ]],
    device float*       out_u          [[ buffer(6) ]],
    device uchar*       out_hashes     [[ buffer(7) ]],
    device atomic_uint* counter        [[ buffer(8) ]],
    constant uint&      max_found      [[ buffer(9) ]],
    constant uint&      iterations     [[ buffer(10) ]],
    uint                gid            [[ thread_position_in_grid ]]
) {
    if (header_len + 32u + 8u > 136u) return;
    if (gid >= iterations) return;

    ulong nonce = start_nonce + (ulong)gid;

    uchar msg[136];
    for (uint j=0;j<header_len;j++) msg[j] = header[j];
    for (int j=0;j<32;j++) msg[header_len + j] = mix[j];
    ulong n = nonce;
    msg[header_len+32+0] = (uchar)(n);
    msg[header_len+32+1] = (uchar)(n>>8);
    msg[header_len+32+2] = (uchar)(n>>16);
    msg[header_len+32+3] = (uchar)(n>>24);
    msg[header_len+32+4] = (uchar)(n>>32);
    msg[header_len+32+5] = (uchar)(n>>40);
    msg[header_len+32+6] = (uchar)(n>>48);
    msg[header_len+32+7] = (uchar)(n>>56);

    uchar dig[32];
    sha3_256_singleblock((const device uchar*)msg, header_len + 32u + 8u, dig);

    // u ← first 16 bytes as 128-bit big-endian mapped to (0,1]
    ulong hi = ((ulong)dig[0]<<56)|((ulong)dig[1]<<48)|((ulong)dig[2]<<40)|((ulong)dig[3]<<32)|
               ((ulong)dig[4]<<24)|((ulong)dig[5]<<16)|((ulong)dig[6]<<8 )|((ulong)dig[7]);
    ulong lo = ((ulong)dig[8]<<56)|((ulong)dig[9]<<48)|((ulong)dig[10]<<40)|((ulong)dig[11]<<32)|
               ((ulong)dig[12]<<24)|((ulong)dig[13]<<16)|((ulong)dig[14]<<8 )|((ulong)dig[15]);

    float u = ((float)((double)hi / 18446744073709551616.0)) +
              ((float)(((double)lo + 1.0) / 340282366920938463463374607431768211456.0));

    if (u <= cutoff) {
        uint idx = atomic_fetch_add_explicit(counter, 1u, memory_order_relaxed);
        if (idx < max_found) {
            out_nonces[idx] = nonce;
            out_u[idx] = u;
            device uchar* dst = out_hashes + ((size_t)idx)*32;
            for (int k=0;k<32;k++) dst[k] = dig[k];
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
    use_gpu: bool


class MetalBackend:
    def __init__(self, device_index: int | None = None) -> None:
        if not _HAS_METAL:
            raise DeviceUnavailable("Metal backend requires macOS + pyobjc-framework-Metal.")
        # Device
        dev = Metal.MTLCreateSystemDefaultDevice()
        if dev is None:
            raise DeviceUnavailable("No Metal device available.")
        self._dev = dev
        # Queue
        self._queue = dev.newCommandQueue()
        if self._queue is None:
            raise DeviceUnavailable("Failed to create Metal command queue.")
        # Compile kernel
        err = None
        lib, err = dev.newLibraryWithSource_options_error_(MSL_SOURCE, None, None)
        if lib is None:
            raise DeviceUnavailable(f"Metal library compile failed: {err!r}")
        fn = lib.newFunctionWithName_("find_hashshares")
        if fn is None:
            raise DeviceUnavailable("Metal function 'find_hashshares' not found.")
        pso, err = dev.newComputePipelineStateWithFunction_error_(fn, None)
        if pso is None:
            raise DeviceUnavailable(f"Metal pipeline creation failed: {err!r}")
        self._pso = pso
        # Device info
        name = str(dev.name())
        self._info = DeviceInfo(
            type=getattr(DeviceType, "GPU", "gpu"),
            name=name,
            index=int(device_index or 0),
            vendor="Apple",
            driver=None,
            compute_units=None,
            memory_bytes=None,
            max_batch=None,
            flags={"metal": True, "sha3_singleblock": True},
        )

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
        thread_id: int = 0,  # API compat
    ) -> List[Dict[str, Any]]:
        # Fallback if message would exceed one SHA3 rate block
        if not prepared.use_gpu:
            return self._scan_cpu(prepared, theta_micro=theta_micro, start_nonce=start_nonce,
                                  iterations=iterations, max_found=max_found)
        try:
            cutoff = float(_exp_neg_theta(theta_micro))
            dev = self._dev
            queue = self._queue
            pso = self._pso

            # Buffers
            b_header = _mkbuf(dev, prepared.header)
            b_header_len = _mkbuf_u32(dev, len(prepared.header))
            b_mix = _mkbuf(dev, prepared.mix_seed)
            b_start = _mkbuf_u64(dev, start_nonce)
            b_cutoff = _mkbuf_f32(dev, cutoff)
            b_out_nonces = dev.newBufferWithLength_options_(max_found * 8, 0)
            b_out_u = dev.newBufferWithLength_options_(max_found * 4, 0)
            b_out_hashes = dev.newBufferWithLength_options_(max_found * 32, 0)
            b_counter = _mkbuf_u32(dev, 0)  # atomic counter
            b_max_found = _mkbuf_u32(dev, max_found)
            b_iters = _mkbuf_u32(dev, iterations)

            # Encoder
            cb = queue.commandBuffer()
            enc = cb.computeCommandEncoder()
            enc.setComputePipelineState_(pso)
            enc.setBuffer_offset_atIndex_(b_header, 0, 0)
            enc.setBuffer_offset_atIndex_(b_header_len, 0, 1)
            enc.setBuffer_offset_atIndex_(b_mix, 0, 2)
            enc.setBuffer_offset_atIndex_(b_start, 0, 3)
            enc.setBuffer_offset_atIndex_(b_cutoff, 0, 4)
            enc.setBuffer_offset_atIndex_(b_out_nonces, 0, 5)
            enc.setBuffer_offset_atIndex_(b_out_u, 0, 6)
            enc.setBuffer_offset_atIndex_(b_out_hashes, 0, 7)
            enc.setBuffer_offset_atIndex_(b_counter, 0, 8)
            enc.setBuffer_offset_atIndex_(b_max_found, 0, 9)
            enc.setBuffer_offset_atIndex_(b_iters, 0, 10)

            # Dispatch
            tpt = min(256, pso.maxTotalThreadsPerThreadgroup())
            threads_per_tg = Metal.MTLSizeMake(tpt, 1, 1)
            tg_count = Metal.MTLSizeMake((iterations + tpt - 1) // tpt, 1, 1)
            enc.dispatchThreadgroups_threadsPerThreadgroup_(tg_count, threads_per_tg)
            enc.endEncoding()
            cb.commit()
            cb.waitUntilCompleted()

            # Read counter
            found_total = int(_read_u32(b_counter))
            if found_total > max_found:
                found_total = max_found

            res: List[Dict[str, Any]] = []
            if found_total > 0:
                out_nonces = bytes(_read_bytes(b_out_nonces, found_total * 8))
                out_u = bytes(_read_bytes(b_out_u, found_total * 4))
                out_hashes = bytes(_read_bytes(b_out_hashes, found_total * 32))
                for i in range(found_total):
                    (nonce,) = struct.unpack_from("<Q", out_nonces, i * 8)
                    (u_f32,) = struct.unpack_from("<f", out_u, i * 4)
                    digest = out_hashes[i * 32 : (i + 1) * 32]
                    d_ratio = (-math.log(max(u_f32, 1e-38))) / max(theta_micro / 1e6, 1e-12)
                    res.append({"nonce": int(nonce), "u": float(u_f32), "d_ratio": float(d_ratio), "hash": digest})
            res.sort(key=lambda x: x["nonce"])
            return res[:max_found]
        except Exception:
            # Any Metal error ⇒ CPU fallback for the call
            return self._scan_cpu(prepared, theta_micro=theta_micro, start_nonce=start_nonce,
                                  iterations=iterations, max_found=max_found)

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
            if len(out) >= max_found:
                break
            nonce = start_nonce + i
            d = _digest_bytes_cpu(prepared.header, prepared.mix_seed, nonce)
            u = _uniform_from_digest(d)
            if u <= cutoff:
                d_ratio = (-math.log(u)) / max(theta_micro / 1e6, 1e-12)
                out.append({"nonce": nonce, "u": float(u), "d_ratio": float(d_ratio), "hash": d})
        return out


# ────────────────────────────────────────────────────────────────────────
# Metal buffer helpers (PyObjC)
# ────────────────────────────────────────────────────────────────────────

def _mkbuf(dev, data: bytes):
    return dev.newBufferWithBytes_length_options_(data, len(data), 0)

def _mkbuf_u32(dev, v: int):
    b = struct.pack("<I", v & 0xFFFFFFFF)
    return dev.newBufferWithBytes_length_options_(b, len(b), 0)

def _mkbuf_u64(dev, v: int):
    b = struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF)
    return dev.newBufferWithBytes_length_options_(b, len(b), 0)

def _mkbuf_f32(dev, f: float):
    b = struct.pack("<f", float(f))
    return dev.newBufferWithBytes_length_options_(b, len(b), 0)

def _read_bytes(buf, n: int) -> memoryview:
    return memoryview(buf.contents()).cast("B")[:n]

def _read_u32(buf) -> int:
    mv = memoryview(buf.contents()).cast("B")
    return struct.unpack_from("<I", mv, 0)[0]


# ────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────

def list_devices() -> List[DeviceInfo]:
    infos: List[DeviceInfo] = []
    if not _HAS_METAL:
        return infos
    try:
        dev = Metal.MTLCreateSystemDefaultDevice()
        if dev is not None:
            infos.append(
                DeviceInfo(
                    type=getattr(DeviceType, "GPU", "gpu"),
                    name=str(dev.name()),
                    index=0,
                    vendor="Apple",
                    flags={"metal": True},
                )
            )
    except Exception:
        pass
    return infos


def create(**opts: Any) -> MetalBackend:
    """
    Create a Metal backend (macOS only).

    Options:
      device_index: int = 0 (reserved for parity with CUDA; Metal selects default device)
    """
    return MetalBackend(device_index=opts.get("device_index", 0))


# Diagnostics
if __name__ == "__main__":  # pragma: no cover
    try:
        dev = create()
        print("[gpu_metal] Device:", dev.info())
        hdr = b"\x00" * 80
        mix = b"\x22" * 32
        prep = dev.prepare_header(hdr, mix)
        res = dev.scan(prep, theta_micro=200000.0, start_nonce=0, iterations=200000, max_found=3)
        for r in res:
            print("  nonce=", r["nonce"], "u=", r["u"], "d_ratio=", r["d_ratio"])
    except Exception as e:
        print("[gpu_metal] Not available:", e)
