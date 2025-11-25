"""
mempool.cli
===========

Entry package for mempool command-line helpers and developer tools.

This package intentionally keeps a tiny surface:
- Acts as a namespace for individual CLI modules (e.g., `inspect`, `watch`,
  `replay`, etc., when added).
- Exposes the mempool package version for `--version` flags.

Downstream launchers may `python -m mempool.cli.<tool>` directly.
"""

from __future__ import annotations

try:
    # Re-export the module version so CLIs can report it.
    from mempool.version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0+dev"

__all__ = ["__version__"]
