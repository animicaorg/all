"""
rpc.methods.aicf — AICF (AI Compute Fund) RPC methods
======================================================

Provides JSON-RPC methods for:
- aicf.getParams - Get AICF configuration parameters
- aicf.getStatus - Get current AICF pool status
- aicf.getClaimable - Get claimable rewards for an address
- aicf.claim - Process a claim transaction (or return unsigned tx)
- aicf.topUp - Governance-only method to add funds to AICF pool

All methods are deterministic and consensus-safe.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from rpc.errors import InvalidParams, MethodNotFound, InternalError
from rpc.methods import method

log = logging.getLogger("rpc.methods.aicf")


# --------------------------------------------------------------------------------------
# Helper functions
# --------------------------------------------------------------------------------------


def _get_aicf_params(ctx: Any) -> Dict[str, Any]:
    """Get AICF parameters from chain params."""
    params = getattr(ctx, "params", None)
    if params is None:
        return {}
    
    aicf_params = params.get("aicf", {})
    return {
        "epoch_length_blocks": aicf_params.get("epoch_length_blocks", 100),
        "block_reward_slice_bps": aicf_params.get("block_reward_slice_bps", 500),
        "fee_slice_bps": aicf_params.get("fee_slice_bps", 2000),
        "ena_call_fee_base_nano": aicf_params.get("ena_call_fee_base_nano", 10000),
        "ena_call_fee_aicf_bps": aicf_params.get("ena_call_fee_aicf_bps", 8000),
        "epoch_payout_bps": aicf_params.get("epoch_payout_bps", 5000),
        "credits_per_block": aicf_params.get("credits_per_block", 1_000_000),
        "max_claim_epochs": aicf_params.get("max_claim_epochs", 100),
        "prune_after_epochs": aicf_params.get("prune_after_epochs", 10000),
    }


def _get_current_epoch(ctx: Any) -> int:
    """Get current epoch from block height."""
    try:
        from execution.state.aicf_state import compute_epoch, get_epoch_length
    except ImportError:
        return 0
    
    # Get current height
    state = getattr(ctx, "state", None)
    if state is None:
        return 0
    
    block_env = getattr(ctx, "block_env", None)
    height = 0
    if block_env is not None:
        height = int(getattr(block_env, "height", 0) or 0)
    
    epoch_length = get_epoch_length(state)
    return compute_epoch(height, epoch_length)


def _decode_address(addr_str: str) -> bytes:
    """Decode address from hex or bech32."""
    # Try hex first
    if addr_str.startswith("0x"):
        try:
            return bytes.fromhex(addr_str[2:])
        except ValueError:
            pass
    
    # Try bech32m
    try:
        from core.encoding.bech32m import bech32m_decode
        hrp, data = bech32m_decode(addr_str)
        if data and len(data) == 32:
            return bytes(data)
    except Exception:
        pass
    
    raise InvalidParams(f"Invalid address format: {addr_str}")


def _encode_address_hex(addr: bytes) -> str:
    """Encode address as 0x-prefixed hex."""
    return "0x" + addr.hex()


def _to_hex_quantity(value: int) -> str:
    """Convert integer to 0x-prefixed hex quantity."""
    return hex(value)


# --------------------------------------------------------------------------------------
# RPC methods
# --------------------------------------------------------------------------------------


@method("aicf.getParams", desc="Get AICF configuration parameters")
async def getParams(ctx: Any, params: List[Any]) -> Dict[str, Any]:
    """
    aicf.getParams - Get AICF configuration parameters.
    
    Params: []
    
    Returns: {
        epoch_length_blocks: int,
        block_reward_slice_bps: int,
        fee_slice_bps: int,
        ena_call_fee_base_nano: int,
        ena_call_fee_aicf_bps: int,
        epoch_payout_bps: int,
        credits_per_block: int,
        max_claim_epochs: int,
        prune_after_epochs: int
    }
    """
    return _get_aicf_params(ctx)


@method("aicf.getStatus", desc="Get current AICF pool status")
async def getStatus(ctx: Any, params: List[Any]) -> Dict[str, Any]:
    """
    aicf.getStatus - Get current AICF pool status.
    
    Params: []
    
    Returns: {
        pool_balance: HexQuantity,
        current_epoch: int,
        current_height: int,
        last_finalized_epoch: int
    }
    """
    try:
        from execution.state.aicf_state import (
            compute_epoch,
            get_epoch_length,
            get_pool_balance,
        )
    except ImportError:
        raise InternalError("AICF state module not available")
    
    state = getattr(ctx, "state", None)
    if state is None:
        raise InternalError("State not available")
    
    # Get current height and epoch
    block_env = getattr(ctx, "block_env", None)
    height = 0
    if block_env is not None:
        height = int(getattr(block_env, "height", 0) or 0)
    
    epoch_length = get_epoch_length(state)
    current_epoch = compute_epoch(height, epoch_length)
    
    # Pool balance
    pool_balance = get_pool_balance(state)
    
    # Last finalized epoch is at least 2 epochs behind current
    last_finalized = max(0, current_epoch - 2)
    
    return {
        "pool_balance": _to_hex_quantity(pool_balance),
        "current_epoch": current_epoch,
        "current_height": height,
        "last_finalized_epoch": last_finalized,
    }


@method("aicf.getClaimable", desc="Get claimable rewards for an address")
async def getClaimable(address: str, ctx: Any = None, up_to_epoch: Optional[int] = None, include_details: bool = False) -> Dict[str, Any]:
    """
    aicf.getClaimable - Get claimable rewards for an address.

    Accepts params by name (paramStructure=by-name) or positional.

    Params: address (string), up_to_epoch? (int), include_details? (bool)

    Returns: {
        claimable: HexQuantity,
        epochs: [int, ...],
        details?: [{
            epoch: int,
            credits: HexQuantity,
            total_credits: HexQuantity,
            share: HexQuantity
        }, ...]
    }
    """
    if not isinstance(address, str) or not address.strip():
        raise InvalidParams("Missing required parameter: address")

    addr_decoded = _decode_address(address.strip())

    try:
        from execution.state.aicf_state import compute_claimable, get_epoch_length
    except ImportError:
        raise InternalError("AICF state module not available")

    state = getattr(ctx, "state", None)
    if state is None:
        raise InternalError("State not available")

    # Get current epoch
    current_epoch = _get_current_epoch(ctx)

    # Get max epochs from params
    aicf_params = _get_aicf_params(ctx)
    max_epochs = aicf_params.get("max_claim_epochs", 100)

    # Compute claimable
    claimable = compute_claimable(state, addr_decoded, current_epoch, max_epochs)

    # Format response
    result: Dict[str, Any] = {
        "claimable": _to_hex_quantity(claimable.total_claimable),
        "epochs": claimable.epochs,
    }

    if include_details:
        details = []
        for epoch, credits_user, credits_total, share in claimable.details:
            details.append({
                "epoch": epoch,
                "credits": _to_hex_quantity(credits_user),
                "total_credits": _to_hex_quantity(credits_total),
                "share": _to_hex_quantity(share),
            })
        result["details"] = details

    return result


@method("aicf.buildClaimTx", desc="Build an unsigned claim transaction")
async def buildClaimTx(ctx: Any, params: List[Any]) -> Dict[str, Any]:
    """
    aicf.buildClaimTx - Build an unsigned claim transaction.
    
    Params: [
        from_address: HexString,
        to_address: HexString,
        amount: HexQuantity,
        options?: {
            nonce?: HexQuantity,
            gas_limit?: HexQuantity,
            gas_price?: HexQuantity,
        }
    ]
    
    Returns: Unsigned transaction object that can be signed and sent via tx.sendRawTransaction
    """
    if len(params) < 3:
        raise InvalidParams("buildClaimTx requires [from_address, to_address, amount]")
    
    from_addr_str = params[0]
    to_addr_str = params[1]
    amount_str = params[2]
    options = params[3] if len(params) > 3 else {}
    
    # Decode addresses
    from_addr = _decode_address(from_addr_str)
    to_addr = _decode_address(to_addr_str)
    
    # Parse amount
    if isinstance(amount_str, str) and amount_str.startswith("0x"):
        amount = int(amount_str, 16)
    else:
        amount = int(amount_str)
    
    # Get options
    nonce = options.get("nonce")
    gas_limit = options.get("gas_limit", hex(50000))  # Default gas for claim
    gas_price = options.get("gas_price", hex(1000000000))  # Default 1 gwei
    
    # Get current nonce if not provided
    if nonce is None:
        state = getattr(ctx, "state", None)
        if state is not None:
            try:
                from execution.state.balances import get_nonce
                nonce_int = get_nonce(state, from_addr)
                nonce = hex(nonce_int)
            except Exception:
                nonce = "0x0"
        else:
            nonce = "0x0"
    
    # Parse gas values
    if isinstance(gas_limit, str) and gas_limit.startswith("0x"):
        gas_limit_int = int(gas_limit, 16)
    else:
        gas_limit_int = int(gas_limit)
    
    if isinstance(gas_price, str) and gas_price.startswith("0x"):
        gas_price_int = int(gas_price, 16)
    else:
        gas_price_int = int(gas_price)
    
    fee = gas_limit_int * gas_price_int
    
    # Get chain_id
    params_obj = getattr(ctx, "params", None)
    chain_id = params_obj.get("chain_id", 1) if params_obj else 1
    
    # Build claim payload (CBOR-encoded)
    import cbor2
    claim_payload = {
        "to_address": to_addr,
        "amount": amount,
    }
    claim_data = cbor2.dumps(claim_payload)
    
    # Build unsigned transaction
    import time
    unsigned_tx = {
        "version": 1,
        "chain_id": chain_id,
        "nonce": int(nonce, 16) if isinstance(nonce, str) and nonce.startswith("0x") else int(nonce),
        "from_addr": from_addr,
        "to_addr": to_addr,  # In claim tx, to_addr is the recipient
        "value": 0,  # No value transfer, just claim
        "fee": fee,
        "gas_limit": gas_limit_int,
        "data": claim_data,
        "memo": "",
        "timestamp": int(time.time()),
        "kind": 4,  # AICF_CLAIM
    }
    
    # Encode to hex for return
    result = {
        "unsigned_tx": {
            "version": hex(unsigned_tx["version"]),
            "chain_id": hex(unsigned_tx["chain_id"]),
            "nonce": hex(unsigned_tx["nonce"]),
            "from_addr": "0x" + from_addr.hex(),
            "to_addr": "0x" + to_addr.hex(),
            "value": "0x0",
            "fee": hex(fee),
            "gas_limit": hex(gas_limit_int),
            "data": "0x" + claim_data.hex(),
            "memo": "",
            "timestamp": hex(unsigned_tx["timestamp"]),
            "kind": "0x4",  # AICF_CLAIM
        },
        "message": "Sign this transaction and send via tx.sendRawTransaction to claim your AICF credits"
    }
    
    return result


@method("aicf.claim", desc="Get claim information (read-only)")
async def claim(address: str, ctx: Any = None, up_to_epoch: Optional[int] = None) -> Dict[str, Any]:
    """
    aicf.claim - Process a claim transaction.

    NOTE: This is a read-only method that returns claimable info.
    Actual claiming requires sending a transaction through tx.sendRawTransaction.
    Use aicf.buildClaimTx to build an unsigned transaction.

    Params: address (string), up_to_epoch? (int)

    Returns: {
        claimable: HexQuantity,
        epochs: [int, ...],
        message: string
    }
    """
    # For now, this just returns claimable info
    claimable_info = await getClaimable(address=address, ctx=ctx, up_to_epoch=up_to_epoch)
    claimable_info["message"] = (
        "This is a read-only response. To claim, use aicf.buildClaimTx to build an unsigned "
        "transaction, sign it, and send via tx.sendRawTransaction."
    )
    return claimable_info


@method("aicf.topUp", desc="Governance-only method to add funds to AICF pool")
async def topUp(ctx: Any, params: List[Any]) -> Dict[str, Any]:
    """
    aicf.topUp - Governance-only method to add funds to AICF pool.
    
    Params: [amount: HexQuantity]
    
    Returns: {
        success: bool,
        message: string
    }
    
    NOTE: This requires governance/admin permissions.
    """
    if not params or len(params) < 1:
        raise InvalidParams("Missing required parameter: amount")
    
    # Parse amount
    amount_str = params[0]
    if isinstance(amount_str, str):
        if amount_str.startswith("0x"):
            amount = int(amount_str, 16)
        else:
            amount = int(amount_str)
    else:
        amount = int(amount_str)
    
    if amount <= 0:
        raise InvalidParams("Amount must be positive")
    
    # Phase 2: Governance top-up transaction
    # Implementation path:
    # 1. Verify caller has governance permission (from governance registry)
    # 2. Build AICF_GOVERNANCE_TOPUP transaction (TxKind.AICF_GOVERNANCE_TOPUP)
    # 3. Sign with governance key
    # 4. Submit transaction to mempool
    # 5. Wait for confirmation
    # 6. Return transaction hash
    # 
    # Example:
    # from execution.state.governance import verify_governance_permission
    # if not verify_governance_permission(ctx.state, from_address):
    #     raise RpcError(code=-32000, message="Not authorized")
    # from coretx.types import TxKind
    # tx = build_tx(kind=TxKind.AICF_GOVERNANCE_TOPUP, value=amount, ...)
    # tx_hash = submit_tx(tx)
    # return {"success": True, "tx_hash": tx_hash}
    
    return {
        "success": False,
        "message": "Top-up functionality not yet implemented (Phase 2 - governance integration pending).",
    }


def _normalize_status_params(raw_params: Any) -> Any:
    """Accept zero/empty/legacy wrapped params for status methods."""
    if raw_params is None:
        return None
    if isinstance(raw_params, dict) and "params" in raw_params:
        return raw_params.get("params")
    if isinstance(raw_params, list) and len(raw_params) == 1 and isinstance(raw_params[0], dict) and "params" in raw_params[0]:
        return raw_params[0].get("params")
    return raw_params


@method(
    "aicf.status",
    aliases=("aicf_status", "aicf.getStatus", "aicf_getStatus"),
    desc="Get AICF status in standard schema",
)
async def aicf_status_endpoint(ctx: Any, params: Any = None) -> Dict[str, Any]:
    """
    aicf.status - Get AICF pool status in the standard status schema.

    Returns: {enabled, ok, reason, message, details}
    """
    try:
        _ = _normalize_status_params(params)
        from execution.state.aicf_state import (
            compute_epoch,
            get_epoch_length,
            get_pool_balance,
        )
    except ImportError:
        return {
            "enabled": False,
            "ok": False,
            "reason": "dependency_missing",
            "message": "AICF state module not available on this node",
            "details": {},
        }

    state = getattr(ctx, "state", None)
    if state is None:
        return {
            "enabled": False,
            "ok": False,
            "reason": "dependency_missing",
            "message": "State not available",
            "details": {},
        }

    try:
        block_env = getattr(ctx, "block_env", None)
        height = 0
        if block_env is not None:
            height = int(getattr(block_env, "height", 0) or 0)

        epoch_length = get_epoch_length(state)
        current_epoch = compute_epoch(height, epoch_length)
        pool_balance = get_pool_balance(state)
        last_finalized = max(0, current_epoch - 2)

        return {
            "enabled": True,
            "ok": True,
            "reason": None,
            "message": None,
            "details": {
                "pool_address": None,
                "total_credits": _to_hex_quantity(pool_balance),
                "block_reward_bp": None,
                "fee_bp": None,
                "last_updated": height,
                # Backward-compatible detail fields for older consumers.
                "pool_balance": _to_hex_quantity(pool_balance),
                "current_epoch": current_epoch,
                "current_height": height,
                "last_finalized_epoch": last_finalized,
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "enabled": True,
            "ok": False,
            "reason": "internal",
            "message": str(exc),
            "details": {},
        }




# ---------------------------------------------------------------------------
# Integration methods: summary, creditsByAddress, recentEvents
# ---------------------------------------------------------------------------


def _open_aicf_state():
    """Open AICFProtocolState for the current chain. Returns None if unavailable."""
    try:
        from aicf.protocol.state import AICFProtocolState  # type: ignore
        import os

        base = os.getenv("ANIMICA_DATA_DIR") or os.path.expanduser("~/.animica")
        chain_id = os.getenv("ANIMICA_CHAIN_ID", "1")
        db_path = os.path.join(base, f"chain-{chain_id}", "aicf_credits.db")
        return AICFProtocolState(db_path)
    except Exception:
        return None


@method(
    "aicf.summary",
    aliases=("aicf_summary",),
    desc="Return a high-level AICF summary: totals, event count, recent activity",
)
async def aicf_summary(ctx: Any, params: Any = None) -> Dict[str, Any]:
    """
    aicf.summary → {total_minted, total_spent, balance, event_count, recent_events[5]}
    """
    state = _open_aicf_state()
    if state is None:
        return {
            "ok": False,
            "reason": "aicf_unavailable",
            "total_minted": "0",
            "total_spent": "0",
            "balance": "0",
            "event_count": 0,
            "recent_events": [],
        }
    try:
        totals = state.get_aicf_totals()
        recent = state.get_credit_ledger(limit=5)
        events_raw = [
            {
                "event_id": e.ledger_id,
                "event_type": e.event_type,
                "amount": e.amount,
                "block_height": e.block_height,
                "timestamp": e.timestamp if hasattr(e, "timestamp") else None,
                "metadata": e.metadata if hasattr(e, "metadata") else None,
            }
            for e in recent
        ]
        return {
            "ok": True,
            "total_minted": totals.minted_total,
            "total_spent": totals.spent_total,
            "balance": totals.balance_total,
            "event_count": len(events_raw),
            "recent_events": events_raw,
        }
    except Exception as exc:
        return {
            "ok": False,
            "reason": str(exc),
            "total_minted": "0",
            "total_spent": "0",
            "balance": "0",
            "event_count": 0,
            "recent_events": [],
        }


@method(
    "aicf.creditsByAddress",
    aliases=("aicf_creditsByAddress", "aicf.credits_by_address"),
    desc="Return AICF credits earned by a specific miner/verifier address",
)
async def aicf_credits_by_address(ctx: Any, params: Any = None) -> Dict[str, Any]:
    """
    aicf.creditsByAddress(address) → {address, balance, spent, minted, events[]}
    """
    if isinstance(params, (list, tuple)):
        if not params:
            raise InvalidParams("Missing required parameter: 'address'")
        address = str(params[0]).strip()
    elif isinstance(params, dict):
        address = str(params.get("address", "")).strip()
        if not address:
            raise InvalidParams("Missing required parameter: 'address'")
    elif isinstance(params, str):
        address = params.strip()
    else:
        raise InvalidParams("params must be a dict, list, or string address")

    if not address:
        raise InvalidParams("address must be non-empty")

    state = _open_aicf_state()
    if state is None:
        return {
            "ok": False,
            "reason": "aicf_unavailable",
            "address": address,
            "balance": "0",
            "events": [],
        }

    try:
        # Try get_miner_credits which returns (balance, spent, minted)
        try:
            credits = state.get_miner_credits(address)
            balance = credits.balance if hasattr(credits, "balance") else "0"
            spent = credits.spent if hasattr(credits, "spent") else "0"
            minted = credits.minted if hasattr(credits, "minted") else "0"
        except Exception:
            balance, spent, minted = "0", "0", "0"

        events = state.get_credit_ledger(miner_address=address, limit=50)
        events_raw = [
            {
                "event_id": e.ledger_id,
                "event_type": e.event_type,
                "amount": e.amount,
                "block_height": e.block_height,
            }
            for e in events
        ]
        return {
            "ok": True,
            "address": address,
            "balance": balance,
            "spent": spent,
            "minted": minted,
            "events": events_raw,
        }
    except Exception as exc:
        return {
            "ok": False,
            "reason": str(exc),
            "address": address,
            "balance": "0",
            "events": [],
        }


@method(
    "aicf.recentEvents",
    aliases=("aicf_recentEvents", "aicf.recent_events"),
    desc="Return recent AICF credit events (newest first)",
)
async def aicf_recent_events(ctx: Any, params: Any = None) -> Dict[str, Any]:
    """
    aicf.recentEvents([limit]) → {events: [...]}
    """
    limit = 20
    if isinstance(params, (list, tuple)) and params:
        try:
            limit = int(params[0])
        except (TypeError, ValueError):
            pass
    elif isinstance(params, dict):
        try:
            limit = int(params.get("limit", 20))
        except (TypeError, ValueError):
            pass
    elif isinstance(params, int):
        limit = params

    limit = max(1, min(limit, 200))

    state = _open_aicf_state()
    if state is None:
        return {"ok": False, "reason": "aicf_unavailable", "events": []}

    try:
        events = state.get_credit_ledger(limit=limit)
        events_raw = [
            {
                "event_id": e.ledger_id,
                "event_type": e.event_type,
                "amount": e.amount,
                "block_height": e.block_height,
                "timestamp": e.timestamp if hasattr(e, "timestamp") else None,
                "miner_address": e.miner_address if hasattr(e, "miner_address") else None,
                "metadata": e.metadata if hasattr(e, "metadata") else None,
            }
            for e in events
        ]
        return {"ok": True, "events": events_raw}
    except Exception as exc:
        return {"ok": False, "reason": str(exc), "events": []}


__all__ = [
    "getParams",
    "getStatus",
    "getClaimable",
    "claim",
    "topUp",
    "aicf_status_endpoint",
    "aicf_summary",
    "aicf_credits_by_address",
    "aicf_recent_events",
]
