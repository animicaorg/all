"""
rpc.methods.aicf — AICF Pool RPC Methods
==========================================

RPC methods for querying and interacting with the AICF pool:
- aicf.getPoolState: Get current pool state (balance, cap, issued, spent)
- aicf.getParams: Get AICF parameters
- aicf.submitProof: Submit an AICF proof for verification
- aicf.getUsageStats: Get usage statistics
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.aicf_pool import AicfPoolState, AicfProof, apply_aicf_proof, verify_aicf_proof
from core.db.state_db import StateDB
from rpc import deps
from rpc import errors as rpc_errors
from rpc.methods import method

log = logging.getLogger("rpc.methods.aicf")

# --------------------------------------------------------------------------------
# RPC Method Implementations
# --------------------------------------------------------------------------------


@method("aicf.getPoolState", desc="Get AICF pool state")
async def get_pool_state(d: deps.Deps, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    aicf.getPoolState() -> pool state dict
    
    Returns current AICF pool state including:
    - balance: current pool balance (base units)
    - cap: maximum pool capacity (base units)
    - issued_total: cumulative issued (mined) into pool
    - spent_total: cumulative spent from pool
    - balance_anm: balance in ANM (human-readable)
    - cap_anm: cap in ANM
    - percent_filled: percentage of cap that has been issued
    """
    state = StateDB(deps.kv)
    pool_data = state.get_aicf_pool_state()
    
    if pool_data is None:
        # AICF pool not initialized (old chain or disabled)
        return {
            "enabled": False,
            "balance": 0,
            "cap": 0,
            "issued_total": 0,
            "spent_total": 0,
            "balance_anm": 0.0,
            "cap_anm": 0.0,
            "percent_filled": 0.0,
        }
    
    # Convert to human-readable
    COIN = 1_000_000_000
    balance = int(pool_data.get("balance", 0))
    cap = int(pool_data.get("cap", 0))
    issued = int(pool_data.get("issued_total", 0))
    spent = int(pool_data.get("spent_total", 0))
    
    return {
        "enabled": True,
        "balance": balance,
        "cap": cap,
        "issued_total": issued,
        "spent_total": spent,
        "balance_anm": balance / COIN,
        "cap_anm": cap / COIN,
        "issued_anm": issued / COIN,
        "spent_anm": spent / COIN,
        "percent_filled": (issued / cap * 100) if cap > 0 else 0,
        "miner_credits_count": len(pool_data.get("miner_credits", {})),
        "epochs_tracked": len(pool_data.get("epoch_proofs", {})),
    }


