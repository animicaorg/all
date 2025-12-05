"""
Top-level 'stdlib' shim for the Animica Python-VM.

Contracts and tests use:

    from stdlib import storage, events, abi, treasury, syscalls
"""

from vm_py.stdlib import syscalls  # type: ignore[attr-defined]
from vm_py.stdlib import abi, events, storage, treasury

__all__ = ["storage", "events", "abi", "treasury", "syscalls"]
