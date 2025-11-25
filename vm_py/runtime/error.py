from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


@dataclass
class VmError(Exception):
    """
    Structured error used inside the Animica Python-VM runtime.

    Supported call patterns (backwards compatible):

        VmError("simple message")

        VmError("message", code="some_code", context={...})

        # Legacy 2-positional form:
        VmError("SOME_CODE", "message")
        VmError("SOME_CODE", "message", context={...})

    Attributes:
        code: short machine-readable code string
        message: human-readable message
        context: optional extra fields for debugging / RPC wiring
    """

    code: str
    message: str
    context: Dict[str, Any]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Defaults
        code: str = "vm_error"
        context: Dict[str, Any] = {}

        # Pull keyword args if provided
        if "code" in kwargs:
            code = str(kwargs.pop("code"))

        if "context" in kwargs:
            ctx = kwargs.pop("context")
            if ctx is None:
                context = {}
            elif isinstance(ctx, Mapping):
                context = dict(ctx)
            else:
                # Best-effort coercion
                context = dict(ctx)  # type: ignore[arg-type]

        # Interpret positional args
        if len(args) == 0:
            message = ""
        elif len(args) == 1:
            # VmError(message)
            message = str(args[0])
        else:
            # Legacy: VmError(code, message, ...)
            code = str(args[0])
            message = str(args[1])

        # Initialise Exception with the human-readable message
        super().__init__(message)

        # Assign dataclass-style fields
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "context", context)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "context": dict(self.context),
        }


__all__ = ["VmError"]
