"""Exception hierarchy for the Animica Python Cloud SDK.

Two families, deliberately kept apart:

* Local errors (:class:`ConfigError`, :class:`ExtractionError`) — something is wrong on the
  developer's machine before a single byte reaches the platform.
* API errors (:class:`ApiError` and subclasses) — the platform said no. These carry the exact
  ``{error: {code, message, details}}`` envelope every animica.dev API route emits (lib/api.ts
  ``err()``), so a caller can branch on ``.code`` instead of parsing prose.

Money-relevant refusals (``insufficient_funds``, ``plan_limit``, ``budget_exceeded``) arrive as
plain :class:`ApiError` with the server's code — the SDK never invents its own billing logic,
because the server is the only authority on balances and entitlements.
"""

from __future__ import annotations

from typing import Any, Optional


class CloudError(Exception):
    """Base class for every error raised by ``animica.cloud``."""


class ConfigError(CloudError):
    """Missing or invalid local configuration (no API key, unreadable credentials file...)."""


class ExtractionError(CloudError):
    """A source file could not be turned into a deployable artifact.

    Raised when SDK-decorated source can't be stripped down to the plain
    ``def entrypoint(request[, ctx])`` module the runtime ABI executes.
    """


class NotDeployedError(CloudError):
    """``.remote()`` was called on a function the platform doesn't know about yet."""


class NetworkError(CloudError):
    """The HTTP request never produced a platform response (DNS, TLS, refused, timeout)."""


class ApiError(CloudError):
    """The platform returned a non-2xx response.

    ``status`` is the HTTP status; ``code`` is the machine-readable error code from the
    response envelope; ``details`` is the optional structured payload (e.g. plan_limit errors
    carry {feature, limit, used, requiredPlan, upgradeUrl}).
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.details: dict[str, Any] = details or {}

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"[{self.status} {self.code}] {super().__str__()}"


class AuthError(ApiError):
    """401/403 — the API key is missing, invalid, revoked, or lacks the required scope."""


class NotFoundError(ApiError):
    """404 — the function / app / execution does not exist (or isn't visible to this key)."""


class RateLimitedError(ApiError):
    """429 — slow down. ``retry_after`` is seconds when the server said, else None."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: Optional[dict] = None,
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(status, code, message, details)
        self.retry_after = retry_after


class ValidationFailed(CloudError):
    """Pre-deploy validation refused the source.

    ``findings`` is the untouched report from the platform validator
    (sandbox/validate.py): [{severity, code, message, line, col}, ...].
    """

    def __init__(self, message: str, findings: Optional[list] = None) -> None:
        super().__init__(message)
        self.findings: list[dict] = findings or []
