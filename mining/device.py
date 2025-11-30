from __future__ import annotations

"""
Animica mining.device
=====================

Unified device abstraction for useful-work hash scanning. Supports:
  - cpu     : pure-Python/NumPy/Numba backend (always available)
  - cuda    : NVIDIA CUDA kernels (optional)
  - rocm    : AMD ROCm HIP kernels (optional)
  - opencl  : Portable OpenCL kernels (optional)
  - metal   : Apple Metal Performance Shaders kernels (optional)

This module *does not* implement the inner loops; it discovers and loads
backend adapters (see mining/cpu_backend.py, mining/gpu_cuda.py, etc.) and
exposes a consistent API to the rest of the miner (orchestrator/search loop).

Design goals
------------
- Deterministic outputs (given the same header/mixSeed/startNonce/iterations).
- Safe feature-gating: GPU backends are optional and imported lazily.
- Clear capability flags for scheduling and tuning.
- No side effects at import time; detection happens on first use.

Public API
----------
- DeviceType(Enum-like)                      : identifiers for backends
- DeviceInfo(dataclass)                      : static info + feature flags
- MiningDevice(Protocol)                     : runtime interface
- list_available() -> list[DeviceInfo]       : enumerate devices across backends
- create(device: str|DeviceType, **opts)     : instantiate a MiningDevice
- prefer(order: list[str|DeviceType])        : helper to pick best available
"""

import importlib
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple, Union

# ────────────────────────────────────────────────────────────────────────
# Errors (local import if available)
# ────────────────────────────────────────────────────────────────────────

try:
    from .errors import DeviceUnavailable, MinerError
except Exception:  # pragma: no cover - keep standalone

    class MinerError(RuntimeError):
        pass

    class DeviceUnavailable(MinerError):
        pass


# ────────────────────────────────────────────────────────────────────────
# Types & Flags
# ────────────────────────────────────────────────────────────────────────


class DeviceType(str):
    CPU = "cpu"
    CUDA = "cuda"
    ROCM = "rocm"
    OPENCL = "opencl"
    METAL = "metal"

    @classmethod
    def normalize(cls, x: Union[str, "DeviceType"]) -> "DeviceType":
        s = str(x).strip().lower()
        if s in {cls.CPU, "host"}:
            return cls.CPU
        if s in {cls.CUDA, "nvidia", "nv"}:
            return cls.CUDA
        if s in {cls.ROCM, "amd", "hip"}:
            return cls.ROCM
        if s in {cls.OPENCL, "ocl"}:
            return cls.OPENCL
        if s in {cls.METAL, "mps"}:
            return cls.METAL
        raise ValueError(f"Unknown device type: {x!r}")


@dataclass(frozen=True)
class DeviceInfo:
    """Static description of a physical/logical device."""

    type: DeviceType
    name: str
    index: int = 0
    vendor: Optional[str] = None
    driver: Optional[str] = None
    compute_units: Optional[int] = None
    memory_bytes: Optional[int] = None
    max_batch: Optional[int] = None
    flags: Dict[str, bool] = field(
        default_factory=dict
    )  # e.g., supports_keccak, supports_udraw

    def id(self) -> str:
        return f"{self.type}:{self.index}"

    def has(self, flag: str) -> bool:
        return bool(self.flags.get(flag, False))


