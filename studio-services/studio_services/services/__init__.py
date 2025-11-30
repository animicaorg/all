"""
studio_services.services
========================

Thin, import-friendly facade for the service layer used by studio-services.

This package exposes the concrete service modules as lazy submodules
so that importing `studio_services.services` does not eagerly import
heavy dependencies (e.g., FastAPI, SQLite drivers, SDKs).

Usage
-----
    from studio_services.services import deploy, verify

    # Call into specific services
    tx_hash = deploy.relay_signed_tx(...)
    result  = verify.verify_source_and_store(...)

Public submodules
-----------------
- deploy     : Accept signed CBOR tx → relay via node RPC; preflight simulation.
- verify     : Re-compile & compare code hash; persist and fetch verification status/results.
- faucet     : Optional testnet drip with rate limits.
- artifacts  : Content-addressed artifact storage helpers.
- simulate   : Offline compile + run a single call (no state writes).

The exact functions/classes re-exported by each submodule are defined
in their respective files; this package only provides lazy access.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

try:
    # Bubble up package version for convenience
    from studio_services.version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = ["deploy", "verify", "faucet", "artifacts", "simulate"]


def __getattr__(name: str):
    """
    Lazily import service submodules on first access.

    This keeps import-time light and avoids importing optional deps unless needed.
    """
    if name in __all__:
        return import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    # Make dir() show logical members
    return sorted(list(globals().keys()) + __all__)


# Optional static typing support (without importing heavy modules at runtime)
if TYPE_CHECKING:  # pragma: no cover
    from . import artifacts as artifacts
    from . import deploy as deploy
    from . import faucet as faucet
    from . import simulate as simulate
    from . import verify as verify
