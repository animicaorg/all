"""PTL service for managing pending transactions."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from core.ptl.model import PtlEntry, ReplicationReceipt, TxStatus
from core.ptl.store import PtlStore
from core.utils.hash import sha3_256

log = logging.getLogger("animica.ptl.service")


class PtlService:
    """Service for managing the Pending Transaction Ledger."""

    def __init__(
        self,
        store: PtlStore,
        *,
        ttl_seconds: int = 3600,
        min_peer_acks: int = 2,
    ) -> None:
        self.store = store
        self.ttl_seconds = ttl_seconds
        self.min_peer_acks = min_peer_acks
        self._running = False
        self._lock = asyncio.Lock()

    async def submit(
        self, tx_bytes: bytes, origin: str = "local"
    ) -> tuple[bytes, PtlEntry]:
        """Submit a new transaction to the PTL."""
        txid = sha3_256(tx_bytes)
        now = time.time()

        async with self._lock:
            existing = self.store.get(txid)
            if existing:
                log.debug(
                    "PTL transaction already exists",
                    extra={"txid": txid.hex()[:16], "status": existing.status.value},
                )
                return txid, existing

            entry = PtlEntry(
                txid=txid,
                tx_bytes=tx_bytes,
                status=TxStatus.STORED,
                received_at=now,
                updated_at=now,
                origin=origin,
                size=len(tx_bytes),
                expire_at=now + self.ttl_seconds,
            )
            self.store.add(entry)
            log.info(
                "PTL transaction submitted",
                extra={
                    "txid": txid.hex()[:16],
                    "origin": origin,
                    "size": len(tx_bytes),
                },
            )
            return txid, entry

    async def get(self, txid: bytes) -> Optional[PtlEntry]:
        """Get a transaction by ID."""
        return self.store.get(txid)

    async def get_pending(self, limit: int = 100) -> list[PtlEntry]:
        """Get all pending transactions."""
        return self.store.list_pending(limit)

    async def get_for_mining(self, limit: int = 1000) -> list[PtlEntry]:
        """Get transactions ready for mining."""
        return self.store.list_for_mining(limit)

    async def update_status(
        self, txid: bytes, status: TxStatus, **kwargs
    ) -> bool:
        """Update transaction status."""
        now = time.time()
        async with self._lock:
            success = self.store.update_status(txid, status, now, **kwargs)
            if success:
                log.info(
                    "PTL status updated",
                    extra={"txid": txid.hex()[:16], "status": status.value},
                )
            return success

    async def add_receipt(
        self, txid: bytes, peer_id: str, status: str, reason: Optional[str] = None
    ) -> None:
        """Add a replication receipt."""
        receipt = ReplicationReceipt(
            peer_id=peer_id,
            txid=txid,
            timestamp=time.time(),
            status=status,
            reason=reason,
        )
        async with self._lock:
            self.store.add_receipt(receipt)
            entry = self.store.get(txid)
            if entry:
                ack_count = entry.ack_count() + (1 if status == "ack" else 0)
                if ack_count >= self.min_peer_acks and entry.status in {
                    TxStatus.STORED,
                    TxStatus.ANNOUNCED,
                    TxStatus.REPLICATING,
                }:
                    self.store.update_status(txid, TxStatus.ATTESTED, time.time())
                    log.info(
                        "PTL transaction attested",
                        extra={"txid": txid.hex()[:16], "acks": ack_count},
                    )

    async def mark_included(self, txid: bytes, height: int) -> bool:
        """Mark a transaction as included in a block."""
        return await self.update_status(
            txid, TxStatus.INCLUDED, included_height=height
        )

    async def mark_finalized(self, txid: bytes, height: int) -> bool:
        """Mark a transaction as finalized."""
        return await self.update_status(
            txid, TxStatus.FINALIZED, finalized_height=height
        )

    async def mark_rejected(self, txid: bytes, reason: str) -> bool:
        """Mark a transaction as rejected."""
        return await self.update_status(txid, TxStatus.REJECTED, reject_reason=reason)

    async def get_replication_status(self, txid: bytes) -> Optional[dict]:
        """Get detailed replication status."""
        entry = await self.get(txid)
        if not entry:
            return None

        return {
            "txid": entry.txid.hex(),
            "status": entry.status.value,
            "ack_count": entry.ack_count(),
            "min_peer_acks": self.min_peer_acks,
            "receipts": [
                {
                    "peer_id": r.peer_id,
                    "timestamp": r.timestamp,
                    "status": r.status,
                    "reason": r.reason,
                }
                for r in entry.receipts
            ],
            "received_at": entry.received_at,
            "updated_at": entry.updated_at,
            "expire_at": entry.expire_at,
        }

    async def maintenance_loop(self) -> None:
        """Background maintenance loop."""
        self._running = True
        while self._running:
            try:
                await asyncio.sleep(30)
                now = time.time()

                # Mark expired transactions
                expired_count = self.store.mark_expired(now)
                if expired_count > 0:
                    log.info("PTL expired transactions", extra={"count": expired_count})

                # Prune old terminal transactions (older than 1 hour)
                prune_before = now - 3600
                pruned_count = self.store.prune_terminal(prune_before)
                if pruned_count > 0:
                    log.info("PTL pruned transactions", extra={"count": pruned_count})

            except Exception:
                log.warning("PTL maintenance loop error", exc_info=True)

    def stop(self) -> None:
        """Stop the service."""
        self._running = False

    def get_stats(self) -> dict:
        """Get PTL statistics."""
        return self.store.get_stats()


__all__ = ["PtlService"]
