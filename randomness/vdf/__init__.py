"""
randomness.vdf
==============

Package marker and light exports for the beacon's VDF (time-delay) component.

Design
------
- We standardize on the ``VDFInput`` and ``VDFProof`` types (from
  :mod:`randomness.types.core`).
- Backends (e.g., a pure-Python Wesolowski verifier or an accelerated one)
  should live in sibling modules (e.g., ``wesolowski.py``) and expose a
  ``verify(input: VDFInput, proof: VDFProof) -> bool`` function.
- This ``__init__`` provides a small indirection to acquire a default verifier
  without importing heavy dependencies at module-import time.

Usage
-----
>>> from randomness.vdf import get_default_verifier, VDFInput, VDFProof
>>> verify = get_default_verifier()   # picks Wesolowski backend if available
>>> ok = verify(vdf_input, vdf_proof)
"""

from __future__ import annotations

from typing import Callable

from ..version import __version__  # re-exported for convenience
from ..types.core import VDFInput, VDFProof

VerifierFn = Callable[[VDFInput, VDFProof], bool]


def _resolve_wesolowski() -> VerifierFn:
    """
    Try to resolve the Wesolowski backend. Raises ImportError if not present.

    Backends should implement:
        def verify(input: VDFInput, proof: VDFProof) -> bool: ...
    """
    from . import wesolowski  # type: ignore
    return wesolowski.verify  # type: ignore[attr-defined]


def get_default_verifier() -> VerifierFn:
    """
    Return the default VDF verifier function.

    Preference order:
      1) ``wesolowski.verify`` if available.
      2) Otherwise, raise a RuntimeError with a helpful message.
    """
    try:
        return _resolve_wesolowski()
    except Exception as e:  # pragma: no cover - exercised when backend absent
        raise RuntimeError(
            "No VDF backend available. Ensure randomness.vdf.wesolowski is present "
            "and provides a 'verify(VDFInput, VDFProof) -> bool' function."
        ) from e


__all__ = [
    "__version__",
    "VDFInput",
    "VDFProof",
    "VerifierFn",
    "get_default_verifier",
]
