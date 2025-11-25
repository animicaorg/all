"""
vm_py.runtime.syscalls_api — capability shims used by the Python VM runtime.

This module exposes deterministic, bytes-first syscall facades that contracts
*could* call via the stdlib (e.g., blob pin, enqueue AI/Quantum work, read
results, zk.verify). In **local mode** (the default here), these are *no-ops*:
they perform input validation and return deterministic placeholders without
touching the network or filesystem.

A host/node may replace the provider with a real bridge to the host capability
layer (e.g., capabilities/host/provider.py) by calling `set_provider(...)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable, Dict, Any

try:
    # Prefer the project's error type if available.
    from vm_py.errors import VmError
except Exception:  # pragma: no cover
    class VmError(Exception):  # type: ignore
        pass

# Best-effort import of config knobs; fall back to sane defaults.
try:  # pragma: no cover - exercised indirectly
    from vm_py import config as _cfg  # type: ignore
    SYSCALL_INPUT_MAX = int(getattr(_cfg, "SYSCALL_INPUT_MAX", 1 << 20))  # 1 MiB
    SYSCALL_QUEUE_MAX = int(getattr(_cfg, "SYSCALL_QUEUE_MAX", 1024))
except Exception:  # pragma: no cover
    SYSCALL_INPUT_MAX = 1 << 20
    SYSCALL_QUEUE_MAX = 1024

from . import hash_api as _h

# ------------------------------ Helpers & Types ------------------------------ #

def _ensure_bytes(x: object, name: str) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    raise VmError(f"{name} must be bytes-like, got {type(x).__name__}")

def _ensure_int(x: object, name: str, *, min_: int = 0, max_: Optional[int] = None) -> int:
    if not isinstance(x, int):
        raise VmError(f"{name} must be int, got {type(x).__name__}")
    if x < min_:
        raise VmError(f"{name} must be \u2265 {min_} (got {x})")
    if max_ is not None and x > max_:
        raise VmError(f"{name} must be \u2264 {max_} (got {x})")
    return x

def _check_size(buf: bytes, name: str) -> None:
    if len(buf) > SYSCALL_INPUT_MAX:
        raise VmError(f"{name} too large ({len(buf)} bytes > {SYSCALL_INPUT_MAX})")

def _task_id(kind: bytes, *parts: bytes) -> bytes:
    # Deterministic, domain-separated id. Purely local; *not* consensus.
    dom = b"cap/task_id/v0"
    return _h.sha3_256(b"".join([kind, b"|"] + list(parts)), domain=dom)

# ------------------------------ Provider Facade ------------------------------ #

@runtime_checkable
class CapProvider(Protocol):
    """Host capability provider interface (read-only from contracts)."""

    # Data Availability
    def blob_pin(self, namespace: int, data: bytes) -> Dict[str, Any]: ...
    # Compute
    def ai_enqueue(self, model: bytes, prompt: bytes) -> Dict[str, Any]: ...
    def quantum_enqueue(self, circuit: bytes, shots: int) -> Dict[str, Any]: ...
    def read_result(self, task_id: bytes) -> Dict[str, Any]: ...
    # ZK
    def zk_verify(self, circuit: bytes, proof: bytes, public_input: bytes) -> Dict[str, Any]: ...

# ------------------------------- Local No-Op -------------------------------- #

class _LocalNoOpProvider:
    """
    Deterministic, side-effect-free stand-in. It *does not* persist blobs,
    *does not* talk to any queue, and *does not* verify proofs.
    """

    __slots__ = ("_pending",)

    def __init__(self) -> None:
        # task_id -> {"ready": False, "result": None}
        self._pending: Dict[bytes, Dict[str, Any]] = {}

    # ---- DA ----
    def blob_pin(self, namespace: int, data: bytes) -> Dict[str, Any]:
        _ensure_int(namespace, "namespace", min_=0, max_=(1 << 32) - 1)
        data_b = _ensure_bytes(data, "data")
        _check_size(data_b, "data")

        # Local commitment is *not* the NMT root; it is a simple DS hash so callers
        # have a stable, reproducible placeholder during local runs.
        digest = _h.sha3_256(data_b, domain=b"cap/blob_pin/local_stub/ns:%d" % namespace)
        return {
            "namespace": namespace,
            "size": len(data_b),
            "commitment": digest,          # bytes
            "commitment_hex": digest.hex() # convenience for tooling
        }

    # ---- Compute (AI/Quantum) ----
    def ai_enqueue(self, model: bytes, prompt: bytes) -> Dict[str, Any]:
        model_b = _ensure_bytes(model, "model")
        prompt_b = _ensure_bytes(prompt, "prompt")
        _check_size(model_b, "model")
        _check_size(prompt_b, "prompt")

        tid = _task_id(b"ai", model_b, b"|", prompt_b)
        if len(self._pending) >= SYSCALL_QUEUE_MAX:
            raise VmError("capabilities queue full in local mode")
        self._pending[tid] = {"ready": False, "result": None}
        return {"task_id": tid, "task_id_hex": tid.hex(), "accepted": True, "note": "local-noop"}

    def quantum_enqueue(self, circuit: bytes, shots: int) -> Dict[str, Any]:
        circ_b = _ensure_bytes(circuit, "circuit")
        _check_size(circ_b, "circuit")
        _ensure_int(shots, "shots", min_=1, max_=1_000_000)

        tid = _task_id(b"quantum", circ_b, b"|", str(shots).encode())
        if len(self._pending) >= SYSCALL_QUEUE_MAX:
            raise VmError("capabilities queue full in local mode")
        self._pending[tid] = {"ready": False, "result": None}
        return {"task_id": tid, "task_id_hex": tid.hex(), "accepted": True, "note": "local-noop"}

    def read_result(self, task_id: bytes) -> Dict[str, Any]:
        tid = _ensure_bytes(task_id, "task_id")
        # In local mode, results never materialize; callers should treat this as "not ready".
        pending = self._pending.get(tid)
        if pending is None:
            # Return a well-formed miss object rather than erroring.
            return {"found": False, "ready": False, "result": None}
        return {"found": True, "ready": False, "result": None}

    # ---- ZK ----
    def zk_verify(self, circuit: bytes, proof: bytes, public_input: bytes) -> Dict[str, Any]:
        # Local mode *does not* perform cryptographic verification.
        _check_size(_ensure_bytes(circuit, "circuit"), "circuit")
        _check_size(_ensure_bytes(proof, "proof"), "proof")
        _check_size(_ensure_bytes(public_input, "public_input"), "public_input")
        return {"ok": False, "units": 0, "note": "local-noop (no verification performed)"}

# Active provider (mutable via set_provider). Defaults to local no-op.
_provider: CapProvider = _LocalNoOpProvider()

def set_provider(provider: CapProvider) -> None:
    """
    Install a host-backed provider that satisfies `CapProvider`.
    Safe to call multiple times (last-wins).
    """
    global _provider
    if not isinstance(provider, CapProvider):
        # Be friendly to duck-typed implementations: check methods presence.
        required = ["blob_pin", "ai_enqueue", "quantum_enqueue", "read_result", "zk_verify"]
        missing = [m for m in required if not hasattr(provider, m)]
        if missing:
            raise VmError(f"provider missing methods: {', '.join(missing)}")
    _provider = provider  # type: ignore[assignment]

def get_provider() -> CapProvider:
    return _provider

# ------------------------------- Public API --------------------------------- #
# These functions are intentionally thin wrappers that:
#  - Enforce bytes/int preconditions
#  - Delegate to the current provider
#  - Return simple, JSON/CBOR-friendly dicts with bytes for binary fields

def blob_pin(namespace: int, data: bytes) -> Dict[str, Any]:
    """Pin a blob under a namespace. Local mode returns a deterministic placeholder commitment."""
    _ensure_int(namespace, "namespace", min_=0, max_=(1 << 32) - 1)
    return _provider.blob_pin(namespace, _ensure_bytes(data, "data"))

def ai_enqueue(model: bytes, prompt: bytes) -> Dict[str, Any]:
    """Enqueue an AI job. Returns a deterministic task_id. Local mode does not produce results."""
    return _provider.ai_enqueue(_ensure_bytes(model, "model"), _ensure_bytes(prompt, "prompt"))

def quantum_enqueue(circuit: bytes, shots: int) -> Dict[str, Any]:
    """Enqueue a quantum job (trap-circuit descriptor + shot count). Deterministic task_id returned."""
    return _provider.quantum_enqueue(_ensure_bytes(circuit, "circuit"), _ensure_int(shots, "shots", min_=1))

def read_result(task_id: bytes) -> Dict[str, Any]:
    """Read a compute result by task_id. Local mode always returns ready=False."""
    return _provider.read_result(_ensure_bytes(task_id, "task_id"))

def zk_verify(circuit: bytes, proof: bytes, public_input: bytes) -> Dict[str, Any]:
    """
    Verify a zero-knowledge proof. Local mode *never* accepts proofs (ok=False).
    A host provider may return {"ok": True/False, "units": <int>, ...}.
    """
    return _provider.zk_verify(
        _ensure_bytes(circuit, "circuit"),
        _ensure_bytes(proof, "proof"),
        _ensure_bytes(public_input, "public_input"),
    )

__all__ = [
    "CapProvider",
    "set_provider",
    "get_provider",
    "blob_pin",
    "ai_enqueue",
    "quantum_enqueue",
    "read_result",
    "zk_verify",
    "SYSCALL_INPUT_MAX",
    "SYSCALL_QUEUE_MAX",
]
