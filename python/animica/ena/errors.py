"""
animica.ena.errors
==================

Exception hierarchy for the ENA (agent / retrieval / useful-work / training)
orchestration layer. All ENA failures derive from :class:`ENAError` so callers
(CLI, service, worker) can catch one type and render a stable message.
"""

from __future__ import annotations

__all__ = [
    "ENAError",
    "ConfigError",
    "StoreError",
    "ProviderError",
    "DatasetError",
    "JobError",
    "TrainingError",
    "VerificationError",
    "PoolError",
]


class ENAError(Exception):
    """Base class for all ENA errors.

    Carries an optional machine-readable ``code`` and an operator ``hint`` so
    the CLI can print actionable guidance without parsing the message string.
    """

    code: str = "ENA/ERROR"

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self) -> str:
        out = f"{self.code}: {self.message}"
        if self.hint:
            out += f"\n  hint: {self.hint}"
        return out


class ConfigError(ENAError):
    code = "ENA/CONFIG"


class StoreError(ENAError):
    code = "ENA/STORE"


class ProviderError(ENAError):
    code = "ENA/PROVIDER"


class DatasetError(ENAError):
    code = "ENA/DATASET"


class JobError(ENAError):
    code = "ENA/JOB"


class TrainingError(ENAError):
    code = "ENA/TRAINING"


class VerificationError(ENAError):
    code = "ENA/VERIFICATION"


class PoolError(ENAError):
    code = "ENA/POOL"
