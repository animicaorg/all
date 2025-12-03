"""
Minimal Data Availability capability demo.

Exposes a single function that pins a blob to the DA layer via stdlib.syscalls
and returns the deterministic commitment bytes. An event is emitted so tooling
can observe the pin operation in logs.
"""
from typing import Final

from stdlib import abi, events, syscalls

# Deterministic default namespace for the demo; callers can override via args.
DEFAULT_NAMESPACE: Final[int] = 0xA11CE5


def _ensure_namespace(ns: int) -> int:
    abi.require(isinstance(ns, int), b"pinner: namespace must be int")
    abi.require(0 <= ns <= 0xFFFFFFFF, b"pinner: namespace out of range")
    return ns


def _ensure_payload(data: bytes) -> bytes:
    abi.require(
        isinstance(data, (bytes, bytearray, memoryview)),
        b"pinner: data must be bytes-like",
    )
    data_b = bytes(data)
    abi.require(len(data_b) > 0, b"pinner: payload must not be empty")
    return data_b


def pin(data: bytes, *, namespace: int = DEFAULT_NAMESPACE) -> bytes:
    """
    Pin a blob using the DA capability and return its commitment bytes.

    Args:
        data: payload to pin (bytes-like, non-empty).
        namespace: 32-bit unsigned namespace id; defaults to DEFAULT_NAMESPACE.

    Returns:
        Commitment bytes provided by the capability provider.
    """
    ns = _ensure_namespace(namespace)
    payload = _ensure_payload(data)

    result = syscalls.blob_pin(ns, payload)
    commitment = result.get("commitment") if isinstance(result, dict) else result

    events.emit(
        b"Pinner.BlobPinned",
        {b"ns": ns, b"size": len(payload), b"commitment": commitment},
    )
    return commitment


def pin_with_namespace(namespace: int, data: bytes) -> bytes:
    """Explicit-namespace wrapper that forwards to :func:`pin`."""
    return pin(data, namespace=namespace)
