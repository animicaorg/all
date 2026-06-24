"""Exception hierarchy for Animica Studio (serverless compute SDK)."""

from __future__ import annotations


class StudioError(Exception):
    """Base class for all Studio SDK errors."""


class ConfigError(StudioError):
    """Misconfiguration: missing endpoint, wallet, or invalid setting."""


class SerializationError(StudioError):
    """A call's function/args could not be packed or a result unpacked."""


class ExecutionError(StudioError):
    """The remote/sandbox execution of a function raised or exited non-zero.

    ``remote_traceback`` carries the worker-side traceback text when available
    so a developer sees the *function's* failure, not just transport noise.
    """

    def __init__(self, message: str, *, remote_traceback: str | None = None,
                 exit_code: int | None = None) -> None:
        super().__init__(message)
        self.remote_traceback = remote_traceback
        self.exit_code = exit_code

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        base = super().__str__()
        if self.remote_traceback:
            return f"{base}\n\n--- remote traceback ---\n{self.remote_traceback}"
        return base


class TimeoutError(StudioError):  # noqa: A001 - deliberately shadows builtin in this namespace
    """A function exceeded its ``timeout`` budget."""


class BillingError(StudioError):
    """Escrow, quoting, or settlement failed."""


class InsufficientFundsError(BillingError):
    """The caller's ANM balance can't cover the quoted cost + fees."""


class JobError(StudioError):
    """The broker rejected a job, or a job ended in a failed terminal state."""


class NotDeployedError(StudioError):
    """A named function/app was requested that isn't registered."""
