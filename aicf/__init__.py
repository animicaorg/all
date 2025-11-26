from __future__ import annotations
"""
AICF - AI Compute Fund package.

This package coordinates off-chain AI/Quantum jobs with on-chain proofs,
provider registry/staking, settlement economics, SLAs/slashing, and RPC/CLI
surfaces. Submodules are lazily imported to keep import time minimal.

Public surface (lazily loaded):
- config, errors, metrics
- registry, queue, economics, sla, treasury
- integration, rpc, cli, policy
"""


from typing import List

try:
    # Populated by aicf/version.py (created in this repo).
    from .version import __version__  # type: ignore
except Exception:  # pragma: no cover - safe fallback when building incrementally
    __version__ = "0.0.0+local"

__all__: List[str] = [
    "__version__",
    # lazily importable subpackages/modules
    "config",
    "errors",
    "metrics",
    "registry",
    "queue",
    "economics",
    "sla",
    "treasury",
    "integration",
    "rpc",
    "cli",
    "policy",
]

# --- Lazy module loader (PEP 562) -------------------------------------------------
# This avoids ImportError while the repo is being brought up incrementally.
import importlib
import types


_lazy_modules = set(__all__) - {"__version__"}


def __getattr__(name: str):
    if name in _lazy_modules:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals().keys()) | _lazy_modules)


def get_version() -> str:
    """Return the AICF package version string."""
    return __version__
