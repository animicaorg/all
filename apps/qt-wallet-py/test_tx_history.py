#!/usr/bin/env python3
"""Test transaction history implementation."""

import json
import tempfile
import time
from pathlib import Path

# Inline the TxHistory implementation for testing
from dataclasses import dataclass
from typing import Any


@dataclass
class TxHistoryEntry:
    """A single transaction history entry."""
    tx_hash: str
    from_addr: str
    to_addr: str | None
    value: int
    status: str  # "pending", "confirmed", "failed"
    timestamp: float
    block_number: int | None = None
    gas_limit: int | None = None
    max_fee: int | None = None
    nonce: int | None = None
    error: str | None = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "tx_hash": self.tx_hash,
            "from": self.from_addr,
            "to": self.to_addr,
            "value": self.value,
            "status": self.status,
            "timestamp": self.timestamp,
            "block_number": self.block_number,
            "gas_limit": self.gas_limit,
            "max_fee": self.max_fee,
            "nonce": self.nonce,
            "error": self.error,
        }
    
    @staticmethod
    def from_dict(d: dict[str, Any]) -> "TxHistoryEntry":
        return TxHistoryEntry(
            tx_hash=d["tx_hash"],
            from_addr=d["from"],
            to_addr=d.get("to"),
            value=int(d["value"]),
            status=d["status"],
            timestamp=float(d["timestamp"]),
            block_number=int(d["block_number"]) if d.get("block_number") is not None else None,
            gas_limit=int(d["gas_limit"]) if d.get("gas_limit") is not None else None,
            max_fee=int(d["max_fee"]) if d.get("max_fee") is not None else None,
            nonce=int(d["nonce"]) if d.get("nonce") is not None else None,
            error=d.get("error"),
        )


