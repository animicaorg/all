"""
mempool2.types - Mempool Data Models
====================================

Core data structures for mempool entries and statistics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from coretx import TxEnvelope, TxId

__all__ = [
    "TxSource",
    "MempoolEntry",
    "MempoolStats",
    "FeeStats",
]


class TxSource(str, Enum):
    """Source of a transaction entering the mempool"""
    RPC = "rpc"  # Submitted via JSON-RPC
    P2P = "p2p"  # Received from peer
    LOCAL = "local"  # Locally generated
    RESUBMIT = "resubmit"  # Resubmitted after reorg


@dataclass(frozen=True)
class MempoolEntry:
    """
    Mempool entry containing transaction envelope + metadata.
    
    This is what gets stored in the mempool and used for sorting/eviction.
    """
    envelope: TxEnvelope
    arrival_time: float  # unix timestamp
    fee_rate: int  # fee per gas unit (wei/gas)
    source: TxSource
    peer_id: Optional[str] = None  # If source=P2P, the peer ID
    
    @property
    def txid(self) -> TxId:
        """Convenience accessor for transaction ID"""
        return self.envelope.txid
    
    @property
    def sender(self) -> bytes:
        """Convenience accessor for sender address"""
        return self.envelope.body.from_addr
    
    @property
    def nonce(self) -> int:
        """Convenience accessor for nonce"""
        return self.envelope.body.nonce
    
    @property
    def fee(self) -> int:
        """Convenience accessor for fee"""
        return self.envelope.body.fee
    
    @property
    def gas_limit(self) -> int:
        """Convenience accessor for gas limit"""
        return self.envelope.body.gas_limit
    
    def size_bytes(self) -> int:
        """Estimate size of this entry in bytes (body + auth)"""
        # Approximate: fields + data + signature
        body = self.envelope.body
        auth = self.envelope.auth
        return (
            32 + 32  # from_addr + to_addr
            + len(body.data)
            + len(body.memo.encode('utf-8'))
            + len(auth.pubkey_bytes)
            + len(auth.signature_bytes)
            + 64  # metadata overhead
        )


@dataclass
class FeeStats:
    """Fee statistics for the mempool"""
    min_fee_rate: int = 0
    max_fee_rate: int = 0
    median_fee_rate: int = 0
    mean_fee_rate: int = 0


@dataclass
class MempoolStats:
    """
    Mempool statistics snapshot.
    
    Useful for monitoring, debugging, and API responses.
    """
    tx_count: int = 0
    total_bytes: int = 0
    unique_senders: int = 0
    fee_stats: FeeStats = field(default_factory=FeeStats)
    
    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict"""
        return {
            "tx_count": self.tx_count,
            "total_bytes": self.total_bytes,
            "unique_senders": self.unique_senders,
            "fee_stats": {
                "min": self.fee_stats.min_fee_rate,
                "max": self.fee_stats.max_fee_rate,
                "median": self.fee_stats.median_fee_rate,
                "mean": self.fee_stats.mean_fee_rate,
            },
        }
