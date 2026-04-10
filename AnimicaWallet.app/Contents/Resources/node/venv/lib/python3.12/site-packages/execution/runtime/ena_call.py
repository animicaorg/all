"""
execution.runtime.ena_call — ENA Inference Call Transaction Handler
===================================================================

Implements ENA (Elastic Neural Agents) inference call transaction handling with:
- Fee collection and routing (AICF, provider, treasury, burn)
- Provider selection and validation
- Receipt generation for compute attribution
- Worker attribution support

Fee Routing:
- AICF pool: 60% (default)
- Provider: 35% (default)
- Treasury: 5% (default)
- Burn: 0% (default)

Fees are paid upfront by the requester and split deterministically.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Mapping, Optional

from ..errors import ExecError
from ..types.status import TxStatus

if TYPE_CHECKING:
    from ..types.result import ApplyResult
    from .env import BlockEnv, TxEnv

log = logging.getLogger("execution.runtime.ena_call")


class ENACallError(ExecError):
    """Raised when ENA call transaction fails."""


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """Tolerant getter over mapping or attribute lookup."""
    for n in names:
        if isinstance(obj, Mapping) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _state_root(state: Any) -> bytes:
    """Best-effort state root extraction."""
    for name in ("compute_state_root", "state_root", "merkle_root"):
        fn = getattr(state, name, None)
        if callable(fn):
            try:
                root = fn()
                if isinstance(root, (bytes, bytearray)):
                    b = bytes(root)
                    if len(b) == 32:
                        return b
                    return b[:32].rjust(32, b"\x00")
            except Exception:
                pass
        val = getattr(state, name, None)
        if isinstance(val, (bytes, bytearray)):
            return bytes(val)[:32].rjust(32, b"\x00")
    return b"\x00" * 32


def apply_ena_call(
    tx: Any,
    state: Any,
    block_env: BlockEnv,
    tx_env: TxEnv,
    *,
    params: Optional[Mapping[str, Any]] = None,
    capabilities: Optional[Any] = None,
) -> "ApplyResult":
    """
    Execute an ENA inference call transaction.
    
    Flow:
    1. Parse ENA call payload (prompt, model, provider_id, worker_id, fee)
    2. Load fee configuration and validate fee amount
    3. Debit fee from sender
    4. Split fee: AICF, provider, treasury, burn
    5. Route ENA call to capabilities adapter
    6. Generate receipt with fee breakdown and result
    
    Args:
        tx: Transaction object with ENA call payload
        state: Mutable state handle
        block_env: Block execution environment
        tx_env: Transaction execution environment
        params: Chain parameters with ENA/AICF config
        capabilities: Capabilities adapter for AI inference
    
    Returns:
        ApplyResult with ENA call result or error
    """
    from ..types.result import ApplyResult
    from ..types.events import LogEvent
    from .ena_fee_config import load_ena_fee_config_from_params
    
    # Extract sender
    sender = bytes(getattr(tx_env, "sender", b"\x00" * 32))
    current_height = int(getattr(block_env, "height", 0) or 0)
    
    # Parse ENA call payload
    tx_data = _get(tx, "data", "payload", "input")
    if tx_data is None:
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=21000,
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"ena.call.error"],
                    data=b"missing ENA call payload",
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    
    # Parse payload
    call_data = tx_data
    if isinstance(tx_data, bytes):
        try:
            import cbor2
            call_data = cbor2.loads(tx_data)
        except Exception:
            call_data = {}
    
    # Extract call fields
    prompt = _get(call_data, "prompt", "input", "text")
    model = _get(call_data, "model", "model_id")
    provider_id = _get(call_data, "provider_id", "provider")
    worker_id = _get(call_data, "worker_id", "worker")
    fee = _get(call_data, "fee", "amount", "value")
    
    if prompt is None or not isinstance(prompt, str):
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=21000,
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"ena.call.error"],
                    data=b"missing or invalid prompt",
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    
    # Parse fee
    try:
        total_fee = int(fee) if fee is not None else 0
    except Exception:
        total_fee = 0
    
    # Load fee configuration
    fee_config = load_ena_fee_config_from_params(params)
    
    # Validate fee
    if total_fee < fee_config.min_fee_nano:
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=21000,
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"ena.call.error"],
                    data=f"fee {total_fee} below minimum {fee_config.min_fee_nano}".encode(),
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    
    # Validate provider
    if not provider_id:
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=21000,
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"ena.call.error"],
                    data=b"missing provider_id",
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    
    # Debit fee from sender
    try:
        from execution.state.apply_balance import debit as state_debit
        
        state_debit(
            state,
            sender,
            total_fee,
            reason="ENA_CALL_FEE",
            tx_hash=None,
            height=current_height,
            callsite="execution.runtime.ena_call",
        )
    except Exception as e:
        log.error(f"Failed to debit fee from sender: {e}", exc_info=True)
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=21000,
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"ena.call.error"],
                    data=f"insufficient balance for fee: {e}".encode()[:256],
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    
    # Split fee
    fee_split = fee_config.split_fee(total_fee)
    aicf_cut = fee_split["aicf_cut"]
    provider_cut = fee_split["provider_cut"]
    burn_cut = fee_split["burn_cut"]
    treasury_cut = fee_split["treasury_cut"]
    
    # Route fees
    try:
        # Credit AICF pool
        if aicf_cut > 0:
            from execution.state.aicf_state import add_inflow, compute_epoch, get_epoch_length
            
            epoch_length = get_epoch_length(state)
            if epoch_length <= 0:
                epoch_length = 100
            current_epoch = compute_epoch(current_height, epoch_length)
            
            add_inflow(state, current_epoch, aicf_cut)
            log.info(f"ENA call: AICF pool credited {aicf_cut} (fee split)")
        
        # Credit provider accrued rewards
        if provider_cut > 0 and provider_id:
            from execution.state.aicf_state import (
                get_provider_accrued,
                set_provider_accrued,
            )
            
            current_accrued = get_provider_accrued(state, provider_id)
            new_accrued = current_accrued + provider_cut
            set_provider_accrued(state, provider_id, new_accrued)
            log.info(f"ENA call: Provider {provider_id} accrued {provider_cut}")
        
        # Handle treasury cut (if any)
        if treasury_cut > 0:
            # Credit treasury module account
            # For now, we'll use a fixed treasury address
            treasury_addr = b"\x00" * 31 + b"\x01"  # 0x0...01
            from execution.state.apply_balance import credit as state_credit
            
            state_credit(
                state,
                treasury_addr,
                treasury_cut,
                reason="ENA_TREASURY_FEE",
                tx_hash=None,
                height=current_height,
                callsite="execution.runtime.ena_call",
            )
            log.info(f"ENA call: Treasury credited {treasury_cut}")
        
        # Handle burn (if any) - just let it sit in limbo (reduce pool balance)
        if burn_cut > 0:
            log.info(f"ENA call: Burned {burn_cut}")
        
        # Update worker stats (if worker_id provided)
        if worker_id and provider_id:
            from execution.state.aicf_state import update_worker_stats
            
            # Estimate tokens processed (rough approximation based on prompt length)
            tokens_processed = len(prompt.split())  # Very rough estimate
            
            update_worker_stats(
                state,
                provider_id,
                str(worker_id),
                tokens_delta=tokens_processed,
                fees_delta=provider_cut,
                job_completed=True,
            )
    
    except Exception as e:
        log.error(f"Failed to route fees: {e}", exc_info=True)
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=30000,
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"ena.call.error"],
                    data=f"fee routing failed: {e}".encode()[:256],
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    
    # Phase 2: ENA service invocation (capabilities adapter integration pending)
    # For now, return a stub response
    result_text = f"[ENA stub] Processed prompt (len={len(prompt)})"
    
    # Success - return receipt with fee breakdown (use strings to avoid BigInt issues)
    return ApplyResult(
        status=TxStatus.SUCCESS,
        gas_used=50000,
        logs=[
            LogEvent(
                address=b"\x00" * 20,
                topics=[b"ena.call.success"],
                data=f"fee={total_fee} provider={provider_id}".encode(),
            )
        ],
        state_root=_state_root(state),
        receipt={
            "type": "ena_call",
            "model": model or "default",
            "provider_id": provider_id,
            "worker_id": worker_id,
            "fee_paid": str(total_fee),  # String to avoid BigInt
            "aicf_cut": str(aicf_cut),
            "provider_cut": str(provider_cut),
            "burn_cut": str(burn_cut),
            "treasury_cut": str(treasury_cut),
            "result": result_text,
            "prompt_hash": None,  # Don't store raw prompt on-chain
        },
    )


__all__ = [
    "apply_ena_call",
    "ENACallError",
]
