"""
Standalone OpenCL miner package.

This module wires the Stratum client to the OpenCL hashing backend and exposes
an asyncio-friendly `StratumOpenCLMiner` used by the CLI entry point.
"""

from .miner import StratumOpenCLMiner

__all__ = ["StratumOpenCLMiner"]
