"""
Compatibility shim:

    from vm_py.runtime.vm_error import VmError

Historically VmError lived here. The canonical implementation now lives
in vm_py.runtime.error; this module simply re-exports it.
"""

from .error import VmError  # type: ignore[attr-defined]

__all__ = ["VmError"]
