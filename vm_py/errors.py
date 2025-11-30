from __future__ import annotations

"""
Compatibility layer for VM errors.

Tests import:

    from vm_py.errors import VmError

The canonical implementation lives in vm_py.runtime.error.
"""

from vm_py.runtime.error import VmError

__all__ = ["VmError"]


# --- Animica VM/Py error types (minimal) ---
class CompileError(Exception):
    """Raised for VM-Py compile/lower/encode failures."""

    pass
