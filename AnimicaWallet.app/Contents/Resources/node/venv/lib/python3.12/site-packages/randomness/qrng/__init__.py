"""
randomness.qrng
===============

Package marker and minimal registry for optional QRNG (Quantum RNG) backends.

⚠️  Non-consensus: Any bytes produced via this module MUST NOT feed directly
into consensus-critical paths. If mixed into beacons or protocols, it must be
strictly through non-consensus, operator-local tooling or via an on-chain rule
that treats QRNG input as optional/advisory only.

This module exposes a tiny pluggable interface so downstream code can register
a concrete QRNG backend (USB device, PCIe card, network service, etc.) and use
it in a controlled manner.

Typical usage
-------------
    from randomness.qrng import register_backend, use_backend, random_bytes

    class MyQRNG:
        def random_bytes(self, n: int) -> bytes:
            # return n bytes from your device/service
            ...

    register_backend("myqrng", MyQRNG())
    use_backend("myqrng")
    b = random_bytes(32)

If no backend is selected, calls to `random_bytes` raise `QRNGNotAvailable`.
"""

from __future__ import annotations

from typing import Dict, Optional, Protocol

# --- Public protocol & error -----------------------------------------------------


class EntropySource(Protocol):
    """Minimal QRNG source protocol."""

    def random_bytes(self, n: int) -> bytes:  # pragma: no cover - protocol
        """Return exactly n bytes of entropy, or raise on failure."""
        ...


class QRNGNotAvailable(RuntimeError):
    """Raised when a QRNG call is attempted without an active backend."""


# --- Simple registry -------------------------------------------------------------

_registry: Dict[str, EntropySource] = {}
_current_name: Optional[str] = None


def register_backend(name: str, source: EntropySource) -> None:
    """
    Register a QRNG backend instance under a name.

    Re-registering the same name replaces the previous instance.
    """
    if not name or not isinstance(name, str):
        raise ValueError("backend name must be a non-empty string")
    _registry[name] = source


def use_backend(name: Optional[str]) -> None:
    """
    Select the active backend by name. Pass None to clear selection.
    """
    global _current_name
    if name is None:
        _current_name = None
        return
    if name not in _registry:
        raise KeyError(f"QRNG backend '{name}' is not registered")
    _current_name = name


def current_backend() -> Optional[EntropySource]:
    """Return the currently selected backend instance, or None if unset."""
    if _current_name is None:
        return None
    return _registry.get(_current_name)


# --- Convenience API -------------------------------------------------------------


def random_bytes(n: int) -> bytes:
    """
    Fetch n bytes from the active QRNG backend.

    Raises:
        QRNGNotAvailable if no backend is selected.
        Any backend-specific error on failure.
    """
    src = current_backend()
    if src is None:
        raise QRNGNotAvailable("No QRNG backend selected; call use_backend(name) first")
    if n < 0:
        raise ValueError("n must be non-negative")
    return src.random_bytes(n)


__all__ = [
    "EntropySource",
    "QRNGNotAvailable",
    "register_backend",
    "use_backend",
    "current_backend",
    "random_bytes",
]
