"""
Top-level 'stdlib' shim for the Animica Python-VM.

Contracts and tests use:

    from stdlib import storage, events, abi, treasury
"""

from vm_py.stdlib import (abi, events, storage,  # type: ignore[attr-defined]
                          treasury)

__all__ = ["storage", "events", "abi", "treasury"]
