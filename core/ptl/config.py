"""PTL (Pending Transaction Ledger) configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class PtlConfig:
    """Configuration for PTL system."""

    # System selection
    tx_system: str = "ptl"  # "ptl" or "mempool"
    
    # Replication settings
    min_peer_acks: int = 2
    ttl_seconds: int = 3600  # 1 hour default
    
    # Database settings
    db_path: Optional[str] = None
    
    # Reconciliation settings
    reconcile_interval_s: float = 10.0
    announce_batch_size: int = 100
    announce_interval_s: float = 1.0
    max_push_batch: int = 50
    
    # Selection settings for mining
    max_block_size: int = 1_000_000
    max_block_gas: int = 10_000_000
    
    @classmethod
    def from_env(cls) -> PtlConfig:
        """Load configuration from environment variables."""
        # Get tx_system and fallback to "ptl" if unset or empty
        tx_system = os.getenv("ANIMICA_TX_SYSTEM", "ptl") or "ptl"
        
        return cls(
            tx_system=tx_system,
            min_peer_acks=int(os.getenv("ANIMICA_PTL_MIN_PEER_ACKS", "2")),
            ttl_seconds=int(os.getenv("ANIMICA_PTL_TTL_SECONDS", "3600")),
            db_path=os.getenv("ANIMICA_PTL_DB_PATH"),
            reconcile_interval_s=float(os.getenv("ANIMICA_PTL_RECONCILE_INTERVAL_S", "10.0")),
            announce_batch_size=int(os.getenv("ANIMICA_PTL_ANNOUNCE_BATCH_SIZE", "100")),
            announce_interval_s=float(os.getenv("ANIMICA_PTL_ANNOUNCE_INTERVAL_S", "1.0")),
            max_push_batch=int(os.getenv("ANIMICA_PTL_MAX_PUSH_BATCH", "50")),
            max_block_size=int(os.getenv("ANIMICA_PTL_MAX_BLOCK_SIZE", "1000000")),
            max_block_gas=int(os.getenv("ANIMICA_PTL_MAX_BLOCK_GAS", "10000000")),
        )
    
    def use_ptl(self) -> bool:
        """Check if PTL system should be used."""
        return self.tx_system.lower() == "ptl"
    
    def use_mempool(self) -> bool:
        """Check if mempool system should be used."""
        return self.tx_system.lower() == "mempool"


__all__ = ["PtlConfig"]
