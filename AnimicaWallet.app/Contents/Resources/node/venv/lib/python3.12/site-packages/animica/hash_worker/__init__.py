"""
Hash worker daemon for Animica.

Off-chain worker that listens for HashJobPosted events, executes hash work,
and submits results back to the chain.
"""

from .backends import CPUBackend, get_backend
from .daemon import HashWorkerDaemon

__all__ = [
    "HashWorkerDaemon",
    "CPUBackend",
    "get_backend",
]
