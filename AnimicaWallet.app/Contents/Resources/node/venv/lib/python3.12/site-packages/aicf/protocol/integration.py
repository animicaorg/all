"""
AICF Protocol Integration for ENA Service
==========================================

Integrates ENA payment flow with AICF protocol deposit recording.

This module extends the existing ENA payment verification to also
record AICF contributions in the protocol state for redistribution.
"""

from __future__ import annotations

import logging
from typing import Dict, Any, Optional

from aicf.protocol.state import ProtocolState
from aicf.protocol.economics import EpochAccountant

logger = logging.getLogger(__name__)


class AICFProtocolRecorder:
    """
    Records AICF deposits into the protocol for redistribution.
    
    This class bridges ENA payments with the AICF protocol state,
    ensuring all inflows are tracked and attributed correctly.
    """

    def __init__(
        self,
        protocol_state: ProtocolState,
        epoch_length_blocks: int = 1000,
    ):
        """
        Initialize protocol recorder.
        
        Args:
            protocol_state: Protocol state manager
            epoch_length_blocks: Blocks per epoch for epoch calculation
        """
        self.state = protocol_state
        self.accountant = EpochAccountant(protocol_state)
        self.epoch_length = epoch_length_blocks

    def calculate_epoch_from_height(self, block_height: int) -> int:
        """
        Calculate epoch ID from block height.
        
        Args:
            block_height: Current block height
        
        Returns:
            Epoch ID
        """
        return block_height // self.epoch_length

    def record_ena_deposit(
        self,
        amount: int,
        tx_hash: str,
        block_height: Optional[int] = None,
        payer: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> str:
        """
        Record an ENA payment as an AICF protocol deposit.
        
        Args:
            amount: Amount in base units
            tx_hash: Payment transaction hash
            block_height: Block height of transaction
            payer: Payer address
            request_id: Optional request identifier
        
        Returns:
            Inflow ID
        """
        # Calculate epoch from block height
        if block_height is not None:
            epoch_id = self.calculate_epoch_from_height(block_height)
        else:
            # If no block height, use epoch 0 (pending assignment)
            epoch_id = 0
        
        # Ensure epoch exists
        start_height = epoch_id * self.epoch_length
        self.state.get_or_create_epoch(epoch_id, start_height)
        
        # Record inflow
        metadata = {}
        if payer:
            metadata["payer"] = payer
        if request_id:
            metadata["request_id"] = request_id
        
        inflow_id = self.accountant.record_inflow(
            epoch_id=epoch_id,
            source="ena",
            amount=str(amount),
            tx_hash=tx_hash,
            block_height=block_height,
            metadata=metadata if metadata else None,
        )
        
        logger.info(
            f"Recorded ENA deposit to AICF protocol",
            extra={
                "inflow_id": inflow_id,
                "amount": amount,
                "tx_hash": tx_hash,
                "epoch_id": epoch_id,
                "block_height": block_height,
                "payer": payer,
            }
        )
        
        return inflow_id

    def get_protocol_status(self) -> Dict[str, Any]:
        """
        Get AICF protocol status for ENA endpoint.
        
        Returns:
            Protocol status dict
        """
        params = self.state.get_all_params()
        
        # Get active worker count
        active_workers = self.state.list_workers(status="ACTIVE", limit=10000)
        
        return {
            "protocol_enabled": True,
            "epoch_length_blocks": int(params.get("epoch_length_blocks", "1000")),
            "challenge_window_blocks": int(params.get("challenge_window_blocks", "100")),
            "reward_split": {
                "gpu_workers_bp": int(params.get("reward_split_gpu_workers_bp", "7000")),
                "treasury_bp": int(params.get("reward_split_treasury_bp", "2000")),
                "dev_bp": int(params.get("reward_split_dev_bp", "500")),
                "burn_bp": int(params.get("reward_split_burn_bp", "500")),
            },
            "active_workers": len(active_workers),
        }


def create_protocol_recorder(
    db_path: str,
    epoch_length_blocks: int = 1000,
) -> AICFProtocolRecorder:
    """
    Create an AICF protocol recorder instance.
    
    Args:
        db_path: Path to protocol database
        epoch_length_blocks: Blocks per epoch
    
    Returns:
        Protocol recorder instance
    """
    state = ProtocolState(db_path)
    return AICFProtocolRecorder(state, epoch_length_blocks)
