"""
execution.types — canonical execution-layer types for Animica.

This package groups small, dependency-light dataclasses and enums that are shared
across the execution engine (VM/runtime, scheduler) and higher layers (RPC/mempool).

Public surface (re-exported):
    TxStatus                      : Enum — SUCCESS / REVERT / OOG
    LogEvent                      : Dataclass — (address, topics, data)
    BlockContext, TxContext       : Dataclasses — execution contexts
    ApplyResult                   : Dataclass — result of applying a tx
    AccessList, AccessListEntry   : Access-list element types
    Gas, GasPrice                 : Tiny numeric wrappers + helpers
"""

from __future__ import annotations

# Re-export the most commonly used types from their submodules.
from .status import TxStatus
from .events import LogEvent
from .context import BlockContext, TxContext
from .result import ApplyResult
from .access_list import AccessList, AccessListEntry
from .gas import Gas, GasPrice

__all__ = [
    "TxStatus",
    "LogEvent",
    "BlockContext",
    "TxContext",
    "ApplyResult",
    "AccessList",
    "AccessListEntry",
    "Gas",
    "GasPrice",
]
