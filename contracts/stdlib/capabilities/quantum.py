# -*- coding: utf-8 -*-
"""
contracts.stdlib.capabilities.quantum
=====================================

Deterministic helpers for **Quantum** compute requests with trap circuits.

This module provides a small, safe wrapper over the low-level capability
syscalls (host-provided) for enqueueing quantum jobs and consuming results
on the next block. It mirrors the shape and guarantees of the AI helper,
but with quantum-specific parameters (shots, trap ratio).

Typical round-trip
------------------
Block N:
    >>> from contracts.stdlib.capabilities import quantum as qt
    >>> task_id = qt.request(circuit_bytes, shots=256, traps_bps=1000)
    # or persist under a tag for one-shot consumption next block:
    >>> qt.request_store(b"qdemo", circuit_bytes, shots=256, traps_bps=1000)

Block N+1:
    >>> status, output = qt.consume(task_id)
    # or via tag:
    >>> status, output = qt.consume_tag(b"qdemo")

Determinism / semantics
-----------------------
- Results are **not** available in the same block by design (host enforces).
  Attempting to read early reverts with b"CAP:NORESULT".
- Tags are bounded (<= 32 bytes) and saved in a namespaced storage key, then
  cleared after a successful consume (single-use).
- Inputs are strictly validated: type and value ranges are enforced here and in
  the low-level wrapper. Any violation yields crisp revert reasons.

Events (binary names; fields are bytes)
---------------------------------------
- b"CAP:QEnqueued"   — emitted by the low-level wrapper (circuit_hash, shots, traps_bps, task_id)
- b"CAP:QTagSaved"   — {b"tag", b"task_id"}
- b"CAP:QTagCleared" — {b"tag"}
- b"CAP:QConsumed"   — {b"task_id", b"status", b"output_hash", b"size"}

Revert reasons (subset)
-----------------------
- b"CAP:TAG"      — tag invalid (empty/too long)
- b"CAP:NOTASK"   — tag lookup had no task_id
- b"CAP:NORESULT" — result not yet available
- b"CAP:TYPE"     — wrong Python type for argument
- b"CAP:LEN"      — circuit length out of bounds
- b"CAP:PARAM"    — shots/traps_bps outside allowed range
"""
from __future__ import annotations

from typing import Final, Tuple

from stdlib import abi, events
from stdlib import hash as _hash  # type: ignore
from stdlib import storage

# The package-local __init__.py exposes the validated low-level wrappers and
# canonical bounds. These wrappers are deterministic and emit base events.
from . import (  # bounds (provided by lower layer; re-exported below for callers/tests)
    MAX_CIRCUIT_LEN, MAX_SHOTS, MAX_TRAPS_BPS, MIN_SHOTS, MIN_TRAPS_BPS,
    quantum_enqueue, read_result)

# -----------------------------------------------------------------------------
# Bounded constants for tag usage
# -----------------------------------------------------------------------------

TAG_NS_PREFIX: Final[bytes] = b"cap:qt:"
MAX_TAG_LEN: Final[int] = 32  # small, indexer-friendly

# -----------------------------------------------------------------------------
# Internal guards
# -----------------------------------------------------------------------------


def _ensure_bytes(x: object) -> bytes:
    if not isinstance(x, (bytes, bytearray)):
        abi.revert(b"CAP:TYPE")
    return bytes(x)


def _ensure_tag(tag: object) -> bytes:
    t = _ensure_bytes(tag)
    if len(t) == 0 or len(t) > MAX_TAG_LEN:
        abi.revert(b"CAP:TAG")
    return t


def _ensure_int_in_range(name: bytes, val: object, lo: int, hi: int) -> int:
    # Require Python int and exact bounds
    if not isinstance(val, int):
        abi.revert(b"CAP:TYPE")
    if val < lo or val > hi:
        abi.revert(b"CAP:PARAM")
    return val


