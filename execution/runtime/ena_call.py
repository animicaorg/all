"""
execution.runtime.ena_call — ENA Inference Call Transaction Handler (Stub)
==========================================================================

Placeholder for ENA (Elastic Neural Agents) inference call transaction handler.

This will be implemented when ENA integration is complete. For now, it returns
a REVERT status to indicate the feature is not yet available.

Future Implementation:
- Extract inference request parameters
- Route to ENA service
- Compute fees (80% to AICF, 20% to miner)
- Record compute receipts for GPU contributor attribution
- Return inference results in transaction receipt
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Mapping, Optional

from ..errors import ExecError
from ..types.status import TxStatus

if TYPE_CHECKING:
    from ..types.result import ApplyResult
    from .env import BlockEnv, TxEnv

log = logging.getLogger("execution.runtime.ena_call")


class ENACallError(ExecError):
    """Raised when ENA call transaction fails."""


def _state_root(state: Any) -> bytes:
    """Best-effort state root extraction."""
    for name in ("compute_state_root", "state_root", "merkle_root"):
        fn = getattr(state, name, None)
        if callable(fn):
            try:
                root = fn()
                if isinstance(root, (bytes, bytearray)):
                    b = bytes(root)
                    if len(b) == 32:
                        return b
                    return b[:32].rjust(32, b"\x00")
            except Exception:
                pass
        val = getattr(state, name, None)
        if isinstance(val, (bytes, bytearray)):
            return bytes(val)[:32].rjust(32, b"\x00")
    return b"\x00" * 32


def apply_ena_call(
    tx: Any,
    state: Any,
    block_env: BlockEnv,
    tx_env: TxEnv,
    *,
    params: Optional[Mapping[str, Any]] = None,
    capabilities: Optional[Any] = None,
) -> "ApplyResult":
    """
    Execute an ENA inference call transaction (placeholder).
    
    Args:
        tx: Transaction object with ENA call payload
        state: Mutable state handle
        block_env: Block execution environment
        tx_env: Transaction execution environment
        params: Chain parameters with AICF config
        capabilities: Capabilities adapter for AI inference
    
    Returns:
        ApplyResult with REVERT status (not implemented yet)
    """
    from ..types.result import ApplyResult
    from ..types.events import LogEvent
    
    log.warning("ENA call transaction received but feature not yet implemented")
    
    return ApplyResult(
        status=TxStatus.REVERT,
        gas_used=21000,  # Base gas for unsupported tx
        logs=[
            LogEvent(
                address=b"\x00" * 20,
                topics=[b"ena.call.error"],
                data=b"ENA call transactions not yet implemented",
            )
        ],
        state_root=_state_root(state),
        receipt=None,
    )


__all__ = [
    "apply_ena_call",
    "ENACallError",
]
