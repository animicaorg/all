"""Convenience entrypoint for running the Animica stratum pool.

This module forwards to :mod:`animica.stratum_pool.cli` so that existing
invocations such as ``python -m animica.mining.pool`` continue to work.
"""

from __future__ import annotations

from animica.stratum_pool.cli import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
