"""
Adapters for integrating studio-services with the Animica toolchain and node.

This package contains thin, testable facades over external systems so the
service layer can be kept framework-agnostic:

- node_rpc       : JSON-RPC client to the Animica node (send tx, head/receipt, etc.)
- light_verify   : Lightweight header/DA verification helpers (wraps sdk/python when available)
- vm_compile     : Offline compile of Python VM contracts for verification/preflight
- vm_hash        : Deterministic code/artifact hashing helpers
- da_client      : Optional bridge to Data Availability post/get/proof endpoints
- pq_addr        : Address validation utilities (bech32m anim1..., PQ alg id rules)

We intentionally avoid importing submodules eagerly to keep import-time fast and
to tolerate partial environments during bootstrap. Submodules are loaded lazily
via PEP 562 (__getattr__).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

# Public attributes resolved lazily
__all__ = [
    "node_rpc",
    "light_verify",
    "vm_compile",
    "vm_hash",
    "da_client",
    "pq_addr",
]


def __getattr__(name: str):
    if name in __all__:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    # Present lazy members in dir()
    return sorted(list(globals().keys()) + __all__)


# Help static analyzers and IDEs discover symbols without importing at runtime
if TYPE_CHECKING:
    from . import (da_client, light_verify, node_rpc, pq_addr, vm_compile,
                   vm_hash)
