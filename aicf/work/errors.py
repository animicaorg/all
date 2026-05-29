"""
aicf.work.errors
----------------

Service-layer errors that the RPC adapter translates into AICFError /
HTTP status codes. Services raise these; transports map them.
"""

from __future__ import annotations

from typing import Any


class WorkError(Exception):
    """Service-layer error with a stable code and intended HTTP status."""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 400,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            out["details"] = self.details
        return out


__all__ = ["WorkError"]
