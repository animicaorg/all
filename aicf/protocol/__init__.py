"""
AICF Protocol Module
====================

GPU Contributor Redistribution Protocol implementation.

This module provides the on-chain protocol layer for:
- GPU worker registration and management
- Training/eval job specifications
- Work submission and verification
- Challenge system with proof commitments
- Epoch-based credit accounting
- Claim processing and payouts
- Model release tracking
- AICF inflow management from ENA and other sources

Components:
-----------
- state: Protocol state management and persistence
- rpc: JSON-RPC methods for protocol interaction
- economics: Epoch accounting, credit minting, reward distribution
- verification: MVP verification model with challenge windows
- models: Data models and schemas
"""

from __future__ import annotations

__all__ = [
    "ProtocolState",
    "ProtocolRPCMethods",
    "EpochAccountant",
    "VerificationPolicy",
]

# Lazy imports to avoid circular dependencies
def __getattr__(name: str):
    if name == "ProtocolState":
        from aicf.protocol.state import ProtocolState
        return ProtocolState
    elif name == "ProtocolRPCMethods":
        from aicf.protocol.rpc import ProtocolRPCMethods
        return ProtocolRPCMethods
    elif name == "EpochAccountant":
        from aicf.protocol.economics import EpochAccountant
        return EpochAccountant
    elif name == "VerificationPolicy":
        from aicf.protocol.verification import VerificationPolicy
        return VerificationPolicy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