@method("aicf.getParams", desc="Get AICF parameters")
async def get_params(d: deps.Deps, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    aicf.getParams() -> AICF parameters
    
    Returns all AICF parameters from chain config:
    - enabled: whether AICF is enabled
    - cap_anm: pool capacity in ANM
    - reward_per_proof_anm: reward per accepted proof
    - max_proofs_per_block: rate limit per block
    - max_proofs_per_miner_per_epoch: rate limit per miner per epoch
    - epoch_blocks: epoch length in blocks
    - min_work_difficulty: minimum work units required
    - verification_timeout_ms: proof verification timeout
    - fee_routing_pct: percentage of fees routed to AICF pool
    """
    # Get chain ID from state or params
    chain_id = getattr(d, "chain_id", 1)
    
    try:
        # Load network-specific params from params.yaml
        import yaml
        from pathlib import Path
        params_path = Path(__file__).parents[2] / "spec" / "params.yaml"
        with open(params_path) as f:
            params_yaml = yaml.safe_load(f)
        
        # Get network-specific config
        network_key = f"animica:{chain_id}"
        network_params = params_yaml.get("networks", {}).get(network_key, {})
        aicf_config = network_params.get("aicf_pool", {})
    except Exception as e:
        log.warning(f"Failed to load AICF params: {e}")
        aicf_config = {}
    
    if not aicf_config.get("enabled", False):
        return {"enabled": False}
    
    return {
        "enabled": True,
        "cap_anm": float(aicf_config.get("cap_anm", 0)),
        "reward_per_proof_anm": float(aicf_config.get("reward_per_proof_anm", 0)),
        "max_proofs_per_block": int(aicf_config.get("max_proofs_per_block", 0)),
        "max_proofs_per_miner_per_epoch": int(aicf_config.get("max_proofs_per_miner_per_epoch", 0)),
        "epoch_blocks": int(aicf_config.get("epoch_blocks", 0)),
        "min_work_difficulty": int(aicf_config.get("min_work_difficulty", 0)),
        "verification_timeout_ms": int(aicf_config.get("verification_timeout_ms", 0)),
        "fee_routing_pct": int(aicf_config.get("fee_routing_pct", 0)),
    }


@method("aicf.submitProof", desc="Submit AICF proof for verification")
async def submit_proof(d: deps.Deps, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    aicf.submitProof(miner_addr, work_units, proof_data, timestamp, nonce) -> result
    
    Submit an AICF proof for verification and potential reward.
    
    Args:
        miner_addr: Miner address (bech32 or hex)
        work_units: Work units completed
        proof_data: Proof payload (hex string)
        timestamp: Unix timestamp
        nonce: Nonce for uniqueness
        
    Returns:
        {
            "valid": bool,
            "reason": str,
            "reward_amount": int (base units),
            "reward_anm": float (ANM),
            "tx_hash": str (if valid and submitted)
        }
    """
    # Parse parameters
    try:
        miner_addr_str = params.get("miner_addr")
        if not miner_addr_str:
            raise rpc_errors.InvalidParams("miner_addr required")
        
        # Convert address to bytes
        from core.utils.address import address_to_bytes
        miner_addr = address_to_bytes(miner_addr_str)
        
        work_units = int(params.get("work_units", 0))
        proof_data_hex = params.get("proof_data", "")
        proof_data = bytes.fromhex(proof_data_hex.removeprefix("0x"))
        timestamp = int(params.get("timestamp", 0))
        nonce = int(params.get("nonce", 0))
        
    except (ValueError, TypeError) as e:
        raise rpc_errors.InvalidParams(f"Invalid parameters: {e}")
    
    # Create proof object
    try:
        proof = AicfProof(
            miner_addr=miner_addr,
            work_units=work_units,
            proof_data=proof_data,
            timestamp=timestamp,
            nonce=nonce,
        )
    except Exception as e:
        raise rpc_errors.InvalidParams(f"Invalid proof data: {e}")
    
    # Get current height and chain params
    state = StateDB(d.kv)
    chain_id = getattr(d, "chain_id", 1)
    
    try:
        # Load network-specific params from params.yaml
        import yaml
        from pathlib import Path
        params_path = Path(__file__).parents[2] / "spec" / "params.yaml"
        with open(params_path) as f:
            params_yaml = yaml.safe_load(f)
        
        # Get network-specific config
        network_key = f"animica:{chain_id}"
        network_params = params_yaml.get("networks", {}).get(network_key, {})
    except Exception as e:
        raise rpc_errors.RpcError(f"Failed to load chain params: {e}")
    
    # Get current pool state
    pool_data = state.get_aicf_pool_state()
    if pool_data is None:
        raise rpc_errors.RpcError("AICF pool not initialized")
    
    pool_state = AicfPoolState.from_dict(pool_data)
    
    # Get current height from deps or block db
    current_height = getattr(d, "height", 0)
    if hasattr(d, "blocks") and d.blocks:
        head_height, _ = d.blocks.get_head()
        current_height = head_height if head_height is not None else 0
    
    # Verify proof
    is_valid, reason, reward_amount = verify_aicf_proof(
        proof, network_params, current_height, pool_state
    )
    
    COIN = 1_000_000_000
    result = {
        "valid": is_valid,
        "reason": reason,
        "reward_amount": reward_amount,
        "reward_anm": reward_amount / COIN,
    }
    
    if not is_valid:
        return result
    
    # Proof is valid - in a real implementation, we would:
    # 1. Create AICF_PROOF transaction
    # 2. Submit to mempool
    # 3. Return tx_hash
    # For now, just return the validation result
    
    log.info(
        f"Valid AICF proof from {miner_addr.hex()[:16]}... "
        f"work={work_units}, reward={reward_amount} nANM"
    )
    
    result["tx_hash"] = "0x" + ("00" * 32)  # Placeholder
    
    return result


@method("aicf.getUsageStats", desc="Get AICF usage statistics")
async def get_usage_stats(d: deps.Deps, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    aicf.getUsageStats() -> usage statistics
    
    Returns:
        {
            "total_proofs": int,
            "total_miners": int,
            "recent_proofs": list of recent proof submissions,
            "top_miners": list of top miners by credits
        }
    """
    state = StateDB(deps.kv)
    pool_data = state.get_aicf_pool_state()
    
    if pool_data is None:
        return {
            "total_proofs": 0,
            "total_miners": 0,
            "recent_proofs": [],
            "top_miners": [],
        }
    
    miner_credits = pool_data.get("miner_credits", {})
    epoch_proofs = pool_data.get("epoch_proofs", {})
    
    # Calculate total proofs across all epochs
    total_proofs = 0
    for epoch_data in epoch_proofs.values():
        for count in epoch_data.values():
            total_proofs += count
    
    # Get top miners
    COIN = 1_000_000_000
    top_miners = []
    for miner_hex, credits in sorted(miner_credits.items(), key=lambda x: x[1], reverse=True)[:10]:
        top_miners.append({
            "address": "0x" + miner_hex if not miner_hex.startswith("0x") else miner_hex,
            "credits": credits,
            "credits_anm": credits / COIN,
        })
    
    return {
        "total_proofs": total_proofs,
        "total_miners": len(miner_credits),
        "top_miners": top_miners,
        "epochs_tracked": len(epoch_proofs),
    }

