"""PTL data models for transaction lifecycle and replication tracking."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional


class TxStatus(str, enum.Enum):
    """Transaction status lifecycle in PTL."""

    NEW = "NEW"  # Just received, not yet stored
    STORED = "STORED"  # Durably stored in PTL
    ANNOUNCED = "ANNOUNCED"  # Announced to at least one peer
    REPLICATING = "REPLICATING"  # Being replicated to peers
    ATTESTED = "ATTESTED"  # Confirmed by minimum peers
    INCLUDED = "INCLUDED"  # Included in a block
    FINALIZED = "FINALIZED"  # Block finalized
    REJECTED = "REJECTED"  # Rejected (invalid)
    EXPIRED = "EXPIRED"  # Expired (TTL exceeded)


@dataclass(frozen=True)
class ReplicationReceipt:
    """Receipt of transaction replication from a peer."""

    peer_id: str
    txid: bytes
    timestamp: float
    status: str  # "ack", "reject", "timeout"
    reason: Optional[str] = None


@dataclass
class PtlEntry:
    """A transaction entry in the PTL."""

    txid: bytes
    tx_bytes: bytes
    status: TxStatus
    received_at: float
    updated_at: float
    origin: str
    fee: int = 0
    size: int = 0
    nonce: Optional[int] = None
    sender: Optional[bytes] = None
    receipts: list[ReplicationReceipt] = field(default_factory=list)
    reject_reason: Optional[str] = None
    included_height: Optional[int] = None
    finalized_height: Optional[int] = None
    expire_at: Optional[float] = None

    def add_receipt(self, receipt: ReplicationReceipt) -> None:
        """Add a replication receipt."""
        self.receipts.append(receipt)

    def update_status(self, new_status: TxStatus, updated_at: float) -> None:
        """Update transaction status."""
        self.status = new_status
        self.updated_at = updated_at

    def is_terminal(self) -> bool:
        """Check if transaction is in a terminal state."""
        return self.status in {
            TxStatus.INCLUDED,
            TxStatus.FINALIZED,
            TxStatus.REJECTED,
            TxStatus.EXPIRED,
        }

    def ack_count(self) -> int:
        """Count of successful acknowledgments."""
        return sum(1 for r in self.receipts if r.status == "ack")


__all__ = ["TxStatus", "PtlEntry", "ReplicationReceipt"]
