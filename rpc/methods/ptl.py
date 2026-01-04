"""PTL RPC methods for Pending Transaction Ledger."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from rpc import deps
from rpc.methods import method
from core.ptl.service import PtlService
from core.ptl.model import TxStatus

log = logging.getLogger(__name__)


def _get_ptl_service() -> Optional[PtlService]:
    """Get PTL service from deps if available."""
    try:
        return deps.get("ptl_service")
    except Exception:
        return None


@method(name="tx.submitRawTransaction")
async def tx_submit_raw(params: Dict[str, Any]) -> Dict[str, Any]:
    """Submit a raw transaction to the PTL.
    
    Args:
        params: {
            "tx": hex-encoded or bytes transaction data,
            "origin": optional origin identifier
        }
    
    Returns:
        {
            "txid": transaction ID (hex),
            "status": current status,
            "received_at": timestamp
        }
    """
    ptl = _get_ptl_service()
    if not ptl:
        raise RuntimeError("PTL service not available")
    
    tx_data = params.get("tx")
    if not tx_data:
        raise ValueError("tx parameter required")
    
    # Handle hex-encoded strings
    if isinstance(tx_data, str):
        if tx_data.startswith("0x"):
            tx_data = tx_data[2:]
        tx_bytes = bytes.fromhex(tx_data)
    else:
        tx_bytes = bytes(tx_data)
    
    origin = params.get("origin", "rpc")
    
    txid, entry = await ptl.submit(tx_bytes, origin=origin)
    
    return {
        "txid": f"0x{txid.hex()}",
        "status": entry.status.value,
        "received_at": entry.received_at,
        "expire_at": entry.expire_at,
    }


@method(name="tx.get")
async def tx_get(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Get a transaction by ID from PTL.
    
    Args:
        params: {"txid": transaction ID (hex)}
    
    Returns:
        Transaction details or None if not found
    """
    ptl = _get_ptl_service()
    if not ptl:
        raise RuntimeError("PTL service not available")
    
    txid_hex = params.get("txid", "")
    if txid_hex.startswith("0x"):
        txid_hex = txid_hex[2:]
    
    txid = bytes.fromhex(txid_hex)
    entry = await ptl.get(txid)
    
    if not entry:
        return None
    
    return {
        "txid": f"0x{entry.txid.hex()}",
        "status": entry.status.value,
        "received_at": entry.received_at,
        "updated_at": entry.updated_at,
        "origin": entry.origin,
        "size": entry.size,
        "fee": entry.fee,
        "ack_count": entry.ack_count(),
        "reject_reason": entry.reject_reason,
        "included_height": entry.included_height,
        "finalized_height": entry.finalized_height,
        "expire_at": entry.expire_at,
    }


