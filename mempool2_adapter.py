"""
Compatibility Bridge: Old Mempool → Mempool2
=============================================

This module provides a compatibility adapter so existing code can continue
using the old mempool API while actually delegating to mempool2 underneath.

This allows gradual migration without breaking existing integrations.
"""

from __future__ import annotations

import logging
from typing import Optional, Any

# Import old mempool types
try:
    from mempool.errors import MempoolError, AdmissionError
except ImportError:
    # Define minimal fallbacks
    class MempoolError(Exception):
        pass
    
    class AdmissionError(MempoolError):
        pass

# Import new system
from coretx import TxEnvelope, TxId, RejectReason
from coretx.canonical import decode_tx_envelope, encode_tx_envelope
from coretx.signing import verify_tx
from mempool2 import admit_tx, MempoolStorage, TxSource
from mempool2.types import MempoolEntry

log = logging.getLogger(__name__)

__all__ = [
    "Mempool2Adapter",
    "get_mempool2_adapter",
]


class Mempool2Adapter:
    """
    Adapter that implements the old mempool interface but uses mempool2 underneath.
    
    This allows existing code to continue working without modification while
    benefiting from the improved error handling and admission logic of mempool2.
    """
    
    def __init__(self, storage_path: str = "./data/mempool2.db", chain_id: int = 1):
        """
        Initialize the adapter.
        
        Args:
            storage_path: Path to SQLite database
            chain_id: Expected chain ID for transactions
        """
        self.storage = MempoolStorage(storage_path)
        self.chain_id = chain_id
        log.info(f"Mempool2Adapter initialized: storage={storage_path}, chain_id={chain_id}")
    
    def add_tx(self, tx_bytes: bytes, source: str = "rpc") -> str:
        """
        Add a transaction (old interface).
        
        Args:
            tx_bytes: Raw CBOR transaction bytes
            source: Source identifier ("rpc", "p2p", etc.)
        
        Returns:
            Transaction ID as hex string
        
        Raises:
            AdmissionError: If transaction is rejected
        """
        try:
            # Decode envelope
            envelope = decode_tx_envelope(tx_bytes)
            
            # Convert source to TxSource enum
            tx_source = TxSource.RPC if source == "rpc" else TxSource.P2P
            
            # Admit to mempool2
            admitted, rejection = admit_tx(
                envelope,
                self.storage,
                self.chain_id,
                source=tx_source,
            )
            
            if not admitted and rejection:
                # Convert TxReject to AdmissionError for backwards compatibility
                raise AdmissionError(
                    f"{rejection.reason.value}: {rejection.message}",
                    context=rejection.context,
                )
            
            return envelope.txid.hex()
        
        except AdmissionError:
            # Re-raise AdmissionError as-is
            raise
        except Exception as e:
            # Wrap any other exceptions
            log.error(f"Unexpected error in add_tx: {type(e).__name__}: {e}")
            raise AdmissionError(
                f"Internal error during admission: {type(e).__name__}",
                context={"error_class": type(e).__name__, "error": str(e)},
            ) from e
    
    def has_tx(self, txid: str | bytes) -> bool:
        """Check if transaction is in mempool"""
        try:
            if isinstance(txid, str):
                if txid.startswith("0x"):
                    txid = txid[2:]
                txid_bytes = bytes.fromhex(txid)
            else:
                txid_bytes = txid
            
            return self.storage.has_tx(TxId(bytes32=txid_bytes))
        except Exception as e:
            log.warning(f"Error in has_tx: {e}")
            return False
    
    def get_tx(self, txid: str | bytes) -> Optional[bytes]:
        """Get transaction bytes by ID"""
        try:
            if isinstance(txid, str):
                if txid.startswith("0x"):
                    txid = txid[2:]
                txid_bytes = bytes.fromhex(txid)
            else:
                txid_bytes = txid
            
            entry = self.storage.get_tx(TxId(bytes32=txid_bytes))
            if entry is None:
                return None
            
            return encode_tx_envelope(entry.envelope)
        except Exception as e:
            log.warning(f"Error in get_tx: {e}")
            return None
    
    def remove_tx(self, txid: str | bytes) -> bool:
        """Remove transaction from mempool"""
        try:
            if isinstance(txid, str):
                if txid.startswith("0x"):
                    txid = txid[2:]
                txid_bytes = bytes.fromhex(txid)
            else:
                txid_bytes = txid
            
            self.storage.remove_tx(TxId(bytes32=txid_bytes))
            return True
        except Exception as e:
            log.warning(f"Error in remove_tx: {e}")
            return False
    
    def get_stats(self) -> dict[str, Any]:
        """Get mempool statistics"""
        try:
            stats = self.storage.get_stats()
            return {
                "count": stats.count,
                "total_bytes": stats.total_bytes,
                "min_fee_rate": stats.min_fee_rate,
                "max_fee_rate": stats.max_fee_rate,
                "avg_fee_rate": stats.avg_fee_rate,
            }
        except Exception as e:
            log.warning(f"Error in get_stats: {e}")
            return {"count": 0, "total_bytes": 0}
    
    def clear(self) -> None:
        """Clear all transactions from mempool"""
        try:
            self.storage.clear()
            log.info("Mempool cleared")
        except Exception as e:
            log.error(f"Error in clear: {e}")


# Singleton instance
_adapter: Optional[Mempool2Adapter] = None


def get_mempool2_adapter(
    storage_path: str = "./data/mempool2.db",
    chain_id: int = 1,
) -> Mempool2Adapter:
    """
    Get the singleton Mempool2Adapter instance.
    
    Args:
        storage_path: Path to SQLite database
        chain_id: Expected chain ID
    
    Returns:
        Singleton adapter instance
    """
    global _adapter
    if _adapter is None:
        _adapter = Mempool2Adapter(storage_path, chain_id)
    return _adapter
