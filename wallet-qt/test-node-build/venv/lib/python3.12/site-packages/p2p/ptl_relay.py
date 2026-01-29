"""PTL P2P relay service for pull-based transaction replication."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Dict, Iterable, List, Optional, Set

from core.ptl.service import PtlService
from core.ptl.model import TxStatus

log = logging.getLogger("animica.p2p.ptl_relay")


@dataclass(slots=True)
class PeerPtlState:
    """PTL state tracking for a peer."""

    conn_id: str
    peer_node_id: Optional[str]
    direction: Optional[str]
    remote: Optional[str]
    announced_txids: Set[bytes] = field(default_factory=set)
    wanted_txids: Set[bytes] = field(default_factory=set)
    last_reconcile_at: float = 0.0
    last_announce_at: float = 0.0


PeerListFn = Callable[[], Iterable[str]]
PeerEligibleFn = Callable[[str], bool]
SendFn = Callable[[str, Any], Awaitable[None]]


class PtlRelayService:
    """PTL relay service for peer-to-peer transaction replication."""

    def __init__(
        self,
        ptl_service: PtlService,
        *,
        reconcile_interval_s: float = 10.0,
        announce_batch_size: int = 100,
        announce_interval_s: float = 1.0,
        max_push_batch: int = 50,
        peer_ids: PeerListFn,
        peer_eligible: PeerEligibleFn,
        send_announce: SendFn,
        send_want: SendFn,
        send_push: SendFn,
        send_ack: SendFn,
    ) -> None:
        self.ptl_service = ptl_service
        self.reconcile_interval_s = reconcile_interval_s
        self.announce_batch_size = announce_batch_size
        self.announce_interval_s = announce_interval_s
        self.max_push_batch = max_push_batch

        self._peer_ids = peer_ids
        self._peer_eligible = peer_eligible
        self._send_announce = send_announce
        self._send_want = send_want
        self._send_push = send_push
        self._send_ack = send_ack

        self._peer_state: Dict[str, PeerPtlState] = {}
        self._running = False
        self._lock = asyncio.Lock()

    def register_peer(
        self,
        conn_id: str,
        *,
        peer_node_id: Optional[str] = None,
        direction: Optional[str] = None,
        remote: Optional[str] = None,
    ) -> None:
        """Register a peer for PTL relay."""
        if conn_id not in self._peer_state:
            self._peer_state[conn_id] = PeerPtlState(
                conn_id=conn_id,
                peer_node_id=peer_node_id,
                direction=direction,
                remote=remote,
            )
            log.info(
                "PTL peer registered",
                extra={"conn_id": conn_id, "peer_id": peer_node_id},
            )

    def unregister_peer(self, conn_id: str) -> None:
        """Unregister a peer."""
        self._peer_state.pop(conn_id, None)
        log.info("PTL peer unregistered", extra={"conn_id": conn_id})

    def _eligible_peers(self) -> List[str]:
        """Get list of eligible peers."""
        return [p for p in self._peer_ids() if self._peer_eligible(p)]

    def _ensure_peer(self, conn_id: str) -> PeerPtlState:
        """Ensure peer state exists."""
        if conn_id not in self._peer_state:
            self._peer_state[conn_id] = PeerPtlState(
                conn_id=conn_id,
                peer_node_id=None,
                direction=None,
                remote=None,
            )
        return self._peer_state[conn_id]

    async def on_ptl_announce(self, conn_id: str, txids: Iterable[bytes]) -> None:
        """Handle PTL_ANNOUNCE message from peer."""
        tx_list = list(txids)
        log.info(
            "PTL_ANNOUNCE received",
            extra={
                "peer": conn_id,
                "count": len(tx_list),
                "first_3": [t.hex()[:16] for t in tx_list[:3]],
            },
        )

        async with self._lock:
            state = self._ensure_peer(conn_id)
            state.announced_txids.update(tx_list)

        # Check which transactions we don't have
        want: List[bytes] = []
        for txid in tx_list:
            entry = await self.ptl_service.get(txid)
            if entry is None:
                want.append(txid)

        if want:
            # Request transactions we don't have
            await self._send_want(conn_id, want)
            log.info(
                "PTL_WANT sent",
                extra={
                    "peer": conn_id,
                    "count": len(want),
                    "first_3": [t.hex()[:16] for t in want[:3]],
                },
            )

    async def on_ptl_want(self, conn_id: str, txids: Iterable[bytes]) -> None:
        """Handle PTL_WANT message from peer."""
        tx_list = list(txids)
        log.info(
            "PTL_WANT received",
            extra={"peer": conn_id, "count": len(tx_list)},
        )

        # Fetch requested transactions and push to peer
        items: List[dict] = []
        for txid in tx_list[:self.max_push_batch]:
            entry = await self.ptl_service.get(txid)
            if entry:
                items.append({"txid": entry.txid, "tx_bytes": entry.tx_bytes})

        if items:
            await self._send_push(conn_id, items)
            log.info(
                "PTL_PUSH sent",
                extra={
                    "peer": conn_id,
                    "count": len(items),
                    "first_3": [i["txid"].hex()[:16] for i in items[:3]],
                },
            )

    async def on_ptl_push(self, conn_id: str, items: Iterable[dict]) -> None:
        """Handle PTL_PUSH message from peer."""
        items_list = list(items)
        log.info(
            "PTL_PUSH received",
            extra={"peer": conn_id, "count": len(items_list)},
        )

        ack_txids: List[bytes] = []
        reject_txids: List[bytes] = []

        for item in items_list:
            txid = item.get("txid")
            tx_bytes = item.get("tx_bytes")

            if not isinstance(txid, (bytes, bytearray)) or not isinstance(
                tx_bytes, (bytes, bytearray)
            ):
                log.warning("PTL_PUSH invalid item", extra={"peer": conn_id})
                continue

            txid_bytes = bytes(txid)
            tx_bytes_data = bytes(tx_bytes)

            try:
                # Submit to PTL
                _, entry = await self.ptl_service.submit(tx_bytes_data, origin=f"peer:{conn_id}")
                
                # Add receipt
                await self.ptl_service.add_receipt(
                    txid_bytes, conn_id, "ack", reason="received"
                )
                
                ack_txids.append(txid_bytes)
                log.info(
                    "PTL transaction received",
                    extra={"peer": conn_id, "txid": txid_bytes.hex()[:16]},
                )
            except Exception as exc:
                reject_txids.append(txid_bytes)
                log.warning(
                    "PTL transaction rejected",
                    extra={
                        "peer": conn_id,
                        "txid": txid_bytes.hex()[:16],
                        "error": str(exc),
                    },
                )

        # Send acknowledgments
        if ack_txids:
            await self._send_ack(conn_id, {"txids": ack_txids, "status": "ack"})
            log.info(
                "PTL_ACK sent",
                extra={"peer": conn_id, "count": len(ack_txids), "status": "ack"},
            )

        if reject_txids:
            await self._send_ack(
                conn_id, {"txids": reject_txids, "status": "reject", "reason": "invalid"}
            )
            log.info(
                "PTL_ACK sent",
                extra={"peer": conn_id, "count": len(reject_txids), "status": "reject"},
            )

    async def on_ptl_ack(self, conn_id: str, data: dict) -> None:
        """Handle PTL_ACK message from peer."""
        txids = data.get("txids", [])
        status = data.get("status", "ack")
        reason = data.get("reason")

        log.info(
            "PTL_ACK received",
            extra={
                "peer": conn_id,
                "count": len(txids),
                "status": status,
                "reason": reason,
            },
        )

        # Record receipts for acknowledged transactions
        for txid in txids:
            await self.ptl_service.add_receipt(txid, conn_id, status, reason)

    async def reconcile_loop(self) -> None:
        """Background reconciliation loop."""
        self._running = True
        while self._running:
            try:
                await asyncio.sleep(1.0)
                now = time.time()

                async with self._lock:
                    peers = list(self._peer_state.values())

                for state in peers:
                    if not self._peer_eligible(state.conn_id):
                        continue

                    # Reconcile periodically
                    if now - state.last_reconcile_at >= self.reconcile_interval_s:
                        await self._reconcile_with_peer(state.conn_id)
                        state.last_reconcile_at = now

            except Exception:
                log.warning("PTL reconcile loop error", exc_info=True)

    async def _reconcile_with_peer(self, conn_id: str) -> None:
        """Reconcile PTL state with a peer."""
        # Get our pending transactions
        pending = await self.ptl_service.get_pending(limit=self.announce_batch_size)
        
        if pending:
            txids = [entry.txid for entry in pending]
            await self._send_announce(conn_id, txids)
            log.info(
                "PTL reconciliation",
                extra={
                    "peer": conn_id,
                    "announced": len(txids),
                    "first_3": [t.hex()[:16] for t in txids[:3]],
                },
            )

    async def announce_new_transaction(self, txid: bytes) -> None:
        """Announce a new transaction to all peers."""
        peers = self._eligible_peers()
        for conn_id in peers:
            try:
                await self._send_announce(conn_id, [txid])
                log.debug(
                    "PTL transaction announced",
                    extra={"peer": conn_id, "txid": txid.hex()[:16]},
                )
            except Exception:
                log.warning(
                    "PTL announce failed",
                    extra={"peer": conn_id, "txid": txid.hex()[:16]},
                    exc_info=True,
                )

    def stop(self) -> None:
        """Stop the relay service."""
        self._running = False

    def snapshot(self) -> dict[str, Any]:
        """Get snapshot of relay state."""
        peers = []
        for state in self._peer_state.values():
            peers.append(
                {
                    "conn_id": state.conn_id,
                    "peer_node_id": state.peer_node_id,
                    "announced_count": len(state.announced_txids),
                    "wanted_count": len(state.wanted_txids),
                    "last_reconcile_at": state.last_reconcile_at,
                }
            )
        return {"peers": peers}


__all__ = ["PtlRelayService"]
