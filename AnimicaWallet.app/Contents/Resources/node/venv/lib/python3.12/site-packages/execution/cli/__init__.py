"""
execution.cli — command-line entrypoints for the Execution module.

This package groups small utilities intended for local testing and ops:

  • execution.cli.apply_block  — apply a CBOR-encoded block to a DB and print the new head
  • execution.cli.run_tx       — run a single CBOR-encoded tx against a temp state and print the ApplyResult

Usage (examples):
    python -m execution.cli.apply_block --help
    python -m execution.cli.run_tx --help
"""

from __future__ import annotations

# Re-export the execution module version for convenience.
try:
    from ..version import __version__  # type: ignore[F401]
except Exception:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = ["__version__"]
