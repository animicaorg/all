"""
Animica Mining Package

This package provides the built-in miner (CPU-first), Stratum & WebSocket getwork
services, and orchestration for assembling candidate blocks with useful proofs.

Exports
-------
__version__ : str
    Semantic version string for the mining module.
"""

from .version import __version__  # re-export

__all__ = ["__version__"]
