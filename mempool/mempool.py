from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Callable

from mempool.tx_hash import normalized_tx_bytes, tx_hash_bytes

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MempoolEntry:
    txid: bytes
    tx_bytes: bytes
    origin: str
    received_at: float


class Mempool:
    def __init__(self, *, max_tx_bytes: int = 1_048_576, 
                 balance_refund_callback: Optional[Callable[[bytes, bytes], None]] = None) -> None:
        self.max_tx_bytes = int(max_tx_bytes)
        self._txs: Dict[bytes, MempoolEntry] = {}
        self._balance_refund_callback = balance_refund_callback

    def add_tx(self, tx: bytes, origin: str) -> bytes:
        raw = normalized_tx_bytes(tx)
        if len(raw) > self.max_tx_bytes:
            raise ValueError("tx too large")
        txid = tx_hash_bytes(raw)
        if txid not in self._txs:
            self._txs[txid] = MempoolEntry(
                txid=txid, tx_bytes=raw, origin=str(origin), received_at=time.time()
            )
        return txid

    def has(self, txid: bytes) -> bool:
        return txid in self._txs

    def get(self, txid: bytes) -> Optional[MempoolEntry]:
        return self._txs.get(txid)

    def list(self) -> List[MempoolEntry]:
        return list(self._txs.values())

    def drop_tx(self, txid: bytes, *, refund_balance: bool = True) -> bool:
        """
        Drop a transaction from the mempool.
        
        Args:
            txid: Transaction ID to drop
            refund_balance: If True, refund the sender's balance (default: True)
            
        Returns:
            True if transaction was dropped, False if not found
        """
        entry = self._txs.pop(txid, None)
        if entry is None:
            return False
            
        if refund_balance and self._balance_refund_callback:
            try:
                # Extract sender from transaction bytes
                sender = self._extract_sender(entry.tx_bytes)
                if sender:
                    self._balance_refund_callback(txid, sender)
                    log.info(
                        "Dropped transaction and refunded balance",
                        extra={"txid": txid.hex(), "sender": sender.hex()}
                    )
            except Exception as e:
                log.warning(
                    "Failed to refund balance for dropped transaction",
                    extra={"txid": txid.hex(), "error": str(e)}
                )
        
        return True

    def drop_many(self, txids: List[bytes], *, refund_balance: bool = True) -> int:
        """
        Drop multiple transactions from the mempool.
        
        Args:
            txids: List of transaction IDs to drop
            refund_balance: If True, refund sender balances (default: True)
            
        Returns:
            Number of transactions dropped
        """
        dropped = 0
        for txid in txids:
            if self.drop_tx(txid, refund_balance=refund_balance):
                dropped += 1
        return dropped

    def _extract_sender(self, tx_bytes: bytes) -> Optional[bytes]:
        """
        Extract sender address from transaction bytes.
        
        Args:
            tx_bytes: Normalized transaction bytes
            
        Returns:
            Sender address bytes, or None if cannot extract
        """
        try:
            # Try to decode CBOR transaction
            import cbor2
            tx_obj = cbor2.loads(tx_bytes)
            
            # Handle different transaction envelope formats
            if isinstance(tx_obj, dict):
                # Look for sender in body
                body = tx_obj.get("body") or tx_obj.get("unsigned")
                if body and isinstance(body, dict):
                    sender = body.get("from") or body.get("sender") or body.get("from_")
                    if sender:
                        if isinstance(sender, bytes):
                            return sender
                        elif isinstance(sender, str):
                            # Try hex decode
                            sender_hex = sender[2:] if sender.startswith("0x") else sender
                            return bytes.fromhex(sender_hex)
        except Exception as e:
            log.debug("Failed to extract sender from tx", extra={"error": str(e)})
        
        return None


__all__ = ["Mempool", "MempoolEntry"]