class TxHistory:
    """Local transaction history tracker."""
    
    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, TxHistoryEntry] = {}
        self._load()
    
    def _load(self) -> None:
        """Load transaction history from disk."""
        if not self._path.exists():
            return
        
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for entry_dict in data.get("transactions", []):
                entry = TxHistoryEntry.from_dict(entry_dict)
                self._entries[entry.tx_hash] = entry
        except Exception:
            # If corrupted, start fresh
            pass
    
    def _save(self) -> None:
        """Save transaction history to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "transactions": [entry.to_dict() for entry in self._entries.values()],
        }
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    
    def add_pending(
        self,
        tx_hash: str,
        from_addr: str,
        to_addr: str | None,
        value: int,
        gas_limit: int | None = None,
        max_fee: int | None = None,
        nonce: int | None = None,
    ) -> None:
        """Add a pending transaction to history."""
        entry = TxHistoryEntry(
            tx_hash=tx_hash,
            from_addr=from_addr,
            to_addr=to_addr,
            value=value,
            status="pending",
            timestamp=time.time(),
            gas_limit=gas_limit,
            max_fee=max_fee,
            nonce=nonce,
        )
        self._entries[tx_hash] = entry
        self._save()
    
    def update_status(
        self,
        tx_hash: str,
        status: str,
        block_number: int | None = None,
        error: str | None = None,
    ) -> None:
        """Update the status of a transaction."""
        if tx_hash not in self._entries:
            return
        
        entry = self._entries[tx_hash]
        entry.status = status
        if block_number is not None:
            entry.block_number = block_number
        if error is not None:
            entry.error = error
        self._save()
    
    def get(self, tx_hash: str) -> TxHistoryEntry | None:
        """Get a transaction by hash."""
        return self._entries.get(tx_hash)
    
    def list(
        self,
        address: str | None = None,
        limit: int = 100,
        offset: int = 0,
        status_filter: str | None = None,
    ) -> list[TxHistoryEntry]:
        """List transactions, optionally filtered by address or status."""
        entries = list(self._entries.values())
        
        # Filter by address if provided
        if address:
            entries = [
                e for e in entries
                if e.from_addr == address or e.to_addr == address
            ]
        
        # Filter by status if provided
        if status_filter:
            entries = [e for e in entries if e.status == status_filter]
        
        # Sort by timestamp (newest first)
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        
        # Apply pagination
        return entries[offset:offset + limit]


def test_tx_history():
    """Test the TxHistory class."""
    print("Testing TxHistory implementation...")
    
    # Create a temporary directory for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "tx_history.json"
        history = TxHistory(history_path)
        
        # Test adding a pending transaction
        print("\n1. Testing add_pending...")
        history.add_pending(
            tx_hash="0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
            from_addr="anim1qyfe6g57j7n9vqqw4n0pxq9eukxm4s7q8xw8z",
            to_addr="anim1qyfe6g57j7n9vqqw4n0pxq9eukxm4s7q8xw9a",
            value=1_000_000_000_000_000_000,  # 1 ANIM
            gas_limit=21000,
            max_fee=1_000_000_000,
            nonce=0,
        )
        
        # Verify the transaction was added
        entry = history.get("0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef")
        assert entry is not None, "Transaction should be added"
        assert entry.status == "pending", "Status should be pending"
        assert entry.value == 1_000_000_000_000_000_000, "Value should match"
        print("✓ Transaction added successfully")
        
        # Test listing transactions
        print("\n2. Testing list...")
        entries = history.list()
        assert len(entries) == 1, "Should have 1 transaction"
        assert entries[0].tx_hash == "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        print(f"✓ Listed {len(entries)} transaction(s)")
        
        # Test filtering by status
        print("\n3. Testing filter by status...")
        pending = history.list(status_filter="pending")
        assert len(pending) == 1, "Should have 1 pending transaction"
        confirmed = history.list(status_filter="confirmed")
        assert len(confirmed) == 0, "Should have 0 confirmed transactions"
        print("✓ Status filtering works")
        
        # Test updating status
        print("\n4. Testing update_status...")
        history.update_status(
            "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
            "confirmed",
            block_number=12345,
        )
        entry = history.get("0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef")
        assert entry.status == "confirmed", "Status should be updated to confirmed"
        assert entry.block_number == 12345, "Block number should be set"
        print("✓ Status updated successfully")
        
        # Test filtering by address
        print("\n5. Testing filter by address...")
        entries = history.list(address="anim1qyfe6g57j7n9vqqw4n0pxq9eukxm4s7q8xw8z")
        assert len(entries) == 1, "Should find transaction for from address"
        entries = history.list(address="anim1qyfe6g57j7n9vqqw4n0pxq9eukxm4s7q8xw9a")
        assert len(entries) == 1, "Should find transaction for to address"
        entries = history.list(address="anim1qyfe6g57j7n9vqqw4n0pxq9eukxm4s7q8xw9b")
        assert len(entries) == 0, "Should not find transaction for unrelated address"
        print("✓ Address filtering works")
        
        # Test persistence
        print("\n6. Testing persistence...")
        history2 = TxHistory(history_path)
        entries = history2.list()
        assert len(entries) == 1, "Should load persisted transaction"
        assert entries[0].status == "confirmed", "Status should be persisted"
        print("✓ Persistence works")
        
        # Test pagination
        print("\n7. Testing pagination...")
        # Add more transactions
        for i in range(10):
            history.add_pending(
                tx_hash=f"0x{i:064x}",
                from_addr="anim1qyfe6g57j7n9vqqw4n0pxq9eukxm4s7q8xw8z",
                to_addr="anim1qyfe6g57j7n9vqqw4n0pxq9eukxm4s7q8xw9a",
                value=i * 1_000_000_000_000_000_000,
                gas_limit=21000,
                max_fee=1_000_000_000,
                nonce=i + 1,
            )
        
        page1 = history.list(limit=5, offset=0)
        assert len(page1) == 5, "Should return 5 transactions"
        page2 = history.list(limit=5, offset=5)
        assert len(page2) == 5, "Should return next 5 transactions"
        print("✓ Pagination works")
        
        print("\n✅ All tests passed!")


if __name__ == "__main__":
    test_tx_history()