@method(name="tx.pending")
async def tx_pending(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get pending transactions from PTL.
    
    Args:
        params: {
            "limit": max number of transactions (default 100),
            "status": optional status filter
        }
    
    Returns:
        List of pending transactions
    """
    ptl = _get_ptl_service()
    if not ptl:
        raise RuntimeError("PTL service not available")
    
    limit = params.get("limit", 100)
    status_filter = params.get("status")
    
    if status_filter:
        # Filter by specific status
        try:
            status = TxStatus(status_filter)
            entries = ptl.store.list_by_status(status, limit=limit)
        except ValueError:
            raise ValueError(f"Invalid status: {status_filter}")
    else:
        # Get all pending
        entries = await ptl.get_pending(limit=limit)
    
    return {
        "transactions": [
            {
                "txid": f"0x{entry.txid.hex()}",
                "status": entry.status.value,
                "received_at": entry.received_at,
                "size": entry.size,
                "fee": entry.fee,
                "ack_count": entry.ack_count(),
            }
            for entry in entries
        ],
        "total": len(entries),
    }


@method(name="ptl.replicationStatus")
async def ptl_replication_status(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Get detailed replication status for a transaction.
    
    This is the canonical PTL replication status method with full schema:
    - tx_hash: transaction hash
    - local_status: unknown|seen|eligible|mined|dropped
    - peers[]: per-peer receipts with status, timestamps, connection info
    - quorum: required_acks, observed_acks, quorum_met
    - persistence: stored_receipts_count, backend info
    - mined: block height, finalization status if available
    
    Args:
        params: {"txid": transaction ID (hex)}
    
    Returns:
        Detailed replication status with enhanced schema
    """
    ptl = _get_ptl_service()
    if not ptl:
        raise RuntimeError("PTL service not available")
    
    txid_hex = params.get("txid", "")
    if txid_hex.startswith("0x"):
        txid_hex = txid_hex[2:]
    
    txid = bytes.fromhex(txid_hex)
    entry = await ptl.get(txid)
    
    if not entry:
        return {
            "tx_hash": f"0x{txid_hex}",
            "local_status": "unknown",
            "peers": [],
            "quorum": {
                "required_acks": ptl.min_peer_acks,
                "observed_acks": 0,
                "quorum_met": False,
            },
            "persistence": {
                "stored_receipts_count": 0,
            },
        }
    
    # Map TxStatus to local_status
    status_map = {
        "NEW": "seen",
        "STORED": "seen",
        "ANNOUNCED": "eligible",
        "REPLICATING": "eligible",
        "ATTESTED": "eligible",
        "INCLUDED": "mined",
        "FINALIZED": "mined",
        "REJECTED": "dropped",
        "EXPIRED": "dropped",
    }
    local_status = status_map.get(entry.status.value, "unknown")
    
    # Build peer receipts with enhanced info
    peers = []
    for r in entry.receipts:
        peer_info = {
            "peer_id": r.peer_id,
            "status": r.status,  # "seen" | "acked" | "missing" | "failed"
            "first_seen_ts": r.timestamp,
            "last_update_ts": r.timestamp,
        }
        if r.reason:
            peer_info["reason"] = r.reason
        peers.append(peer_info)
    
    # Quorum information
    ack_count = entry.ack_count()
    quorum_met = ack_count >= ptl.min_peer_acks
    
    result = {
        "tx_hash": f"0x{entry.txid.hex()}",
        "local_status": local_status,
        "peers": peers,
        "quorum": {
            "required_acks": ptl.min_peer_acks,
            "observed_acks": ack_count,
            "quorum_met": quorum_met,
        },
        "persistence": {
            "stored_receipts_count": len(entry.receipts),
            "store_backend": "sqlite",
        },
    }
    
    # Add mined info if available
    if entry.included_height is not None:
        result["mined"] = {
            "block_height": entry.included_height,
            "finalized": entry.finalized_height is not None,
            "finalized_height": entry.finalized_height,
        }
    
    # Add timing info
    result["received_at"] = entry.received_at
    result["updated_at"] = entry.updated_at
    if entry.expire_at:
        result["expire_at"] = entry.expire_at
    
    return result


@method(name="tx.replicationStatus")
async def tx_replication_status(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Backward-compatible alias for ptl.replicationStatus.
    
    Args:
        params: {"txid": transaction ID (hex)}
    
    Returns:
        Detailed replication status (delegates to ptl.replicationStatus)
    """
    log.debug("tx.replicationStatus called, forwarding to ptl.replicationStatus")
    return await ptl_replication_status(params)


@method(name="debug.ptlStats")
async def debug_ptl_stats(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get PTL statistics.
    
    Returns:
        PTL statistics by status
    """
    ptl = _get_ptl_service()
    if not ptl:
        raise RuntimeError("PTL service not available")
    
    stats = ptl.get_stats()
    
    return {
        "stats": stats,
        "min_peer_acks": ptl.min_peer_acks,
        "ttl_seconds": ptl.ttl_seconds,
    }


@method(name="debug.ptlPeers")
async def debug_ptl_peers(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get PTL peer information.
    
    Returns:
        PTL peer relay state
    """
    try:
        relay = deps.get("ptl_relay")
        if not relay:
            return {"peers": [], "error": "PTL relay not available"}
        
        snapshot = relay.snapshot()
        return snapshot
    except Exception as exc:
        log.warning("Failed to get PTL relay snapshot", exc_info=True)
        return {"peers": [], "error": str(exc)}


# Compatibility shims for old mempool RPC
@method(name="mempool.add")
async def mempool_add_compat(params: Dict[str, Any]) -> Dict[str, Any]:
    """Compatibility shim for old mempool.add → tx.submitRawTransaction."""
    log.debug("mempool.add called, forwarding to tx.submitRawTransaction")
    return await tx_submit_raw(params)


@method(name="mempool.get")
async def mempool_get_compat(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Compatibility shim for old mempool.get → tx.get."""
    log.debug("mempool.get called, forwarding to tx.get")
    return await tx_get(params)


@method(name="mempool.list")
async def mempool_list_compat(params: Dict[str, Any]) -> Dict[str, Any]:
    """Compatibility shim for old mempool.list → tx.pending."""
    log.debug("mempool.list called, forwarding to tx.pending")
    return await tx_pending(params)


__all__ = [
    "tx_submit_raw",
    "tx_get",
    "tx_pending",
    "ptl_replication_status",
    "tx_replication_status",
    "debug_ptl_stats",
    "debug_ptl_peers",
    "mempool_add_compat",
    "mempool_get_compat",
    "mempool_list_compat",
]
