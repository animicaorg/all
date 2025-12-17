from __future__ import annotations

"""
Animica mining.cpu_backend
==========================

CPU mining backend with a focus on correctness and portability. It exposes
the standard backend entrypoints:

- list_devices() -> list[DeviceInfo]
- create(**opts) -> MiningDevice

and implements the MiningDevice interface expected by mining/device.py:
  - info()
  - prepare_header(header_bytes: bytes, mix_seed: bytes)
  - scan(prepared, theta_micro, start_nonce, iterations, max_found, thread_id)
  - close()

Performance notes
-----------------
- Hashing is done with a keccak/sha3 binding provided by mining/nonce_domain if
  available; otherwise we fall back to hashlib.sha3_256 over a domain-separated
  prefix (header||mixSeed||nonce_le8). The binding & mapping must remain
  consistent with proofs/hashshare.py.
- Optional NumPy/Numba are detected to speed up the *math* (not hashing), but
  hashing dominates so the pure-Python path is already fine for devnet/tests.
- Multi-threading can help because hashlib runs in C; we provide a simple
  parallel splitter when `threads>1` was requested, but *deterministic* result
  ordering is maintained by sorting on nonce before returning.

Determinism
-----------
Given the same inputs (header, mixSeed, theta_micro, start_nonce, iterations),
the set of accepted nonces and their order is deterministic regardless of
threading or environment differences. This is enforced by:
  - fixed digest→uniform mapping,
  - accepting when u <= exp(-Theta),
  - sorting the results by nonce before truncating to max_found.
"""

import math
import os
import struct
import threading
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Optional accelerators (very light usage)
try:
    import numpy as _np  # type: ignore
except Exception:  # pragma: no cover
    _np = None

try:
    import numba as _nb  # type: ignore
except Exception:  # pragma: no cover
    _nb = None

# Local types from mining.device (soft import to avoid hard dep cycle at import time)
try:  # pragma: no cover - types only
    from .device import DeviceInfo, DeviceType, MiningDevice  # type: ignore
except Exception:  # Fallback lightweight types if the import graph isn't ready

    class DeviceType(str):
        CPU = "cpu"

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
        flags: Dict[str, bool] = None  # type: ignore


# Prefer the canonical nonce-domain helpers if present
# They define digest binding and uniform mapping consistent with verification.
try:
    from . import nonce_domain as nd  # type: ignore

    _HAS_ND = True
except Exception:  # pragma: no cover
    _HAS_ND = False

# Fallback hash (NIST SHA3-256) if mining.nonce_domain is not present.
# NOTE: If your chain uses Keccak-256 (pre-NIST), ensure nonce_domain is present.
import hashlib

# ────────────────────────────────────────────────────────────────────────
# Utilities
# ────────────────────────────────────────────────────────────────────────


def _nonce_le8(n: int) -> bytes:
    return struct.pack("<Q", n & 0xFFFFFFFFFFFFFFFF)


def _digest_bytes(header: bytes, mix: bytes, nonce: int) -> bytes:
    """
    Binding for header/mix/nonce → digest. Use canonical binding from
    mining.nonce_domain if available, else fallback to SHA3-256(header||mix||nonce_le8).
    """
    if _HAS_ND and hasattr(nd, "digest_header_mix_nonce"):
        return nd.digest_header_mix_nonce(header, mix, nonce)  # type: ignore
    # Fallback (dev-only): SHA3-256 over concatenation
    h = hashlib.sha3_256()
    h.update(header)
    h.update(mix)
    h.update(_nonce_le8(nonce))
    return h.digest()


def _uniform_from_digest(d: bytes) -> float:
    """
    Map digest → u in (0,1]. Prefer canonical mapping if available.
    Otherwise use first 16 bytes as a big-endian integer, add 1, divide by 2^128.
    """
    if _HAS_ND and hasattr(nd, "uniform_from_digest"):
        return float(nd.uniform_from_digest(d))  # type: ignore
    u_num = int.from_bytes(d[:16], "big") + 1
    u_den = 1 << 128
    return u_num / u_den


def _exp_neg_theta(theta_micro: float) -> float:
    if _HAS_ND and hasattr(nd, "exp_neg_theta"):
        return float(nd.exp_neg_theta(theta_micro))  # type: ignore
    return math.exp(-theta_micro / 1e6)


# ────────────────────────────────────────────────────────────────────────
# Backend implementation
# ────────────────────────────────────────────────────────────────────────


@dataclass
class _Prepared:
    header: bytes
    mix_seed: bytes


