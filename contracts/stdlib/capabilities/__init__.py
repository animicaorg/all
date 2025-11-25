# -*- coding: utf-8 -*-
"""
contracts.stdlib.capabilities
=============================

Deterministic, contract-friendly wrappers around VM syscalls that interface
with **capabilities/** (blob pinning, AI/Quantum off-chain compute, zk.verify,
deterministic random). These helpers normalize inputs, enforce conservative
length caps, emit consistent events, and surface crisp revert reasons.

Design goals
------------
- **Determinism-first**: no ambient state; all effects are explicit (events,
  receipts via next-block result reads, content-addressed commits).
- **Tight guards**: length/shape validation before invoking the host to avoid
  unnecessary syscall work and guarantee clear failure codes.
- **Predictable events**: stable binary event names and fields for indexers.

Syscall mapping
---------------
- ``blob_pin(ns: int, data: bytes) -> bytes`` → commitment (NMT/DA root)
- ``ai_enqueue(model: bytes, prompt: bytes) -> bytes`` → task_id
- ``quantum_enqueue(circuit: bytes, shots: int) -> bytes`` → task_id
- ``read_result(task_id: bytes) -> tuple[bytes, bytes]`` → (status, output)
- ``zk_verify(circuit: bytes, proof: bytes, public: bytes) -> bool``
- ``random_bytes(n: int) -> bytes``

Events
------
- ``b"CAP:BlobPinned"      {ns, size, commitment}``
- ``b"CAP:AIEnqueued"      {model, prompt_hash, task_id}``
- ``b"CAP:QuantumEnqueued" {circuit_hash, shots, task_id}``
- ``b"CAP:ResultRead"      {task_id, status, output_hash, size}``

Revert reasons (subset)
-----------------------
- ``b"CAP:LEN"`` (payload length invalid)
- ``b"CAP:TYPE"`` (wrong type)
- ``b"CAP:RANGE"`` (numeric out of range)
- ``b"CAP:EMPTY"`` (empty where disallowed)
- ``b"CAP:NORESULT"`` (read_result with no record yet)

Notes
-----
Authorization/economic policy is a higher-layer concern. These helpers do not
charge gas beyond the VM's own metering and merely forward to host providers.
"""
from __future__ import annotations

from typing import Final, Tuple

from stdlib import abi, events, hash as _hash, storage  # type: ignore
from stdlib import syscalls as _sys                     # type: ignore

# -----------------------------------------------------------------------------
# Bounds & constants (chosen to be safely below host/provider maxima)
# -----------------------------------------------------------------------------

# Blob pinning
MAX_BLOB_SIZE: Final[int] = 1_000_000  # ~1 MB per call (demo/devnet-friendly)
MAX_NAMESPACE: Final[int] = (1 << 32) - 1

# AI
MAX_MODEL_LEN: Final[int] = 64
MAX_PROMPT_LEN: Final[int] = 8 * 1024  # 8 KiB
MIN_PROMPT_LEN: Final[int] = 1

# Quantum
MAX_CIRCUIT_LEN: Final[int] = 12 * 1024  # 12 KiB JSON-ish payload
MAX_SHOTS: Final[int] = 4096
MIN_SHOTS: Final[int] = 1

# zk
MAX_ZK_CIRCUIT_LEN: Final[int] = 32 * 1024
MAX_ZK_PROOF_LEN: Final[int] = 32 * 1024
MAX_ZK_PUBLIC_LEN: Final[int] = 8 * 1024

# Random
MAX_RANDOM_BYTES: Final[int] = 4096

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _ensure_bytes(x: object) -> bytes:
    if not isinstance(x, (bytes, bytearray)):
        abi.revert(b"CAP:TYPE")
    return bytes(x)


def _ensure_nonempty(b: bytes) -> None:
    if len(b) == 0:
        abi.revert(b"CAP:EMPTY")


def _ensure_len_at_most(b: bytes, n: int) -> None:
    if len(b) > n:
        abi.revert(b"CAP:LEN")


def _ensure_positive(n: int) -> None:
    if not isinstance(n, int) or n <= 0:
        abi.revert(b"CAP:RANGE")


def _ensure_range(n: int, lo: int, hi: int) -> None:
    if not isinstance(n, int) or n < lo or n > hi:
        abi.revert(b"CAP:RANGE")


def _h256(b: bytes) -> bytes:
    return _hash.sha3_256(b)


# -----------------------------------------------------------------------------
# Blob pinning
# -----------------------------------------------------------------------------

def blob_pin(ns: int, data: bytes) -> bytes:
    """
    Pin a blob (content-addressed) under a numeric namespace.

    Parameters
    ----------
    ns : int
        Namespace id (0..2^32-1). Higher-level policy defines allowed ranges.
    data : bytes
        Payload to pin (<= MAX_BLOB_SIZE).

    Returns
    -------
    bytes
        Commitment / NMT root as produced by the host.

    Emits
    -----
    b"CAP:BlobPinned" with fields {b"ns", b"size", b"commitment"}.
    """
    _ensure_range(ns, 0, MAX_NAMESPACE)
    data_b = _ensure_bytes(data)
    _ensure_len_at_most(data_b, MAX_BLOB_SIZE)
    _ensure_nonempty(data_b)

    commit = _sys.blob_pin(ns, data_b)  # type: ignore[attr-defined]
    events.emit(b"CAP:BlobPinned", {b"ns": ns.to_bytes(4, "big"), b"size": len(data_b).to_bytes(4, "big"), b"commitment": _ensure_bytes(commit)})
    return _ensure_bytes(commit)


