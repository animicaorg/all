from __future__ import annotations

"""
Animica • Data Availability • CLI
=================================

Package marker for DA command-line utilities.

This namespace will host small, focused CLIs such as:
- put_blob.py    : Post a blob; print commitment & receipt.
- get_blob.py    : Retrieve a blob by commitment (to stdout or file).
- sim_sample.py  : Simulate DAS sampling and report p_fail.
- inspect_root.py: Decode/inspect an NMT root and namespace ranges.

Each CLI is designed to be importable as a module (for programmatic use)
and runnable as a script (e.g., `python -m da.cli.put_blob`).
"""

# Re-export package version for convenience
try:  # pragma: no cover - defensive fallback
    from da.version import __version__
except Exception:  # pragma: no cover
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
