"""
Animica DA errors.

Lightweight, typed exception hierarchy with structured metadata suitable for
API layers (e.g., FastAPI) or internal callers.

Usage:

    from da.errors import NotFound, InvalidProof, NamespaceRangeError

    raise NotFound("Blob not found", data={"commitment": commit_hex})

All errors expose:
- .code   : stable machine-readable code (snake_case)
- .status : suggested HTTP status (int)
- .data   : optional structured payload (dict-like)
- .to_problem() : RFC 7807-compatible dict for JSON responses
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


class DAError(Exception):
    """
    Base class for DA errors.

    Subclasses should set `default_code` and `default_status`.
    """
    default_code = "da_error"
    default_status = 400

    def __init__(
        self,
        message: str = "",
        *,
        code: Optional[str] = None,
        status: Optional[int] = None,
        data: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.status = int(status if status is not None else self.default_status)
        # Store a shallow copy to prevent accidental external mutation
        self.data: Dict[str, Any] = dict(data) if data else {}

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.message:
            return f"{self.code}: {self.message}"
        return self.code

    def to_problem(self) -> Dict[str, Any]:
        """
        Render as an RFC 7807 "problem detail" object.
        """
        return {
            "type": f"urn:animica:da:{self.code}",
            "title": self.code.replace("_", " ").title(),
            "status": self.status,
            "detail": self.message or None,
            "data": self.data or None,
        }

    @classmethod
    def from_exc(cls, exc: BaseException, *, code: Optional[str] = None, status: Optional[int] = None) -> "DAError":
        """
        Wrap an arbitrary exception into a DAError with a best-effort message.
        """
        msg = f"{exc.__class__.__name__}: {exc}"
        return cls(msg, code=code, status=status)


class NotFound(DAError):
    """
    The requested blob/commitment/namespace was not found.
    """
    default_code = "not_found"
    default_status = 404


class InvalidProof(DAError):
    """
    Provided proof failed to verify (NMT branch, inclusion/range proof, or DAS proof).
    """
    default_code = "invalid_proof"
    default_status = 422  # Unprocessable Entity


class NamespaceRangeError(DAError):
    """
    Namespace identifier is out of allowed range or hits a reserved span.
    """
    default_code = "namespace_out_of_range"
    default_status = 400


__all__ = [
    "DAError",
    "NotFound",
    "InvalidProof",
    "NamespaceRangeError",
]
