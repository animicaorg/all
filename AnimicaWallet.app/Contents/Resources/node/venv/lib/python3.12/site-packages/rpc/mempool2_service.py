"""
rpc.mempool2_service - Service Wrapper for Mempool2
====================================================

Singleton service managing mempool2 instance for RPC handlers.
Provides a clean interface between RPC layer and mempool2 core.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from coretx import TxEnvelope, TxId, TxReject
from mempool2 import MempoolEntry, MempoolStats, MempoolStorage, TxSource, admit_tx

__all__ = ["Mempool2Service", "get_mempool2_service"]

log = logging.getLogger(__name__)
_DEBUG = os.environ.get("ANIMICA_DEBUG_TX") == "1"


class Mempool2Service:
    """
    Singleton service managing mempool2 instance.
    
    Provides high-level API for RPC handlers to interact with mempool2:
    - admit_tx: Submit transaction for admission
    - get_tx: Retrieve transaction by ID
    - get_stats: Get mempool statistics
    - list_txs: List all transactions
    """
    
    def __init__(
        self,
        storage_path: str | Path,
        chain_id: int = 1,
        max_tx_bytes: int = 128 * 1024,
        min_fee_rate: int = 1,
    ):
        """
        Initialize mempool2 service.
        
        Args:
            storage_path: Path to SQLite database
            chain_id: Expected chain ID
            max_tx_bytes: Maximum transaction size
            min_fee_rate: Minimum fee rate (wei/gas)
        """
        self.storage = MempoolStorage(storage_path)
        self.chain_id = chain_id
        self.max_tx_bytes = max_tx_bytes
        self.min_fee_rate = min_fee_rate
        
        if _DEBUG:
            log.info(
                f"Initialized Mempool2Service: "
                f"chain_id={chain_id}, "
                f"storage={storage_path}"
            )
    
    def admit_tx(
        self,
        envelope: TxEnvelope,
        source: TxSource = TxSource.RPC,
        peer_id: Optional[str] = None,
    ) -> tuple[bool, Optional[TxReject]]:
        """
        Admit transaction to mempool.
        
        Args:
            envelope: Transaction envelope
            source: Source of transaction (RPC, P2P, etc.)
            peer_id: Optional peer ID if source is P2P
            
        Returns:
            (True, None) if admitted
            (False, TxReject) if rejected with reason
        """
        if _DEBUG:
            log.debug(
                f"Admitting tx {envelope.txid.hex()[:16]}... "
                f"from {source.value}, peer={peer_id}"
            )
        
        # Call core admission function
        success, rejection = admit_tx(
            envelope=envelope,
            storage=self.storage,
            chain_id=self.chain_id,
            source=source,
            peer_id=peer_id,
        )
        
        if _DEBUG:
            if success:
                log.debug(f"Admitted tx {envelope.txid.hex()[:16]}...")
            else:
                log.debug(
                    f"Rejected tx {envelope.txid.hex()[:16]}...: "
                    f"{rejection.reason.value if rejection else 'unknown'}"
                )
        
        return success, rejection
    
    def get_tx(self, txid: TxId) -> Optional[MempoolEntry]:
        """
        Retrieve transaction from mempool.
        
        Args:
            txid: Transaction ID
            
        Returns:
            MempoolEntry if found, None otherwise
        """
        return self.storage.get_tx(txid)
    
    def has_tx(self, txid: TxId) -> bool:
        """
        Check if transaction exists in mempool.
        
        Args:
            txid: Transaction ID
            
        Returns:
            True if exists, False otherwise
        """
        return self.storage.has_tx(txid)
    
    def get_stats(self) -> MempoolStats:
        """
        Get mempool statistics.
        
        Returns:
            MempoolStats snapshot
        """
        return self.storage.get_stats()
    
    def list_txs(self, limit: Optional[int] = None) -> list[MempoolEntry]:
        """
        List transactions in mempool.
        
        Args:
            limit: Optional maximum number to return
            
        Returns:
            List of mempool entries
        """
        return self.storage.list_txs(limit=limit)
    
    def close(self) -> None:
        """Close storage connection"""
        self.storage.close()


# Singleton instance
_mempool2_service: Optional[Mempool2Service] = None


def get_mempool2_service() -> Mempool2Service:
    """
    Get or create the singleton mempool2 service.
    
    Reads configuration from environment:
    - ANIMICA_MEMPOOL2_DB_PATH: Database path (default: ./data/mempool2.db)
    - ANIMICA_CHAIN_ID: Chain ID (default: 1)
    - ANIMICA_MAX_TX_BYTES: Max tx size (default: 131072)
    - ANIMICA_MIN_FEE_RATE: Min fee rate (default: 1)
    
    Returns:
        Mempool2Service singleton
    """
    global _mempool2_service
    
    if _mempool2_service is None:
        # Read config from environment
        from rpc.deps import resolve_data_dir
        db_path = os.environ.get("ANIMICA_MEMPOOL2_DB_PATH", str(resolve_data_dir() / "mempool" / "mempool2.db"))
        chain_id = int(os.environ.get("ANIMICA_CHAIN_ID", "1"))
        max_tx_bytes = int(os.environ.get("ANIMICA_MAX_TX_BYTES", "131072"))
        min_fee_rate = int(os.environ.get("ANIMICA_MIN_FEE_RATE", "1"))
        
        _mempool2_service = Mempool2Service(
            storage_path=db_path,
            chain_id=chain_id,
            max_tx_bytes=max_tx_bytes,
            min_fee_rate=min_fee_rate,
        )
        
        log.info(f"Created Mempool2Service singleton: chain_id={chain_id}")
    
    return _mempool2_service
