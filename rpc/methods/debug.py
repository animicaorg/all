"""Debug RPC methods for transaction lifecycle tracing and diagnostics.

This module provides introspection endpoints for debugging transaction
propagation, mempool state, and cross-node mining scenarios.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from rpc.methods import method
from rpc import deps

log = logging.getLogger(__name__)


def _normalize_hash(h: Any) -> str:
    """Normalize hash to 0x-prefixed lowercase hex string."""
    if isinstance(h, bytes):
        return "0x" + h.hex().lower()
    if isinstance(h, str):
        h = h.lower()
        return h if h.startswith("0x") else f"0x{h}"
    return str(h)


def _hash_to_bytes(h: Any) -> bytes:
    """Convert hash to bytes."""
    if isinstance(h, bytes):
        return h
    if isinstance(h, str):
        h = h[2:] if h.startswith("0x") else h
        return bytes.fromhex(h)
    raise ValueError(f"Invalid hash type: {type(h)}")


@method(
    "debug.traceTx",
    desc="Trace the lifecycle of a transaction across mempool, P2P, and mining",
    aliases=("debug.trace_tx", "debug.txTrace"),
)
def debug_trace_tx(params: Any) -> Dict[str, Any]:
    """
    Trace a transaction's lifecycle from submission to confirmation.
    
    Returns:
        {
            "txid": "0x...",
            "status": "pending" | "mined" | "unknown",
            "lifecycle": {
                "received_at": timestamp,
                "peer_source": "local" | "peer-id",
                "mempool_status": "in_pool" | "not_found" | "evicted",
                "mempool_first_seen": timestamp,
                "p2p_events": [
                    {"type": "inv_sent", "peer": "...", "at": timestamp},
                    {"type": "get_received", "peer": "...", "at": timestamp},
                    {"type": "data_sent", "peer": "...", "at": timestamp}
                ],
                "mined_in_block": {
                    "height": number,
                    "hash": "0x...",
                    "miner": "0x...",
                    "timestamp": timestamp
                },
                "confirmations": number
            }
        }
    """
    # Parse params
    if isinstance(params, dict):
        txid = params.get("txid") or params.get("hash") or params.get("tx_hash")
    elif isinstance(params, (list, tuple)) and len(params) > 0:
        txid = params[0]
    elif isinstance(params, str):
        txid = params
    else:
        raise ValueError("txid parameter required")
    
    if not txid:
        raise ValueError("txid parameter required")
    
    txid_hex = _normalize_hash(txid)
    txid_bytes = _hash_to_bytes(txid_hex)
    
    result: Dict[str, Any] = {
        "txid": txid_hex,
        "status": "unknown",
        "lifecycle": {},
    }
    
    # Check mempool service
    try:
        from rpc.mempool_service import get_mempool_service_singleton
        
        mempool_service = get_mempool_service_singleton()
        if mempool_service is not None:
            # Check if tx is in mempool
            has_tx = mempool_service.has_hash(txid_hex)
            result["lifecycle"]["mempool_status"] = "in_pool" if has_tx else "not_found"
            
            if has_tx:
                result["status"] = "pending"
                # Try to get tx details
                try:
                    raw = mempool_service.get_raw(txid_hex)
                    if raw:
                        result["lifecycle"]["tx_size_bytes"] = len(raw)
                except Exception:
                    pass
            
            # Check rejection history
            rejection = mempool_service.get_rejection(txid_hex)
            if rejection:
                result["lifecycle"]["rejection"] = {
                    "reason": rejection.get("reason"),
                    "details": rejection.get("details"),
                    "timestamp": rejection.get("ts"),
                }
    except Exception as e:
        log.debug(f"Error checking mempool service: {e}", exc_info=True)
    
    # Check P2P tx relay service
    try:
        ctx = deps.get_ctx()
        p2p_service = getattr(ctx, "p2p", None) if ctx else None
        
        if p2p_service is not None:
            txrelay = getattr(p2p_service, "_txrelay", None)
            if txrelay is not None:
                # Get tx relay state
                tx_state = txrelay._tx_store.get(txid_bytes)
                if tx_state:
                    result["lifecycle"]["p2p"] = {
                        "arrival_time": tx_state.arrival_time,
                        "source": tx_state.source,
                        "validation_status": tx_state.validation_status,
                        "mempool_status": tx_state.mempool_status,
                        "last_peer": tx_state.last_peer,
                    }
                
                # Get request state
                request_state = txrelay._request_mgr.get_state(txid_bytes)
                if request_state:
                    result["lifecycle"]["request_tracking"] = {
                        "state": request_state.state,
                        "attempts": request_state.attempts,
                        "first_seen_at": request_state.first_seen_at,
                        "last_updated_at": request_state.last_updated_at,
                        "last_peer": request_state.last_peer,
                        "last_reason": request_state.last_reason,
                    }
    except Exception as e:
        log.debug(f"Error checking P2P relay service: {e}", exc_info=True)
    
    # Check if tx is mined (in chain)
    try:
        ctx = deps.get_ctx()
        if ctx:
            tx_index = getattr(ctx, "tx_index", None)
            if tx_index and hasattr(tx_index, "get_tx_location"):
                try:
                    location = tx_index.get_tx_location(txid_bytes)
                    if location:
                        result["status"] = "mined"
                        result["lifecycle"]["mined_in_block"] = {
                            "height": location.get("height"),
                            "hash": _normalize_hash(location.get("block_hash")),
                            "index": location.get("tx_index"),
                        }
                        
                        # Get confirmations
                        try:
                            head = ctx.get_head() if hasattr(ctx, "get_head") else None
                            if head and isinstance(head, dict):
                                head_height = int(head.get("height", 0))
                                block_height = int(location.get("height", 0))
                                result["lifecycle"]["confirmations"] = max(
                                    0, head_height - block_height + 1
                                )
                        except Exception:
                            pass
                except Exception:
                    pass
            
            # Fallback: check block DB
            if result["status"] == "unknown":
                block_db = getattr(ctx, "block_db", None)
                if block_db and hasattr(block_db, "get_tx"):
                    try:
                        tx_record = block_db.get_tx(txid_bytes)
                        if tx_record:
                            result["status"] = "mined"
                            result["lifecycle"]["found_in_chain"] = True
                    except Exception:
                        pass
    except Exception as e:
        log.debug(f"Error checking chain: {e}", exc_info=True)
    
    return result


@method(
    "debug.txStatus",
    desc="Get simple status of a transaction (pending, mined, unknown)",
    aliases=("debug.tx_status", "tx.status"),
)
def debug_tx_status(params: Any) -> Dict[str, Any]:
    """
    Get the simple status of a transaction.
    
    Returns:
        {
            "txid": "0x...",
            "status": "pending" | "mined" | "unknown",
            "in_mempool": bool,
            "in_chain": bool,
            "block_height": number | null,
            "confirmations": number | null
        }
    """
    # Parse params
    if isinstance(params, dict):
        txid = params.get("txid") or params.get("hash") or params.get("tx_hash")
    elif isinstance(params, (list, tuple)) and len(params) > 0:
        txid = params[0]
    elif isinstance(params, str):
        txid = params
    else:
        raise ValueError("txid parameter required")
    
    if not txid:
        raise ValueError("txid parameter required")
    
    txid_hex = _normalize_hash(txid)
    txid_bytes = _hash_to_bytes(txid_hex)
    
    result: Dict[str, Any] = {
        "txid": txid_hex,
        "status": "unknown",
        "in_mempool": False,
        "in_chain": False,
        "block_height": None,
        "confirmations": None,
    }
    
    # Check mempool
    try:
        from rpc.mempool_service import get_mempool_service_singleton
        
        mempool_service = get_mempool_service_singleton()
        if mempool_service is not None:
            result["in_mempool"] = mempool_service.has_hash(txid_hex)
            if result["in_mempool"]:
                result["status"] = "pending"
    except Exception as e:
        log.debug(f"Error checking mempool: {e}", exc_info=True)
    
    # Check chain
    try:
        ctx = deps.get_ctx()
        if ctx:
            tx_index = getattr(ctx, "tx_index", None)
            if tx_index and hasattr(tx_index, "exists"):
                try:
                    result["in_chain"] = tx_index.exists(txid_bytes)
                    if result["in_chain"]:
                        result["status"] = "mined"
                        
                        # Try to get block height
                        if hasattr(tx_index, "get_tx_location"):
                            try:
                                location = tx_index.get_tx_location(txid_bytes)
                                if location:
                                    result["block_height"] = location.get("height")
                                    
                                    # Get confirmations
                                    try:
                                        head = ctx.get_head() if hasattr(ctx, "get_head") else None
                                        if head and isinstance(head, dict):
                                            head_height = int(head.get("height", 0))
                                            block_height = int(location.get("height", 0))
                                            result["confirmations"] = max(
                                                0, head_height - block_height + 1
                                            )
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                except Exception:
                    pass
    except Exception as e:
        log.debug(f"Error checking chain: {e}", exc_info=True)
    
    return result


@method(
    "debug.mempoolTxTrace",
    desc="Get detailed mempool event history for a transaction",
    aliases=("debug.mempool_tx_trace",),
)
def debug_mempool_tx_trace(params: Any) -> Dict[str, Any]:
    """
    Get detailed mempool admission/eviction history for a transaction.
    
    Returns:
        {
            "txid": "0x...",
            "in_mempool": bool,
            "admission_history": [
                {"at": timestamp, "origin": "local|peer-id", "result": "accepted|rejected", "reason": "..."}
            ],
            "eviction_history": [
                {"at": timestamp, "reason": "..."}
            ]
        }
    """
    # Parse params
    if isinstance(params, dict):
        txid = params.get("txid") or params.get("hash") or params.get("tx_hash")
    elif isinstance(params, (list, tuple)) and len(params) > 0:
        txid = params[0]
    elif isinstance(params, str):
        txid = params
    else:
        raise ValueError("txid parameter required")
    
    if not txid:
        raise ValueError("txid parameter required")
    
    txid_hex = _normalize_hash(txid)
    
    result: Dict[str, Any] = {
        "txid": txid_hex,
        "in_mempool": False,
        "rejection": None,
    }
    
    try:
        from rpc.mempool_service import get_mempool_service_singleton
        
        mempool_service = get_mempool_service_singleton()
        if mempool_service is not None:
            result["in_mempool"] = mempool_service.has_hash(txid_hex)
            
            # Get rejection info if available
            rejection = mempool_service.get_rejection(txid_hex)
            if rejection:
                result["rejection"] = {
                    "reason": rejection.get("reason"),
                    "details": rejection.get("details"),
                    "timestamp": rejection.get("ts"),
                    "age_seconds": time.time() - rejection.get("ts", 0),
                }
    except Exception as e:
        log.debug(f"Error checking mempool service: {e}", exc_info=True)
    
    return result


__all__ = [
    "debug_trace_tx",
    "debug_tx_status",
    "debug_mempool_tx_trace",
]
