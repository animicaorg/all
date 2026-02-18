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
                # Get tx relay state (note: accessing internal state for debug purposes)
                try:
                    tx_store = getattr(txrelay, "_tx_store", None)
                    if tx_store:
                        tx_state = tx_store.get(txid_bytes)
                        if tx_state:
                            result["lifecycle"]["p2p"] = {
                                "arrival_time": tx_state.arrival_time,
                                "source": tx_state.source,
                                "validation_status": tx_state.validation_status,
                                "mempool_status": tx_state.mempool_status,
                                "last_peer": tx_state.last_peer,
                            }
                except Exception:
                    pass
                
                # Get request state (note: accessing internal state for debug purposes)
                try:
                    request_mgr = getattr(txrelay, "_request_mgr", None)
                    if request_mgr:
                        request_state = request_mgr.get_state(txid_bytes)
                        if request_state:
                            result["lifecycle"]["request_tracking"] = {
                                "state": request_state.state,
                                "attempts": request_state.attempts,
                                "first_seen_at": request_state.first_seen_at,
                                "last_updated_at": request_state.last_updated_at,
                                "last_peer": request_state.last_peer,
                                "last_reason": request_state.last_reason,
                            }
                except Exception:
                    pass
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


@method(
    "tx.explainReject",
    desc="Explain why a transaction would be rejected by the mempool",
    aliases=("debug.explainReject", "tx.explain_reject"),
)
def tx_explain_reject(params: Any) -> Dict[str, Any]:
    """
    Dry-run validation for a transaction to explain why it would be rejected.
    
    This is useful for debugging transaction submission issues without actually
    submitting the transaction to the mempool.
    
    Args:
        params: Either raw transaction bytes (hex string) or dict with "raw" key
    
    Returns:
        {
            "valid": bool,
            "reason": str | null,
            "details": {
                "chain_id": int | null,
                "sender": str | null,
                "nonce": int | null,
                "gas_limit": int | null,
                "checks": {
                    "chain_id_match": bool,
                    "sender_valid": bool,
                    "nonce_valid": bool,
                    "balance_sufficient": bool,
                    "gas_price_sufficient": bool,
                    "signature_valid": bool
                }
            }
        }
    """
    # Parse params
    if isinstance(params, dict):
        raw_tx = params.get("raw") or params.get("rawTx") or params.get("raw_tx")
    elif isinstance(params, (list, tuple)) and len(params) > 0:
        raw_tx = params[0]
    elif isinstance(params, str):
        raw_tx = params
    else:
        raise ValueError("raw transaction parameter required")
    
    if not raw_tx:
        raise ValueError("raw transaction parameter required")
    
    # Normalize raw_tx to bytes
    if isinstance(raw_tx, str):
        if raw_tx.startswith("0x"):
            raw_tx = raw_tx[2:]
        try:
            raw_bytes = bytes.fromhex(raw_tx)
        except ValueError as e:
            return {
                "valid": False,
                "reason": "invalid_hex_encoding",
                "details": {"error": str(e)},
            }
    elif isinstance(raw_tx, (bytes, bytearray)):
        raw_bytes = bytes(raw_tx)
    else:
        return {
            "valid": False,
            "reason": "invalid_parameter_type",
            "details": {"type": type(raw_tx).__name__},
        }
    
    result: Dict[str, Any] = {
        "valid": True,
        "reason": None,
        "details": {
            "chain_id": None,
            "sender": None,
            "nonce": None,
            "gas_limit": None,
            "checks": {},
        },
    }
    
    # Try to decode transaction
    try:
        from core.encoding.cbor import loads as cbor_loads
        from core.utils.tx import normalize_tx_envelope
        from mempool.tx_hash import tx_hash_hex as compute_tx_hash
        
        try:
            tx_obj = cbor_loads(raw_bytes)
        except Exception as e:
            result["valid"] = False
            result["reason"] = "cbor_decode_error"
            result["details"]["error"] = str(e)
            return result
        
        # Compute txid
        try:
            txid = compute_tx_hash(raw_bytes)
            result["details"]["txid"] = txid
        except Exception as e:
            result["details"]["txid_error"] = str(e)
        
        # Normalize envelope
        try:
            normalized = normalize_tx_envelope(tx_obj)
        except Exception as e:
            result["valid"] = False
            result["reason"] = "normalization_error"
            result["details"]["error"] = str(e)
            return result
        
        # Extract transaction fields
        tx_body = normalized.get("tx", {})
        
        # Chain ID check
        chain_id = tx_body.get("chainId") or tx_body.get("chain_id")
        result["details"]["chain_id"] = chain_id
        
        try:
            ctx = deps.get_ctx()
            if ctx:
                expected_chain_id = getattr(ctx, "chain_id", None)
                if expected_chain_id is not None:
                    result["details"]["checks"]["chain_id_match"] = (
                        int(chain_id) == int(expected_chain_id)
                    )
                    if not result["details"]["checks"]["chain_id_match"]:
                        result["valid"] = False
                        result["reason"] = "chain_id_mismatch"
                        result["details"]["expected_chain_id"] = expected_chain_id
        except Exception:
            pass
        
        # Sender extraction
        sender = tx_body.get("from") or tx_body.get("sender")
        result["details"]["sender"] = sender
        result["details"]["checks"]["sender_valid"] = sender is not None
        
        if not sender:
            result["valid"] = False
            result["reason"] = "missing_sender"
            return result
        
        # Nonce check (v1 transactions)
        nonce = tx_body.get("nonce")
        result["details"]["nonce"] = nonce
        
        # Gas limit check
        gas_limit = tx_body.get("gasLimit") or tx_body.get("gas_limit") or tx_body.get("gas")
        result["details"]["gas_limit"] = gas_limit
        result["details"]["checks"]["gas_limit_valid"] = gas_limit is not None and int(gas_limit or 0) > 0
        
        if not gas_limit or int(gas_limit) <= 0:
            result["valid"] = False
            result["reason"] = "invalid_gas_limit"
            return result
        
        # Gas price check
        max_fee = tx_body.get("maxFee") or tx_body.get("max_fee") or tx_body.get("gasPrice")
        result["details"]["max_fee"] = max_fee
        
        try:
            from rpc.mempool_service import get_mempool_service_singleton
            
            mempool_service = get_mempool_service_singleton()
            if mempool_service is not None:
                min_gas_price = mempool_service.min_gas_price_wei
                result["details"]["min_gas_price_wei"] = min_gas_price
                
                if max_fee and min_gas_price:
                    result["details"]["checks"]["gas_price_sufficient"] = (
                        int(max_fee) >= min_gas_price
                    )
                    if not result["details"]["checks"]["gas_price_sufficient"]:
                        result["valid"] = False
                        result["reason"] = "gas_price_too_low"
        except Exception:
            pass
        
        # Balance check (if state DB available)
        try:
            from rpc.mempool_service import get_mempool_service_singleton
            
            mempool_service = get_mempool_service_singleton()
            if mempool_service is not None and mempool_service.state_db is not None:
                sender_bytes = bytes.fromhex(sender[2:] if sender.startswith("0x") else sender)
                
                try:
                    balance = mempool_service.state_db.get_balance(sender_bytes)
                    result["details"]["balance"] = int(balance)
                    
                    # Estimate spend
                    value = tx_body.get("value", 0)
                    max_gas_cost = int(gas_limit) * int(max_fee or 0)
                    total_spend = int(value) + max_gas_cost
                    
                    result["details"]["estimated_spend"] = total_spend
                    result["details"]["checks"]["balance_sufficient"] = balance >= total_spend
                    
                    if balance < total_spend:
                        result["valid"] = False
                        result["reason"] = "insufficient_balance"
                except Exception as e:
                    result["details"]["balance_check_error"] = str(e)
        except Exception:
            pass
        
        # Nonce check (if state DB available)
        if nonce is not None:
            try:
                from rpc.mempool_service import get_mempool_service_singleton
                
                mempool_service = get_mempool_service_singleton()
                if mempool_service is not None:
                    sender_bytes = bytes.fromhex(sender[2:] if sender.startswith("0x") else sender)
                    
                    # Use private method safely for debug purposes
                    try:
                        confirmed_nonce = getattr(mempool_service, "_confirmed_nonce", lambda x: None)(sender_bytes)
                        if confirmed_nonce is not None:
                            result["details"]["confirmed_nonce"] = confirmed_nonce
                            
                            expected_nonce = mempool_service.get_next_nonce(sender_bytes, confirmed_nonce)
                            result["details"]["expected_nonce"] = expected_nonce
                            result["details"]["checks"]["nonce_valid"] = int(nonce) == expected_nonce
                            
                            if int(nonce) < expected_nonce:
                                result["valid"] = False
                                result["reason"] = "nonce_too_low"
                            elif int(nonce) > expected_nonce:
                                result["valid"] = False
                                result["reason"] = "nonce_gap"
                    except Exception:
                        pass
            except Exception as e:
                result["details"]["nonce_check_error"] = str(e)
        
    except Exception as e:
        result["valid"] = False
        result["reason"] = "validation_error"
        result["details"]["error"] = str(e)
        log.debug(f"Error validating transaction: {e}", exc_info=True)
    
    return result


