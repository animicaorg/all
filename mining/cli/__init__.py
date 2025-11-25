from __future__ import annotations

"""
Animica Mining CLI package.

This namespace groups the miner-facing command-line tools:

  - python -m mining.cli.miner          # start the built-in CPU miner
  - python -m mining.cli.stratum_proxy  # bridge local node RPC <-> external miners
  - python -m mining.cli.getwork        # fetch & print current header template (debug)

The actual command implementations live in sibling modules. This file exposes a
version banner and keeps imports lightweight so that merely importing the
package does not pull heavy dependencies (numpy/numba/etc.).
"""

try:
    from ..version import __version__
except Exception:  # pragma: no cover
    __version__ = "0.0.0-dev"

__all__ = ["__version__"]
