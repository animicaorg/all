"""Local node management subsystem.

This module provides a clean interface for running a local Animica node
that the GUI controls directly. All RPC communication is restricted to
localhost only - no remote node connections are supported.
"""

from .manager import LocalNodeManager
from .rpc import LocalRpcClient
from .status import NodeStatus, SyncStatus

__all__ = [
    "LocalNodeManager",
    "LocalRpcClient", 
    "NodeStatus",
    "SyncStatus",
]
