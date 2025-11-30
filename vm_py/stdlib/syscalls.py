"""
Deterministic syscall stubs available to contracts.

These wrappers call into the VM runtime's syscall layer
(:mod:`vm_py.runtime.syscalls_api`). In local/dev modes they resolve to
no-ops or deterministic simulators; in a full node they bridge to the host
capability provider (DA, AICF, zk, randomness).

Exposed surface (stable, minimal):
    - blob_pin(ns, data) -> bytes
    - ai_enqueue(model, prompt) -> bytes
    - quantum_enqueue(circuit, shots=256) -> bytes
    - read_result(task_id) -> bytes | None
    - zk_verify(circuit, proof, public_input) -> bool
    - random_bytes(n) -> bytes

All functions validate basic types and sizes to keep contract code simple and
defensive. More detailed policy/limit checks happen inside the runtime/host.
"""

from __future__ import annotations

from typing import Optional, Union

from vm_py.runtime import syscalls_api as _sys

BytesLike = Union[bytes, bytearray, memoryview]
StrOrBytes = Union[str, BytesLike]


# ------------------------------- helpers ------------------------------------


def _to_bytes(name: str, val: StrOrBytes) -> bytes:
    """Accept bytes-like or str; encode str as UTF-8."""
    if isinstance(val, str):
        return val.encode("utf-8")
    if isinstance(val, bytes):
        return val
    if isinstance(val, (bytearray, memoryview)):
        return bytes(val)
    raise TypeError(f"{name} must be bytes-like or str, got {type(val).__name__}")


def _ns_to_bytes(ns: Union[int, BytesLike]) -> bytes:
    """
    Normalize a namespace id to bytes.

    Accepts:
        • int in range [0, 2^32-1] → 4-byte big-endian
        • bytes-like (already-encoded namespace id)
    """
    if isinstance(ns, int):
        if ns < 0 or ns > 0xFFFFFFFF:
            raise ValueError("namespace int must be in range [0, 2^32-1]")
        return ns.to_bytes(4, "big")
    if isinstance(ns, bytes):
        if len(ns) == 0:
            raise ValueError("namespace bytes must not be empty")
        return ns
    if isinstance(ns, (bytearray, memoryview)):
        b = bytes(ns)
        if len(b) == 0:
            raise ValueError("namespace bytes must not be empty")
        return b
    raise TypeError(f"ns must be int or bytes-like, got {type(ns).__name__}")


def _ensure_non_negative(name: str, n: int) -> None:
    if not isinstance(n, int):
        raise TypeError(f"{name} must be int, got {type(n).__name__}")
    if n < 0:
        raise ValueError(f"{name} must be non-negative")


# -------------------------------- DA ----------------------------------------


def blob_pin(ns: Union[int, BytesLike], data: BytesLike) -> bytes:
    """
    Pin a blob under a namespace, returning its commitment (NMT root bytes).

    Args:
        ns: namespace id (int → 4-byte BE) or bytes-like domain id.
        data: raw blob bytes.

    Returns:
        Commitment bytes (implementation-defined length; typically 32).
    """
    ns_b = _ns_to_bytes(ns)
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError(f"data must be bytes-like, got {type(data).__name__}")
    data_b = bytes(data)
    if len(data_b) == 0:
        raise ValueError("data must not be empty")
    return _sys.blob_pin(ns_b, data_b)


# ------------------------------ Compute -------------------------------------


def ai_enqueue(model: StrOrBytes, prompt: StrOrBytes) -> bytes:
    """
    Enqueue an AI job. Returns a deterministic task_id (bytes).

    Args:
        model: identifier string or bytes (e.g., b"animica/tiny-v1").
        prompt: request payload (bytes or UTF-8 string).

    Returns:
        task_id bytes (stable across identical inputs by design).
    """
    model_b = _to_bytes("model", model)
    prompt_b = _to_bytes("prompt", prompt)
    if len(model_b) == 0:
        raise ValueError("model must not be empty")
    if len(prompt_b) == 0:
        raise ValueError("prompt must not be empty")
    return _sys.ai_enqueue(model_b, prompt_b)


def quantum_enqueue(circuit: StrOrBytes, shots: int = 256) -> bytes:
    """
    Enqueue a Quantum job. Returns a deterministic task_id (bytes).

    Args:
        circuit: serialized circuit description (bytes or UTF-8 string).
        shots: number of shots/samples to request (non-negative).

    Returns:
        task_id bytes.
    """
    circuit_b = _to_bytes("circuit", circuit)
    _ensure_non_negative("shots", shots)
    if len(circuit_b) == 0:
        raise ValueError("circuit must not be empty")
    return _sys.quantum_enqueue(circuit_b, shots)


def read_result(task_id: BytesLike) -> Optional[bytes]:
    """
    Read a completed job result (if available) for the given task_id.

    Deterministic semantics: results become readable in the block following
    successful proof inclusion; otherwise None is returned.

    Args:
        task_id: bytes-like identifier returned by enqueue.

    Returns:
        result bytes, or None if not yet available.
    """
    if not isinstance(task_id, (bytes, bytearray, memoryview)):
        raise TypeError(f"task_id must be bytes-like, got {type(task_id).__name__}")
    tid = bytes(task_id)
    if len(tid) == 0:
        raise ValueError("task_id must not be empty")
    return _sys.read_result(tid)


# --------------------------------- zk ---------------------------------------


def zk_verify(circuit: StrOrBytes, proof: BytesLike, public_input: StrOrBytes) -> bool:
    """
    Verify a zk proof against a circuit and public input.

    Args:
        circuit: serialized circuit/program (bytes or UTF-8 string).
        proof: proof bytes.
        public_input: serialized public input (bytes or UTF-8 string).

    Returns:
        True if verification succeeds, False otherwise.
    """
    circ_b = _to_bytes("circuit", circuit)
    pub_b = _to_bytes("public_input", public_input)
    if not isinstance(proof, (bytes, bytearray, memoryview)):
        raise TypeError(f"proof must be bytes-like, got {type(proof).__name__}")
    proof_b = bytes(proof)
    if len(circ_b) == 0:
        raise ValueError("circuit must not be empty")
    if len(proof_b) == 0:
        raise ValueError("proof must not be empty")
    return _sys.zk_verify(circ_b, proof_b, pub_b)


# ------------------------------- randomness ---------------------------------


def random_bytes(n: int) -> bytes:
    """
    Deterministic random bytes (contract-visible): seeded from tx/chain context.
    In production this may be mixed with a beacon when available, but remains
    deterministic per the runtime's transcript.

    Args:
        n: number of bytes to return (non-negative).

    Returns:
        bytes of length n.
    """
    _ensure_non_negative("n", n)
    return _sys.random_bytes(n)


__all__ = (
    "blob_pin",
    "ai_enqueue",
    "quantum_enqueue",
    "read_result",
    "zk_verify",
    "random_bytes",
)
