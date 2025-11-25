"""
Top-level 'stdlib' shim for the Animica Python-VM.

Contracts and tests use:

    from stdlib import storage, events, abi, treasury
"""

from vm_py.stdlib import storage, events, abi, treasury  # type: ignore[attr-defined]

__all__ = ["storage", "events", "abi", "treasury"]
