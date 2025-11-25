"""
capabilities
------------

Deterministic off-chain “syscalls” for Animica contracts.

This package exposes host-side capability providers (blob pin/get, AI/Quantum
enqueue + result read, zk.verify, deterministic randomness, treasury hooks),
plus the job/queue plumbing and adapters that make those calls safe for
consensus. All external effects are constrained to be deterministic across
nodes.

Public surface is intentionally small here; individual submodules document
their own APIs.

Example (read-only import):

    from capabilities import __version__
"""

from __future__ import annotations

try:
    # Populated by capabilities/version.py
    from .version import __version__  # type: ignore
except Exception:  # pragma: no cover - fallback for editable installs
    __version__ = "0.0.0"

__all__ = ["__version__"]
