"""
randomness.rpc
--------------

Convenience re-exports for mounting the randomness REST/WS/JSON-RPC
endpoints onto a host FastAPI app or RPC registry.

Typical use:

    from fastapi import FastAPI
    from randomness.rpc import mount_randomness_rpc

    app = FastAPI()
    mount_randomness_rpc(app, service=my_randomness_service)

See `randomness.adapters.rpc_mount` for the concrete interfaces.
"""

from __future__ import annotations

from ..adapters.rpc_mount import (EventSource, RandomnessService, get_router,
                                  mount_randomness_rpc)

__all__ = [
    "mount_randomness_rpc",
    "get_router",
    "RandomnessService",
    "EventSource",
]
