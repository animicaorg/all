from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
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
    
    def clear_old_pending(self, max_age_seconds: float = 3600) -> int:
        """Clear old pending transactions that are likely stale."""
        now = time.time()
        removed = 0
        
        to_remove = []
        for tx_hash, entry in self._entries.items():
            if entry.status == "pending" and (now - entry.timestamp) > max_age_seconds:
                to_remove.append(tx_hash)
        
        for tx_hash in to_remove:
            del self._entries[tx_hash]
            removed += 1
        
        if removed > 0:
            self._save()
        
        return removed