__all__ = [
    "debug_trace_tx",
    "debug_tx_status",
    "debug_mempool_tx_trace",
    "tx_explain_reject",
    "debug_verify_useful_work_proof",
]


@method(
    "debug.verifyUsefulWorkProof",
    desc="Verify a useful work proof without submitting a share",
    aliases=("debug.verify_useful_work_proof", "debug.verifyUWP"),
)
def debug_verify_useful_work_proof(params: Any) -> Dict[str, Any]:
    """
    Verify a useful work proof for testing/debugging purposes.
    
    Args:
        params: {
            "proofHex": "hex-encoded proof",
            "context": {
                "jobId": "...",
                "nonce": "0x...",
                "mixSeed": "0x...",
                "height": 12345,
                "minerAddress": "anim1...",
                "timestamp": 1234567890
            }
        }
    
    Returns:
        {
            "status": "ACCEPTED" | "REJECTED" | ...,
            "statusCode": 0..5,
            "accepted": bool,
            "reason": "...",
            "bonusCredits": number,
            "metadata": {...}
        }
    """
    try:
        from core.usefulwork import (
            decode_proof_from_hex,
            verify_proof,
            ShareContext,
            UWPDecodeError,
        )
    except ImportError as e:
        return {
            "error": "UWP module not available",
            "details": str(e),
        }
    
    # Parse params
    if isinstance(params, dict):
        proof_hex = params.get("proofHex") or params.get("proof_hex") or params.get("proof")
        context_data = params.get("context", {})
    elif isinstance(params, (list, tuple)) and len(params) > 0:
        proof_hex = params[0]
        context_data = params[1] if len(params) > 1 else {}
    elif isinstance(params, str):
        proof_hex = params
        context_data = {}
    else:
        return {
            "error": "Invalid parameters",
            "details": "Expected {proofHex, context} or proof hex string",
        }
    
    if not proof_hex:
        return {
            "error": "Missing proof",
            "details": "proofHex parameter is required",
        }
    
    # Decode proof
    try:
        proof = decode_proof_from_hex(proof_hex)
    except UWPDecodeError as e:
        return {
            "error": "Decode error",
            "details": str(e),
            "status": "REJECTED",
            "statusCode": 1,
            "accepted": False,
        }
    except Exception as e:
        log.error(f"Unexpected decode error: {e}", exc_info=True)
        return {
            "error": "Decode error",
            "details": str(e),
            "status": "VERIFIER_ERROR",
            "statusCode": 5,
            "accepted": False,
        }
    
    # Build context
    job_id = str(context_data.get("jobId") or context_data.get("job_id") or "test-job")
    
    nonce_val = context_data.get("nonce") or context_data.get("n") or "0x0000000000000000"
    if isinstance(nonce_val, str):
        if nonce_val.startswith("0x"):
            nonce_bytes = bytes.fromhex(nonce_val[2:])
        else:
            nonce_bytes = bytes.fromhex(nonce_val)
    else:
        nonce_bytes = nonce_val
    
    mix_seed_val = context_data.get("mixSeed") or context_data.get("mix_seed") or b"\x00" * 32
    if isinstance(mix_seed_val, str):
        if mix_seed_val.startswith("0x"):
            mix_seed_bytes = bytes.fromhex(mix_seed_val[2:])
        else:
            mix_seed_bytes = bytes.fromhex(mix_seed_val)
    else:
        mix_seed_bytes = mix_seed_val
    
    context = ShareContext(
        job_id=job_id,
        nonce=nonce_bytes,
        mix_seed=mix_seed_bytes,
        height=int(context_data.get("height") or 0),
        miner_address=str(context_data.get("minerAddress") or context_data.get("miner_address") or ""),
        timestamp=int(context_data.get("timestamp") or time.time()),
    )
    
    # Verify proof
    try:
        result = verify_proof(proof=proof, context=context)
        return result.to_dict()
    except Exception as e:
        log.error(f"Verification error: {e}", exc_info=True)
        return {
            "error": "Verification error",
            "details": str(e),
            "status": "VERIFIER_ERROR",
            "statusCode": 5,
            "accepted": False,
        }
