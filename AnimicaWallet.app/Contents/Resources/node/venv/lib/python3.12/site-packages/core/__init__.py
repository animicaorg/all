"""
Animica core package.

This module provides the deterministic substrate for nodes: canonical types,
encoders, genesis boot, persistence, and head/block plumbing. Higher-level
modules (consensus, p2p, rpc, vm, da, aicf) build on top.

Only re-exports the version here to keep import-time side effects near zero.
"""

from __future__ import annotations

# Prefer the in-tree version tag; gracefully fall back to package metadata or a local tag.
try:
    from .version import __version__  # type: ignore
except Exception:  # pragma: no cover - fallback path
    try:
        # When installed as a wheel, use package metadata.
        from importlib.metadata import version as _pkg_version  # py>=3.8
    except Exception:  # pragma: no cover
        _pkg_version = None  # type: ignore

    try:
        __version__ = _pkg_version("animica-core") if _pkg_version else "0.0.0+local"
    except Exception:  # pragma: no cover
        __version__ = "0.0.0+local"


def get_version() -> str:
    """Return the semantic version string for this package."""
    return __version__


__all__ = ["__version__", "get_version"]
