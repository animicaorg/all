from __future__ import annotations

"""
Error hierarchy and helpers for Studio Services.

This module defines a small, consistent set of API exceptions and helpers
to serialize them as RFC 7807 "problem+json" responses. These exceptions
are framework-agnostic; a FastAPI/Starlette middleware can catch them and
return JSON automatically.

Usage
-----
    from studio_services.errors import BadRequest

    raise BadRequest("Invalid ABI", details={"path": "/functions/0/inputs/1"})

Design
------
- Every error has:
  - ``status_code`` (int): HTTP status
  - ``code`` (str): stable machine code (e.g., "bad_request")
  - ``message`` (str): human-friendly summary
  - ``details`` (dict|None): optional structured diagnostics
- ``to_problem()`` returns an RFC 7807 dict.
- ``to_response()`` returns a Starlette JSONResponse (if available).
"""

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


DEFAULT_ERROR_DOCS_BASE = "https://docs.animica.dev/errors"  # hypothetical docs base


@dataclass
class ApiError(Exception):
    message: str
    status_code: int = 400
    code: str = "bad_request"
    details: Optional[Mapping[str, Any]] = None
    type_uri_base: str = DEFAULT_ERROR_DOCS_BASE

    def __post_init__(self) -> None:
        # Ensure Exception str is meaningful
        super().__init__(self.message)

    # --- RFC 7807 helpers -------------------------------------------------- #

    def type_uri(self) -> str:
        return f"{self.type_uri_base}#{self.code}"

    def title(self) -> str:
        # Human title from code (fallback to message)
        return {
            "bad_request": "Bad Request",
            "chain_mismatch": "Chain ID Mismatch",
            "verify_failed": "Verification Failed",
            "faucet_disabled": "Faucet Disabled",
            "unauthorized": "Unauthorized",
            "forbidden": "Forbidden",
            "not_found": "Not Found",
            "rate_limited": "Rate Limited",
            "rpc_error": "Upstream RPC Error",
            "server_error": "Internal Server Error",
        }.get(self.code, self.message or "Error")

    def to_problem(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "type": self.type_uri(),
            "title": self.title(),
            "status": self.status_code,
            "code": self.code,
            "detail": self.message,
        }
        if self.details:
            body["details"] = dict(self.details)
        return body

    # --- Framework bridges ------------------------------------------------- #

    def to_response(self):
        """
        Return a Starlette JSONResponse, if Starlette is available.
        This keeps the error usable without FastAPI in unit tests.
        """
        try:
            from starlette.responses import JSONResponse  # type: ignore
        except Exception as e:  # pragma: no cover - optional dependency path
            raise RuntimeError("Starlette is not available to build a response") from e
        return JSONResponse(self.to_problem(), status_code=self.status_code)

    def to_http_exception(self):
        """
        Return a FastAPI/Starlette HTTPException instance, embedding the problem body.
        Some apps prefer raising HTTPException directly from route handlers.
        """
        # Try FastAPI first, then Starlette
        exc_cls = None
        try:
            from fastapi import HTTPException as FastApiHTTPException  # type: ignore

            exc_cls = FastApiHTTPException
        except Exception:  # pragma: no cover
            try:
                from starlette.exceptions import HTTPException as StarletteHTTPException  # type: ignore

                exc_cls = StarletteHTTPException
            except Exception as e:  # pragma: no cover
                raise RuntimeError("Neither FastAPI nor Starlette HTTPException is available") from e

        return exc_cls(status_code=self.status_code, detail=self.to_problem())

    # --- Conversions ------------------------------------------------------- #

    @classmethod
    def from_unexpected(cls, err: BaseException) -> "ApiError":
        """
        Convert an unexpected exception into a generic server error while
        preserving a minimal diagnostic in ``details``.
        """
        return ServerError(
            "Unhandled server error",
            details={"exc_type": err.__class__.__name__, "str": str(err)},
        )


# ------------------------------ Concrete types ------------------------------- #


class BadRequest(ApiError):
    def __init__(self, message: str = "Bad request", *, details: Optional[Mapping[str, Any]] = None):
        super().__init__(message=message, status_code=400, code="bad_request", details=details)


class ChainMismatch(ApiError):
    def __init__(self, expected: int, got: int):
        super().__init__(
            message=f"Chain ID mismatch: expected {expected}, got {got}",
            status_code=409,
            code="chain_mismatch",
            details={"expected": expected, "got": got},
        )


class VerifyFail(ApiError):
    def __init__(self, reason: str = "Source verification failed", *, details: Optional[Mapping[str, Any]] = None):
        super().__init__(message=reason, status_code=422, code="verify_failed", details=details)


class FaucetOff(ApiError):
    def __init__(self):
        super().__init__(message="Faucet is disabled", status_code=403, code="faucet_disabled")


class Unauthorized(ApiError):
    def __init__(self, message: str = "Missing or invalid credentials", *, details: Optional[Mapping[str, Any]] = None):
        super().__init__(message=message, status_code=401, code="unauthorized", details=details)


class Forbidden(ApiError):
    def __init__(self, message: str = "Forbidden", *, details: Optional[Mapping[str, Any]] = None):
        super().__init__(message=message, status_code=403, code="forbidden", details=details)


class NotFound(ApiError):
    def __init__(self, what: str = "Resource", *, details: Optional[Mapping[str, Any]] = None):
        super().__init__(message=f"{what} not found", status_code=404, code="not_found", details=details)


class RateLimited(ApiError):
    def __init__(self, retry_after: Optional[float] = None):
        details: Dict[str, Any] = {}
        if retry_after is not None:
            details["retry_after"] = retry_after
        super().__init__(message="Too many requests", status_code=429, code="rate_limited", details=details)


class RpcError(ApiError):
    def __init__(self, message: str = "Upstream RPC error", *, details: Optional[Mapping[str, Any]] = None, status: int = 502):
        super().__init__(message=message, status_code=status, code="rpc_error", details=details)


class ServerError(ApiError):
    def __init__(self, message: str = "Internal server error", *, details: Optional[Mapping[str, Any]] = None):
        super().__init__(message=message, status_code=500, code="server_error", details=details)


__all__ = [
    "ApiError",
    "BadRequest",
    "ChainMismatch",
    "VerifyFail",
    "FaucetOff",
    "Unauthorized",
    "Forbidden",
    "NotFound",
    "RateLimited",
    "RpcError",
    "ServerError",
]