def _key_for(tag: bytes) -> bytes:
    return TAG_NS_PREFIX + tag


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def request(circuit: bytes, *, shots: int, traps_bps: int) -> bytes:
    """
    Enqueue a Quantum job (circuit + params) and return its deterministic task id.

    Parameters
    ----------
    circuit : bytes
        Serialized circuit description (implementation-defined; e.g., JSON/CBOR)
        with length <= MAX_CIRCUIT_LEN. Content is treated as opaque bytes here.
    shots : int
        Number of measurement shots in [MIN_SHOTS, MAX_SHOTS].
    traps_bps : int
        Trap-circuit ratio in basis points (1/100 of a percent) — must be within
        [MIN_TRAPS_BPS, MAX_TRAPS_BPS]. Example: 1000 = 10%.

    Returns
    -------
    bytes
        task_id assigned by the host (deterministic for the tx).

    Emits (from lower layer)
    ------------------------
    b"CAP:QEnqueued" (circuit_hash, shots, traps_bps, task_id)
    """
    cbytes = _ensure_bytes(circuit)
    # quick length guard (low-level wrapper also enforces)
    if len(cbytes) == 0 or len(cbytes) > int(MAX_CIRCUIT_LEN):
        abi.revert(b"CAP:LEN")
    s = _ensure_int_in_range(b"shots", shots, int(MIN_SHOTS), int(MAX_SHOTS))
    t = _ensure_int_in_range(
        b"traps_bps", traps_bps, int(MIN_TRAPS_BPS), int(MAX_TRAPS_BPS)
    )
    return quantum_enqueue(cbytes, s, t)


def request_store(tag: bytes, circuit: bytes, *, shots: int, traps_bps: int) -> bytes:
    """
    Enqueue a job and persist its task id under a bounded **tag** in storage.

    Use this in block N, then call ``consume_tag(tag)`` in block N+1.

    Emits
    -----
    - b"CAP:QEnqueued" (base layer)
    - b"CAP:QTagSaved" with {b"tag", b"task_id"}
    """
    tbytes = _ensure_tag(tag)
    tid = request(circuit, shots=shots, traps_bps=traps_bps)
    storage.set(_key_for(tbytes), tid)
    events.emit(b"CAP:QTagSaved", {b"tag": tbytes, b"task_id": tid})
    return tid


def load_task_id(tag: bytes) -> bytes:
    """
    Load a previously stored task id for a tag (without consuming).

    Reverts with b"CAP:NOTASK" if the tag does not exist or is empty.
    """
    t = _ensure_tag(tag)
    tid = storage.get(_key_for(t))
    if not isinstance(tid, (bytes, bytearray)) or len(tid) == 0:
        abi.revert(b"CAP:NOTASK")
    return bytes(tid)


def clear_task_id(tag: bytes) -> None:
    """
    Clear a stored task id (best-effort; emits if a record existed).

    Emits b"CAP:QTagCleared" {b"tag"} when a record existed.
    """
    t = _ensure_tag(tag)
    k = _key_for(t)
    tid = storage.get(k)
    if isinstance(tid, (bytes, bytearray)) and len(tid) > 0:
        storage.set(k, b"")
        events.emit(b"CAP:QTagCleared", {b"tag": t})


def consume(task_id: bytes) -> Tuple[bytes, bytes]:
    """
    Read the result for a given task id (block N+1 or later).

    Returns
    -------
    (status, output) : tuple[bytes, bytes]

    Emits
    -----
    b"CAP:QConsumed" {b"task_id", b"status", b"output_hash", b"size"}

    Notes
    -----
    The lower-level read_result may emit a generic capability read event; this
    function adds a Quantum-specific mirror for indexers focused on Quantum jobs.
    """
    tid = _ensure_bytes(task_id)
    status, output = read_result(tid)
    events.emit(
        b"CAP:QConsumed",
        {
            b"task_id": tid,
            b"status": status,
            b"output_hash": _hash.sha3_256(output),
            b"size": len(output).to_bytes(4, "big"),
        },
    )
    return (status, output)


def consume_tag(tag: bytes) -> Tuple[bytes, bytes]:
    """
    Load the task id for ``tag``, read the result, and clear the tag.

    Reverts
    -------
    - b"CAP:NOTASK" if no task id stored under the tag.
    - b"CAP:NORESULT" if the result is not yet available.
    """
    t = _ensure_tag(tag)
    k = _key_for(t)
    tid = storage.get(k)
    if not isinstance(tid, (bytes, bytearray)) or len(tid) == 0:
        abi.revert(b"CAP:NOTASK")
    status, output = consume(bytes(tid))
    storage.set(k, b"")  # clear after successful consume
    events.emit(b"CAP:QTagCleared", {b"tag": t})
    return (status, output)


# -----------------------------------------------------------------------------
# Re-exports of bounds for callers/tests
# -----------------------------------------------------------------------------

__all__ = [
    "request",
    "request_store",
    "load_task_id",
    "clear_task_id",
    "consume",
    "consume_tag",
    # bounds re-exports for tests and contracts to self-validate inputs
    "MAX_TAG_LEN",
    "MAX_CIRCUIT_LEN",
    "MIN_SHOTS",
    "MAX_SHOTS",
    "MIN_TRAPS_BPS",
    "MAX_TRAPS_BPS",
]
