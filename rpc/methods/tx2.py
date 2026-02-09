"""
rpc.methods.tx2 - New Transaction RPC Methods (Mempool2)
========================================================

Production-ready RPC methods using the new mempool2 system.

These methods replace the old tx.py handlers which had TypeError issues.
All methods use coretx canonical encoding and mempool2 admission engine.

Methods:
- tx2.sendRawTransaction: Submit raw CBOR transaction
- tx2.getTransaction: Query transaction by hash
- tx2.getTransactionStatus: Get tx status (pending/confirmed/unknown)
- tx2.getMempoolStats: Get mempool statistics
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from coretx import TxEnvelope, TxId
from coretx.canonical import compute_txid, decode_tx_envelope
from coretx.errors import RejectReason

from rpc import errors as rpc_errors
from rpc.methods import method
from rpc.mempool2_service import get_mempool2_service
from mempool2 import TxSource

__all__ = [
    "send_raw_transaction_v2",
    "get_transaction_v2",
    "get_transaction_status_v2",
    "get_mempool_stats_v2",
]

log = logging.getLogger(__name__)
_DEBUG = os.environ.get("ANIMICA_DEBUG_TX") == "1"


def _hex_to_bytes(hex_str: str) -> bytes:
    """
    Convert hex string to bytes.
    
    Handles both 0x-prefixed and raw hex.
    
    Args:
        hex_str: Hex string to decode
        
    Returns:
        Decoded bytes
        
    Raises:
        ValueError: If hex string is invalid
    """
    if not isinstance(hex_str, str):
        raise ValueError(f"Expected string, got {type(hex_str).__name__}")
    
    # Strip 0x prefix if present
    if hex_str.startswith("0x") or hex_str.startswith("0X"):
        hex_str = hex_str[2:]
    
    try:
        return bytes.fromhex(hex_str)
    except ValueError as e:
        raise ValueError(f"Invalid hex string: {e}") from e


def _bytes_to_hex(data: bytes) -> str:
    """Convert bytes to 0x-prefixed hex string"""
    return "0x" + data.hex()


def _txid_from_hex(hex_str: str) -> TxId:
    """
    Parse TxId from hex string.
    
    Args:
        hex_str: Hex-encoded transaction ID (32 bytes)
        
    Returns:
        TxId instance
        
    Raises:
        ValueError: If not valid 32-byte hex
    """
    data = _hex_to_bytes(hex_str)
    if len(data) != 32:
        raise ValueError(f"TxId must be 32 bytes, got {len(data)}")
    return TxId(bytes32=data)


def _envelope_to_dict(envelope: TxEnvelope) -> dict[str, Any]:
    """
    Convert TxEnvelope to JSON-serializable dict.
    
    Returns compact representation with all fields.
    """
    body = envelope.body
    auth = envelope.auth
    
    return {
        "txid": _bytes_to_hex(envelope.txid.bytes32),
        "body": {
            "version": body.version,
            "chain_id": body.chain_id,
            "nonce": body.nonce,
            "from_addr": _bytes_to_hex(body.from_addr),
            "to_addr": _bytes_to_hex(body.to_addr),
            "value": str(body.value),
            "fee": str(body.fee),
            "gas_limit": body.gas_limit,
            "data": _bytes_to_hex(body.data),
            "memo": body.memo,
            "timestamp": body.timestamp,
            "kind": int(body.kind),
        },
        "auth": {
            "scheme_id": auth.scheme_id,
            "pubkey_bytes": _bytes_to_hex(auth.pubkey_bytes),
            "signature_bytes": _bytes_to_hex(auth.signature_bytes),
            "prehash_id": auth.prehash_id,
        },
    }


@method("tx2.sendRawTransaction", desc="Submit raw CBOR transaction to mempool2")
async def send_raw_transaction_v2(raw_tx: str) -> dict[str, Any]:
    """
    Submit a raw transaction to mempool2.
    
    This is the primary entry point for transaction submission.
    Uses coretx canonical encoding and mempool2 admission engine.
    
    Args:
        raw_tx: Hex-encoded CBOR transaction envelope
    
    Returns:
        {
            "txid": "0x...",
            "admitted": true
        }
        
    Raises:
        InvalidParams: If raw_tx is not valid hex or CBOR
        InvalidTx: If transaction fails admission (with TxReject payload)
    """
    try:
        # Step 1: Decode hex
        try:
            tx_bytes = _hex_to_bytes(raw_tx)
        except ValueError as e:
            if _DEBUG:
                log.debug(f"Hex decode failed: {e}")
            raise rpc_errors.InvalidParams(f"Invalid hex encoding: {e}")
        
        # Step 2: Decode CBOR envelope
        try:
            envelope = decode_tx_envelope(tx_bytes)
        except (ValueError, TypeError) as e:
            if _DEBUG:
                log.debug(f"CBOR decode failed: {e}")
            raise rpc_errors.InvalidParams(f"Invalid CBOR encoding: {e}")
        
        if _DEBUG:
            log.debug(
                f"Decoded tx {envelope.txid.hex()[:16]}... "
                f"chain_id={envelope.body.chain_id}, "
                f"nonce={envelope.body.nonce}"
            )
        
        # Step 3: Get mempool2 service
        try:
            mempool = get_mempool2_service()
        except Exception as e:
            log.error(f"Failed to get mempool2 service: {e}")
            raise rpc_errors.ServerError(f"Mempool service unavailable: {e}")
        
        # Step 4: Admit transaction
        success, rejection = mempool.admit_tx(
            envelope=envelope,
            source=TxSource.RPC,
            peer_id=None,
        )
        
        # Step 5: Handle result
        if success:
            if _DEBUG:
                log.info(f"Admitted tx {envelope.txid.hex()[:16]}... via RPC")
            
            return {
                "txid": _bytes_to_hex(envelope.txid.bytes32),
                "admitted": True,
            }
        else:
            # Transaction rejected - raise JSON-RPC error with rejection payload
            if not rejection:
                # Should never happen, but defensive
                raise rpc_errors.InvalidTx("Transaction rejected (no details)")
            
            if _DEBUG:
                log.debug(
                    f"Rejected tx {envelope.txid.hex()[:16]}...: "
                    f"{rejection.reason.value} - {rejection.message}"
                )
            
            # Map rejection reason to RPC error code
            code_map = {
                RejectReason.invalid_signature: rpc_errors.AnimicaCode.BAD_SIGNATURE,
                RejectReason.invalid_pubkey: rpc_errors.AnimicaCode.BAD_SIGNATURE,
                RejectReason.scheme_unsupported: rpc_errors.AnimicaCode.BAD_SIGNATURE,
                RejectReason.chain_id_mismatch: rpc_errors.AnimicaCode.CHAIN_ID_MISMATCH,
                RejectReason.insufficient_funds: rpc_errors.AnimicaCode.INSUFFICIENT_FUNDS,
                RejectReason.nonce_too_low: rpc_errors.AnimicaCode.NONCE_TOO_LOW,
                RejectReason.nonce_too_high: rpc_errors.AnimicaCode.NONCE_TOO_HIGH,
                RejectReason.nonce_gap: rpc_errors.AnimicaCode.NONCE_TOO_LOW,
                RejectReason.fee_too_low: rpc_errors.AnimicaCode.FEE_TOO_LOW,
                RejectReason.tx_oversize: rpc_errors.AnimicaCode.TX_TOO_LARGE,
                RejectReason.tx_already_known: rpc_errors.AnimicaCode.DUPLICATE_TX,
            }
            
            rpc_code = code_map.get(rejection.reason, rpc_errors.AnimicaCode.INVALID_TX)
            
            # Include TxReject payload in error data
            error_data = rejection.to_dict()
            error_data["txid"] = _bytes_to_hex(envelope.txid.bytes32)
            
            raise rpc_errors.RpcError(
                code=int(rpc_code),
                message=rejection.message,
                data=error_data,
            )
    
    except rpc_errors.RpcError:
        # Re-raise RPC errors as-is
        raise
    except Exception as e:
        # Catch any unexpected errors
        log.exception(f"Unexpected error in tx2.sendRawTransaction: {e}")
        raise rpc_errors.InternalError(f"Internal error: {e}")


@method("tx2.getTransaction", desc="Get transaction by hash from mempool2 or blocks")
async def get_transaction_v2(tx_hash: str) -> Optional[dict[str, Any]]:
    """
    Query transaction by hash from mempool2 or blockchain.
    
    Checks mempool first, then falls back to block DB.
    
    Args:
        tx_hash: Hex-encoded transaction ID (32 bytes)
    
    Returns:
        Transaction envelope + status if found, None otherwise:
        {
            "txid": "0x...",
            "status": "pending" | "confirmed",
            "body": {...},
            "auth": {...},
            "block_height": <height> (only if confirmed),
            "block_hash": "0x..." (only if confirmed)
        }
    """
    try:
        # Parse txid
        try:
            txid = _txid_from_hex(tx_hash)
        except ValueError as e:
            raise rpc_errors.InvalidParams(f"Invalid tx_hash: {e}")
        
        # Check mempool first
        mempool = get_mempool2_service()
        entry = mempool.get_tx(txid)
        
        if entry:
            # Found in mempool
            result = _envelope_to_dict(entry.envelope)
            result["status"] = "pending"
            result["arrival_time"] = entry.arrival_time
            result["fee_rate"] = entry.fee_rate
            result["source"] = entry.source.value
            return result
        
        # TODO: Check block DB for confirmed transactions
        # This requires integration with core.db.block_db.BlockDB
        # For now, return None if not in mempool
        
        return None
    
    except rpc_errors.RpcError:
        raise
    except Exception as e:
        log.exception(f"Unexpected error in tx2.getTransaction: {e}")
        raise rpc_errors.InternalError(f"Internal error: {e}")


@method("tx2.getTransactionStatus", desc="Get transaction status (pending/confirmed/unknown)")
async def get_transaction_status_v2(tx_hash: str) -> dict[str, Any]:
    """
    Get transaction status.
    
    Checks mempool and blocks to determine status.
    
    Args:
        tx_hash: Hex-encoded transaction ID (32 bytes)
    
    Returns:
        {
            "txid": "0x...",
            "status": "pending" | "confirmed" | "unknown",
            "in_mempool": <bool>,
            "block_height": <height> (only if confirmed),
            "block_hash": "0x..." (only if confirmed)
        }
    """
    try:
        # Parse txid
        try:
            txid = _txid_from_hex(tx_hash)
        except ValueError as e:
            raise rpc_errors.InvalidParams(f"Invalid tx_hash: {e}")
        
        # Check mempool
        mempool = get_mempool2_service()
        in_mempool = mempool.has_tx(txid)
        
        if in_mempool:
            return {
                "txid": tx_hash,
                "status": "pending",
                "in_mempool": True,
            }
        
        # TODO: Check block DB for confirmed transactions
        # For now, return unknown if not in mempool
        
        return {
            "txid": tx_hash,
            "status": "unknown",
            "in_mempool": False,
        }
    
    except rpc_errors.RpcError:
        raise
    except Exception as e:
        log.exception(f"Unexpected error in tx2.getTransactionStatus: {e}")
        raise rpc_errors.InternalError(f"Internal error: {e}")


@method("tx2.getMempoolStats", desc="Get mempool2 statistics")
async def get_mempool_stats_v2() -> dict[str, Any]:
    """
    Get mempool2 statistics.
    
    Returns comprehensive stats about the mempool state.
    
    Returns:
        {
            "tx_count": <int>,
            "total_bytes": <int>,
            "unique_senders": <int>,
            "fee_stats": {
                "min": <int>,
                "max": <int>,
                "median": <int>,
                "mean": <int>
            }
        }
    """
    try:
        mempool = get_mempool2_service()
        stats = mempool.get_stats()
        return stats.to_dict()
    
    except Exception as e:
        log.exception(f"Unexpected error in tx2.getMempoolStats: {e}")
        raise rpc_errors.InternalError(f"Internal error: {e}")