class MiningDevice(Protocol):
    """Runtime interface provided by each backend instance."""

    def info(self) -> DeviceInfo: ...

    def prepare_header(self, header_bytes: bytes, mix_seed: bytes) -> Any:
        """
        Backend-specific preprocessing for the current header template.
        Returns an opaque handle to be used with `scan`.
        Implementations must treat inputs as immutable.
        """
        ...

    def scan(
        self,
        prepared: Any,
        *,
        theta_micro: float,
        start_nonce: int,
        iterations: int,
        max_found: int = 1,
        thread_id: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Search a contiguous nonce range and return up to `max_found` shares.
        Each returned dict *must* include at least:
          - "nonce"      : int
          - "u"          : float  (raw uniform draw in (0,1], before -ln)
          - "d_ratio"    : float  (share difficulty / target)
          - "hash"       : bytes  (digest binding header+nonce)
        Backends may also include extras (e.g., lanes, cycles).
        """
        ...

    def close(self) -> None:
        """Release device resources (contexts/queues)."""
        ...


# ────────────────────────────────────────────────────────────────────────
# Backend registry & lazy loaders
# ────────────────────────────────────────────────────────────────────────

_loader_lock = threading.Lock()
_registry: Dict[str, Dict[str, Any]] = (
    {}
)  # {backend_name: {"mod": module, "list": fn, "create": fn}}


def _try_load(backend: str, module_name: str, list_fn: str, create_fn: str) -> None:
    """Best-effort import; record presence in _registry."""
    with _loader_lock:
        if backend in _registry:  # already attempted
            return
        try:
            mod = importlib.import_module(module_name)
            lfn = getattr(mod, list_fn)
            cfn = getattr(mod, create_fn)
            _registry[backend] = {"mod": mod, "list": lfn, "create": cfn}
        except Exception:
            _registry[backend] = {}  # mark as unavailable


def _boot_registry() -> None:
    # CPU is always present (pure-Python fallback)
    _try_load(DeviceType.CPU, "mining.cpu_backend", "list_devices", "create")
    # Optional GPU/accelerated backends (guarded)
    _try_load(DeviceType.CUDA, "mining.gpu_cuda", "list_devices", "create")
    _try_load(
        DeviceType.ROCM, "mining.gpu_rocm", "list_devices", "create"
    )  # optional future module
    _try_load(DeviceType.OPENCL, "mining.gpu_opencl", "list_devices", "create")
    _try_load(DeviceType.METAL, "mining.gpu_metal", "list_devices", "create")


def _available(backend: str) -> bool:
    _boot_registry()
    ent = _registry.get(backend) or {}
    return (
        "list" in ent
        and callable(ent["list"])
        and "create" in ent
        and callable(ent["create"])
    )


# ────────────────────────────────────────────────────────────────────────
# Discovery
# ────────────────────────────────────────────────────────────────────────


def list_available() -> List[DeviceInfo]:
    """
    Enumerate devices across all discovered backends.
    Honors environment filters:
      ANIMICA_DEVICE_ALLOW = "cuda,opencl,cpu" (only these)
      ANIMICA_DEVICE_DENY  = "metal"           (exclude these)
    """
    _boot_registry()
    allow = _parse_csv_env("ANIMICA_DEVICE_ALLOW")
    deny = _parse_csv_env("ANIMICA_DEVICE_DENY")
    out: List[DeviceInfo] = []
    for backend, ent in _registry.items():
        if not ent:
            continue
        if allow and backend not in allow:
            continue
        if deny and backend in deny:
            continue
        try:
            devices = ent["list"]()  # returns Iterable[DeviceInfo]
            for d in devices:
                # Normalize type & add default flags for expectations
                flags = dict(
                    {
                        "supports_keccak": True,
                        "supports_udraw": True,
                        "supports_batch": True,
                    },
                    **(d.flags or {}),
                )
                out.append(
                    DeviceInfo(
                        type=DeviceType.normalize(d.type),
                        name=d.name,
                        index=d.index,
                        vendor=d.vendor,
                        driver=d.driver,
                        compute_units=d.compute_units,
                        memory_bytes=d.memory_bytes,
                        max_batch=d.max_batch,
                        flags=flags,
                    )
                )
        except Exception:
            # If a backend fails enumeration, skip it gracefully
            continue
    # Ensure at least one CPU info even if the cpu backend didn't provide list()
    if not any(d.type == DeviceType.CPU for d in out) and _available(DeviceType.CPU):
        out.append(
            DeviceInfo(
                type=DeviceType.CPU,
                name="CPU (fallback)",
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
        )
    # Stable deterministic order: type→index→name
    out.sort(key=lambda d: (d.type, d.index, d.name))
    return out


# ────────────────────────────────────────────────────────────────────────
# Factory
# ────────────────────────────────────────────────────────────────────────


def create(device: Union[str, DeviceType], **opts: Any) -> MiningDevice:
    """
    Instantiate a MiningDevice for the requested backend.

    Common options:
      index: int = 0
      threads: int = 0           # cpu: worker threads; gpu: streams/queues
      batch_size: int = 0        # hint for kernel launch size
      pinned_mem: bool = False   # gpu: use pinned host buffers
      affinity: Optional[str]    # cpu: e.g., "0-7,16-23"
    """
    backend = DeviceType.normalize(device)
    _boot_registry()
    ent = _registry.get(backend) or {}
    if not ent:
        raise DeviceUnavailable(
            f"Backend '{backend}' not available (module missing or failed to import)."
        )
    try:
        dev = ent["create"](**opts)
        # Quick sanity check
        _ = dev.info()
        _validate_device_interface(dev)
        return dev
    except DeviceUnavailable:
        raise
    except Exception as e:
        raise DeviceUnavailable(f"Failed to create device '{backend}': {e}") from e


def prefer(order: Iterable[Union[str, DeviceType]], **opts: Any) -> MiningDevice:
    """
    Pick the first available backend from `order`.
    Example: prefer([DeviceType.CUDA, DeviceType.OPENCL, DeviceType.CPU], index=0)
    """
    last_err: Optional[Exception] = None
    for b in order:
        try:
            return create(b, **opts)
        except Exception as e:  # pragma: no cover
            last_err = e
            continue
    if last_err:
        raise last_err
    raise DeviceUnavailable("No devices matched the requested preference order.")


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────


def _parse_csv_env(key: str) -> List[str]:
    v = os.environ.get(key, "").strip()
    if not v:
        return []
    return [DeviceType.normalize(x) for x in re.split(r"[,\s]+", v) if x]


def _validate_device_interface(dev: MiningDevice) -> None:
    # Lightweight check that required methods exist
    for m in ("info", "prepare_header", "scan", "close"):
        if not hasattr(dev, m):
            raise DeviceUnavailable(f"Device missing method: {m}")


# ────────────────────────────────────────────────────────────────────────
# Fallback: lightweight CPU shim if mining.cpu_backend isn't present yet
# ────────────────────────────────────────────────────────────────────────

if not _available(DeviceType.CPU):
    # Provide an ultra-minimal CPU backend so other components can be imported.
    # This shim is correct but not fast; the real cpu_backend.py will override it.
    import hashlib
    import math
    from typing import NamedTuple

    class _ShimWork(NamedTuple):
        header: bytes
        mix: bytes

    class _ShimCPU:
        def __init__(self, index: int = 0, threads: int = 0, **_: Any) -> None:
            self._info = DeviceInfo(
                type=DeviceType.CPU,
                name="CPU (shim)",
                index=index,
                vendor="generic",
                driver="python",
                compute_units=os.cpu_count() or 1,
                memory_bytes=None,
                max_batch=1,
                flags={
                    "supports_keccak": True,
                    "supports_udraw": True,
                    "supports_batch": False,
                },
            )

        def info(self) -> DeviceInfo:
            return self._info

        def prepare_header(self, header_bytes: bytes, mix_seed: bytes) -> _ShimWork:
            return _ShimWork(header=bytes(header_bytes), mix=bytes(mix_seed))

        def scan(
            self,
            prepared: _ShimWork,
            *,
            theta_micro: float,
            start_nonce: int,
            iterations: int,
            max_found: int = 1,
            thread_id: int = 0,
        ) -> List[Dict[str, Any]]:
            found: List[Dict[str, Any]] = []
            # Very slow reference: hash = sha3_256(header || mix || nonce_le8)
            # u = (int(hash[:16]) + 1) / 2**128  (toy u-draw) → d_ratio via -ln(u)
            # Accept share if -ln(u) * 1e6 >= theta_micro (i.e., u <= exp(-Θ))
            exp_neg_theta = math.exp(-theta_micro / 1e6)
            h, m = prepared.header, prepared.mix
            for i in range(iterations):
                if len(found) >= max_found:
                    break
                nonce = start_nonce + i
                n8 = nonce.to_bytes(8, "little")
                digest = hashlib.sha3_256(h + m + n8).digest()
                # Map to uniform (0,1]: take first 16 bytes as big int
                u_num = int.from_bytes(digest[:16], "big") + 1
                u_den = 1 << 128
                u = u_num / u_den
                if u <= exp_neg_theta:
                    # d_ratio is proportional to -ln(u) / (-ln(exp(-Θ))) ~= (-ln u) / Θ
                    d_ratio = (-math.log(u)) / max(theta_micro / 1e6, 1e-9)
                    found.append(
                        {
                            "nonce": nonce,
                            "u": float(u),
                            "d_ratio": float(d_ratio),
                            "hash": digest,
                        }
                    )
            return found

        def close(self) -> None:
            return None

    # Register shim
    def _shim_list_devices() -> List[DeviceInfo]:
        return [_ShimCPU().info()]

    def _shim_create(**opts: Any) -> MiningDevice:
        return _ShimCPU(**opts)

    _registry[DeviceType.CPU] = {
        "mod": None,
        "list": _shim_list_devices,
        "create": _shim_create,
    }

# ────────────────────────────────────────────────────────────────────────
# __main__ (diagnostics)
# ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":  # pragma: no cover
    import json

    devs = list_available()
    print(
        json.dumps(
            [
                {
                    "id": d.id(),
                    "type": d.type,
                    "name": d.name,
                    "vendor": d.vendor,
                    "driver": d.driver,
                    "compute_units": d.compute_units,
                    "memory_bytes": d.memory_bytes,
                    "max_batch": d.max_batch,
                    "flags": d.flags,
                }
                for d in devs
            ],
            indent=2,
        )
    )