# -----------------------------------------------------------------------------
# AI enqueue
# -----------------------------------------------------------------------------

def ai_enqueue(model: bytes, prompt: bytes) -> bytes:
    """
    Enqueue an AI job.

    Model id and prompt are free-form bytes but capped for determinism.

    Returns
    -------
    bytes
        Deterministic task_id assigned by the host.

    Emits
    -----
    b"CAP:AIEnqueued" {model, prompt_hash, task_id}
    """
    m = _ensure_bytes(model)
    p = _ensure_bytes(prompt)
    _ensure_nonempty(m)
    _ensure_nonempty(p)
    _ensure_len_at_most(m, MAX_MODEL_LEN)
    _ensure_range(len(p), MIN_PROMPT_LEN, MAX_PROMPT_LEN)

    task_id = _sys.ai_enqueue(m, p)  # type: ignore[attr-defined]
    tid = _ensure_bytes(task_id)
    events.emit(b"CAP:AIEnqueued", {b"model": m, b"prompt_hash": _h256(p), b"task_id": tid})
    return tid


# -----------------------------------------------------------------------------
# Quantum enqueue
# -----------------------------------------------------------------------------

def quantum_enqueue(circuit: bytes, shots: int) -> bytes:
    """
    Enqueue a quantum job with a (JSON-encoded) circuit and shot count.

    Returns
    -------
    bytes
        Deterministic task_id.

    Emits
    -----
    b"CAP:QuantumEnqueued" {circuit_hash, shots, task_id}
    """
    c = _ensure_bytes(circuit)
    _ensure_nonempty(c)
    _ensure_len_at_most(c, MAX_CIRCUIT_LEN)
    _ensure_range(shots, MIN_SHOTS, MAX_SHOTS)

    task_id = _sys.quantum_enqueue(c, shots)  # type: ignore[attr-defined]
    tid = _ensure_bytes(task_id)
    events.emit(b"CAP:QuantumEnqueued", {b"circuit_hash": _h256(c), b"shots": shots.to_bytes(4, "big"), b"task_id": tid})
    return tid


# -----------------------------------------------------------------------------
# Results (read-only, deterministic)
# -----------------------------------------------------------------------------

def read_result(task_id: bytes) -> Tuple[bytes, bytes]:
    """
    Read a result for a previously enqueued task.

    The host enforces next-block availability semantics; if the result is not
    present yet, this call **reverts**.

    Returns
    -------
    (status, output) : tuple[bytes, bytes]
        Status is an implementation-defined tag (e.g., b"OK", b"ERR:<code>").

    Emits
    -----
    b"CAP:ResultRead" {task_id, status, output_hash, size}

    Reverts
    -------
    b"CAP:NORESULT" if no record exists yet for the task_id.
    """
    tid = _ensure_bytes(task_id)
    _ensure_nonempty(tid)

    status, output = _sys.read_result(tid)  # type: ignore[attr-defined]
    s = _ensure_bytes(status)
    o = _ensure_bytes(output)
    if len(s) == 0:
        abi.revert(b"CAP:NORESULT")

    events.emit(b"CAP:ResultRead", {b"task_id": tid, b"status": s, b"output_hash": _h256(o), b"size": len(o).to_bytes(4, "big")})
    return (s, o)


# -----------------------------------------------------------------------------
# zk.verify
# -----------------------------------------------------------------------------

def zk_verify(circuit: bytes, proof: bytes, public: bytes) -> bool:
    """
    Verify a zero-knowledge proof.

    Returns
    -------
    bool
        True if verification succeeds, else False.
    """
    c = _ensure_bytes(circuit)
    pr = _ensure_bytes(proof)
    pu = _ensure_bytes(public)

    _ensure_len_at_most(c, MAX_ZK_CIRCUIT_LEN)
    _ensure_len_at_most(pr, MAX_ZK_PROOF_LEN)
    _ensure_len_at_most(pu, MAX_ZK_PUBLIC_LEN)

    return bool(_sys.zk_verify(c, pr, pu))  # type: ignore[attr-defined]


# -----------------------------------------------------------------------------
# Random
# -----------------------------------------------------------------------------

def random_bytes(n: int) -> bytes:
    """
    Deterministic random bytes source.

    The host may later mix in beacon entropy in a **predictable** transcript,
    but the output is stable for a given tx/seed as per VM determinism rules.
    """
    _ensure_range(n, 1, MAX_RANDOM_BYTES)
    return _ensure_bytes(_sys.random(n))  # type: ignore[attr-defined]


__all__ = [
    # blob
    "blob_pin",
    # AI
    "ai_enqueue",
    # Quantum
    "quantum_enqueue",
    # Results
    "read_result",
    # zk
    "zk_verify",
    # Random
    "random_bytes",
    # bounds (exported for library users/tests)
    "MAX_BLOB_SIZE",
    "MAX_NAMESPACE",
    "MAX_MODEL_LEN",
    "MAX_PROMPT_LEN",
    "MIN_PROMPT_LEN",
    "MAX_CIRCUIT_LEN",
    "MAX_SHOTS",
    "MIN_SHOTS",
    "MAX_ZK_CIRCUIT_LEN",
    "MAX_ZK_PROOF_LEN",
    "MAX_ZK_PUBLIC_LEN",
    "MAX_RANDOM_BYTES",
]
