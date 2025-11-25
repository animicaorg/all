"""
Animica Mempool package.

Lightweight public surface:
- __version__: semantic version (from mempool.version if available)
- get_version(): safe accessor for the version string

Selected submodules are lazily exposed on first access to avoid import cycles:
- config, errors, metrics
(They are imported only if you touch mempool.config / mempool.errors / mempool.metrics)
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

try:
    # Prefer the real version module if present.
    from .version import __version__  # type: ignore
except Exception:  # pragma: no cover - falls back in early bring-up
    __version__ = "0.0.0+dev"

__all__ = ["__version__", "get_version"]

def get_version() -> str:
    """Return the mempool package version string."""
    return __version__

# --- Lazy exports for common submodules ------------------------------------
_lazy_exports = {
    "config": "mempool.config",
    "errors": "mempool.errors",
    "metrics": "mempool.metrics",
}

def __getattr__(name: str) -> Any:  # PEP 562
    mod_path = _lazy_exports.get(name)
    if mod_path is None:
        raise AttributeError(f"module 'mempool' has no attribute {name!r}")
    import importlib
    mod = importlib.import_module(mod_path)
    globals()[name] = mod  # cache after first import
    return mod

if TYPE_CHECKING:  # for IDEs/type-checkers without incurring import-time cost
    from . import config as config  # noqa: F401
    from . import errors as errors  # noqa: F401
    from . import metrics as metrics  # noqa: F401
