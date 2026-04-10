"""
omni_sdk.rpc
------------

Lightweight RPC helpers.

This package exposes:
- RpcClient: HTTP JSON-RPC client (see .http)
- WsClient:  WebSocket subscription client (see .ws)

Import style:

    from omni_sdk.rpc import RpcClient, WsClient
    rpc = RpcClient(url="http://localhost:8545")
    ws  = WsClient(url="ws://localhost:8546")

Both submodules are intentionally small and dependency-light.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Re-export (soft) so consumers can `from omni_sdk.rpc import RpcClient, WsClient`
try:  # pragma: no cover - resolved when submodules exist
    from .http import RpcClient  # type: ignore
except Exception:  # pragma: no cover
    RpcClient = None  # type: ignore[assignment]

try:  # pragma: no cover
    from .ws import WsClient  # type: ignore
except Exception:  # pragma: no cover
    WsClient = None  # type: ignore[assignment]

# For type checkers (optional)
if TYPE_CHECKING:
    # These imports are only for typing; runtime re-exports above may be None
    from .http import RpcClient as _RpcClient  # noqa: F401
    from .ws import WsClient as _WsClient  # noqa: F401

__all__ = ["RpcClient", "WsClient"]
