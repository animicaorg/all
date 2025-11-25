"""
Animica execution layer — deterministic Python-VM host, gas, state, receipts, schedulers.

This package exposes only lightweight metadata at import time. Heavy modules (VM, DB
adapters, etc.) should be imported explicitly from their subpackages to avoid side effects.
"""

# Package metadata (robust to missing version module during early bootstraps)
try:
    from .version import __version__, git_describe  # type: ignore
except Exception:  # pragma: no cover - fallback for fresh checkouts
    __version__ = "0.0.0+local"

    def git_describe() -> str:
        """Return a best-effort version string when VCS metadata isn't available."""
        return __version__

__all__ = ["__version__", "git_describe"]