class _CPUDevice:
    """
    Reference CPU backend.

    Options:
      - index: int = 0
      - threads: int = 0 (0/1 -> single-thread; >1 splits the nonce range)
      - batch_size: int = hint for higher-level schedulers (not used here)
      - affinity: Optional[str] = CPU affinity string (e.g., "0-7,16-23") — best effort
    """

    def __init__(
        self,
        index: int = 0,
        threads: int = 0,
        batch_size: int = 0,
        affinity: Optional[str] = None,
        **_: Any,
    ) -> None:
        self._info = DeviceInfo(
            type=getattr(DeviceType, "CPU", "cpu"),
            name="CPU",
            index=index,
            vendor="generic",
            driver="python",
            compute_units=os.cpu_count() or 1,
            memory_bytes=None,
            max_batch=None,
            flags={
                "supports_keccak": True,
                "supports_udraw": True,
                "supports_batch": True,
            },
        )
        self._threads = max(0, int(threads))
        self._batch_hint = max(0, int(batch_size))
        self._affinity = affinity
        self._set_affinity_once()

    # ---- MiningDevice interface ----

    def info(self) -> DeviceInfo:
        return self._info

    def prepare_header(self, header_bytes: bytes, mix_seed: bytes) -> _Prepared:
        # Defensive copies; treat as immutable
        return _Prepared(header=bytes(header_bytes), mix_seed=bytes(mix_seed))

    def scan(
        self,
        prepared: _Prepared,
        *,
        theta_micro: float,
        start_nonce: int,
        iterations: int,
        max_found: int = 1,
        thread_id: int = 0,
    ) -> List[Dict[str, Any]]:
        # Single-threaded fast path
        if self._threads <= 1 or iterations <= 1_000:
            return _scan_range(
                prepared.header,
                prepared.mix_seed,
                theta_micro,
                start_nonce,
                iterations,
                max_found,
            )

        # Parallel split across T threads; maintain deterministic ordering
        T = min(self._threads, max(1, os.cpu_count() or 1))
        # Split into contiguous chunks; last thread gets the remainder
        base = iterations // T
        rem = iterations % T

        results: List[List[Dict[str, Any]]] = [[] for _ in range(T)]
        threads: List[threading.Thread] = []

        def worker(k: int, n0: int, iters: int) -> None:
            results[k] = _scan_range(
                prepared.header,
                prepared.mix_seed,
                theta_micro,
                n0,
                iters,
                max_found=max_found,
            )

        n = start_nonce
        for t in range(T):
            it = base + (1 if t < rem else 0)
            if it <= 0:
                continue
            th = threading.Thread(target=worker, args=(t, n, it), daemon=True)
            threads.append(th)
            th.start()
            n += it

        for th in threads:
            th.join()

        # Merge results; keep only up to max_found by lowest nonce first (deterministic)
        merged = [item for sub in results for item in sub]
        merged.sort(key=lambda x: x["nonce"])
        if max_found > 0 and len(merged) > max_found:
            merged = merged[:max_found]
        return merged

    def close(self) -> None:
        return None

    # ---- internals ----

    def _set_affinity_once(self) -> None:
        if not self._affinity:
            return
        try:
            cpus: List[int] = []
            for part in self._affinity.split(","):
                part = part.strip()
                if not part:
                    continue
                if "-" in part:
                    a, b = part.split("-", 1)
                    cpus.extend(range(int(a), int(b) + 1))
                else:
                    cpus.append(int(part))
            if hasattr(os, "sched_setaffinity"):
                os.sched_setaffinity(0, set(cpus))  # type: ignore[attr-defined]
        except Exception:
            # Best effort; ignore if not supported
            pass


# ────────────────────────────────────────────────────────────────────────
# Inner loop (pure-Python; tiny hot function)
# ────────────────────────────────────────────────────────────────────────


def _scan_range(
    header: bytes,
    mix_seed: bytes,
    theta_micro: float,
    start_nonce: int,
    iterations: int,
    max_found: int,
) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    cutoff = _exp_neg_theta(theta_micro)
    # Local bindings for speed
    dig = _digest_bytes
    uni = _uniform_from_digest
    for i in range(iterations):
        if max_found > 0 and len(found) >= max_found:
            break
        nonce = start_nonce + i
        d = dig(header, mix_seed, nonce)
        u = uni(d)
        if u <= cutoff:
            # Difficulty ratio scaled relative to Θ: (-ln u) / Θ
            dr = (-math.log(u)) / max(theta_micro / 1e6, 1e-12)
            found.append(
                {"nonce": nonce, "u": float(u), "d_ratio": float(dr), "hash": d}
            )
    return found


# ────────────────────────────────────────────────────────────────────────
# Backend entrypoints
# ────────────────────────────────────────────────────────────────────────


def list_devices() -> List[DeviceInfo]:
    """Enumerate a single logical CPU device."""
    return [
        DeviceInfo(
            type=getattr(DeviceType, "CPU", "cpu"),
            name="CPU",
            index=0,
            vendor="generic",
            driver="python",
            compute_units=os.cpu_count() or 1,
            memory_bytes=None,
            max_batch=None,
            flags={
                "supports_keccak": True,
                "supports_udraw": True,
                "supports_batch": True,
            },
        )
    ]


def create(**opts: Any) -> _CPUDevice:
    """
    Create a CPU device.

    Options:
      index: int = 0
      threads: int = 0
      batch_size: int = 0
      affinity: Optional[str] = None (Linux)
    """
    return _CPUDevice(**opts)


# ────────────────────────────────────────────────────────────────────────
# Diagnostics
# ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":  # pragma: no cover
    dev = create(threads=0)
    info = dev.info()
    print(
        f"[cpu_backend] Device: {info.name} cu={info.compute_units} driver={info.driver}"
    )
    hdr = b"\x00" * 80
    mix = b"\x11" * 32
    prep = dev.prepare_header(hdr, mix)
    res = dev.scan(
        prep, theta_micro=200000.0, start_nonce=0, iterations=1_000_0, max_found=3
    )
    for r in res:
        print(f"  nonce={r['nonce']} u={r['u']:.6e} d_ratio={r['d_ratio']:.3f}")
