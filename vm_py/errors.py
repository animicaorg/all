from __future__ import annotations

"""Compatibility layer for VM errors.

Historically different parts of the repo imported error classes from either
`vm_py.errors` or `vm_py.runtime.error`.  This module keeps a stable public
surface and funnels everything through structured `VmError` instances.
"""

from typing import Any, Mapping

from vm_py.runtime.error import VmError


class ValidationError(VmError):
    """Raised when source, ABI payloads, or runtime calls fail validation."""

    def __init__(
        self,
        message: str,
        *,
        phase: str | None = None,
        reason: str | None = None,
        context: Mapping[str, Any] | None = None,
        code: str = "validation_error",
    ) -> None:
        ctx = dict(context or {})
        if phase is not None:
            ctx.setdefault("phase", phase)
        if reason is not None:
            ctx.setdefault("reason", reason)
        super().__init__(message, code=code, context=ctx)


class ForbiddenImport(ValidationError):
    """Raised for imports outside the deterministic stdlib allowlist."""

    def __init__(
        self,
        module: str,
        *,
        symbol: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        ctx = dict(context or {})
        ctx.setdefault("module", module)
        if symbol is not None:
            ctx.setdefault("symbol", symbol)
        super().__init__(
            f"forbidden import: {module}" + (f".{symbol}" if symbol else ""),
            phase="ast",
            reason="forbidden_import",
            context=ctx,
            code="forbidden_import",
        )


class CompileError(VmError):
    """Raised for VM compile/lower/encode failures."""

    def __init__(
        self,
        message: str,
        *,
        context: Mapping[str, Any] | None = None,
        code: str = "compile_error",
    ) -> None:
        super().__init__(message, code=code, context=dict(context or {}))


class Revert(VmError):
    """Deterministic contract revert."""

    def __init__(self, message: str = "revert", *, context: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="revert", context=dict(context or {}))


class OOG(VmError):
    """Out-of-gas condition."""

    def __init__(self, message: str = "out of gas", *, context: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="out_of_gas", context=dict(context or {}))


__all__ = [
    "VmError",
    "ValidationError",
    "ForbiddenImport",
    "CompileError",
    "Revert",
    "OOG",
]
