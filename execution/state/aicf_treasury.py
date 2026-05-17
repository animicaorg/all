"""
AICF treasury sweep — consensus state-transition rule.

Defines the canonical AICF treasury address per chain_id and the
deterministic post-block hook that converts any balance held at that
address into AICF state-pool inflow at the current epoch.

This is a consensus rule, not a node-local operation. Every node
upgrading to the release that ships this file applies the same sweep
when executing a block, so the resulting state root is identical
across the network. The treasury "address" is a magic constant from
the chain's point of view — nobody needs to operate a node at the
treasury, and the sweep doesn't require any private key.

Activation semantics:
- Sweep fires after every block whose treasury balance > 0.
- Balance is moved (debit treasury EOA, credit pool via add_inflow)
  in a single deterministic step at block finalization.
- Inflow is credited to the *current* epoch (computed from block
  height + epoch_length params).

Compatibility note: introducing this rule changes block-execution
semantics. Nodes running prior versions will compute different state
roots once any transfer to the treasury occurs. Coordinate the
upgrade window or accept that the chain forks at the treasury's
first non-zero balance. This module is intentionally separate from
the existing aicf_state primitives so the activation surface is
small and reviewable.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from execution.state.aicf_state import (
    add_inflow,
    compute_epoch,
    get_epoch_length,
)
from execution.state.apply_balance import debit

log = logging.getLogger("animica.execution.aicf_treasury")


# Canonical treasury addresses per chain_id, as 32-byte digests
# (sha3-256(pubkey) — the bytes the tx body's `to` field uses).
# These must match the bech32 strings exposed by
# rpc/methods/aicf_jobs.py::_DEFAULT_TREASURY_BY_CHAIN; the helper
# `treasury_address_bytes()` is the single source of truth at runtime.
_TREASURY_DIGEST_BY_CHAIN: Dict[int, bytes] = {
    # 1 (mainnet) — anim1zqpf6a5hvup7kggxdutt3tgswz4aa9rwlpsz8ywf73m4l5cmzhfk7pcnh6kgt
    1: bytes.fromhex(
        "9d76976703eb21066f16b8ad1070abde946ef8602391c9f4775fd31b15d36f07"
    ),
}


def treasury_address_bytes(chain_id: int) -> Optional[bytes]:
    """Return the 32-byte treasury digest for ``chain_id``, or None
    if this chain has no treasury rule registered yet."""
    return _TREASURY_DIGEST_BY_CHAIN.get(int(chain_id))


def _block_height(block_env: Any) -> int:
    height = getattr(block_env, "height", None)
    if height is None and isinstance(block_env, dict):
        height = block_env.get("height")
    try:
        return int(height or 0)
    except (TypeError, ValueError):
        return 0


def _chain_id_from(block_env: Any, params: Any) -> int:
    for source in (block_env, params):
        if source is None:
            continue
        cid = getattr(source, "chain_id", None)
        if cid is None and isinstance(source, dict):
            cid = source.get("chain_id") or source.get("chainId")
        if cid is not None:
            try:
                return int(cid)
            except (TypeError, ValueError):
                continue
    return 0


def sweep_treasury_into_pool(
    state: Any,
    *,
    block_env: Any,
    params: Optional[Any] = None,
) -> int:
    """Post-block consensus hook: drain the AICF treasury balance into
    the state pool's current-epoch inflow.

    Returns the amount swept (0 if the chain has no treasury rule or
    the treasury balance was already 0). Idempotent: a second call
    on the same finalized state observes a zeroed treasury and is a
    no-op.

    Implementation notes:
    - Reads `state.get_balance(treasury_bytes)` — the same accessor
      that normal value transfers use, so any tx that landed value
      into the treasury during this block contributes here.
    - Calls `debit()` with a clear `reason="aicf_treasury_sweep"`
      and `height=` for the audit trail in
      apply_balance.confirmed_balance_mutate logs.
    - Calls `add_inflow()` which updates both the per-epoch inflow
      ledger and the cached pool_balance, the same way block-reward
      slicing does.
    """
    chain_id = _chain_id_from(block_env, params)
    treasury = treasury_address_bytes(chain_id)
    if treasury is None:
        return 0

    try:
        balance = int(state.get_balance(treasury) or 0)
    except Exception as exc:
        log.warning("treasury sweep: get_balance failed: %s", exc)
        return 0
    if balance <= 0:
        return 0

    height = _block_height(block_env)
    try:
        epoch_length = get_epoch_length(state)
    except Exception:
        epoch_length = 100
    if epoch_length <= 0:
        epoch_length = 100
    epoch = compute_epoch(height, epoch_length)

    try:
        debit(
            state,
            treasury,
            balance,
            reason="aicf_treasury_sweep",
            height=height,
            callsite="execution.state.aicf_treasury.sweep_treasury_into_pool",
        )
    except Exception as exc:
        log.warning("treasury sweep: debit failed: %s", exc)
        return 0

    try:
        add_inflow(state, epoch, balance)
    except Exception as exc:
        # We've already debited the treasury — we must succeed at the
        # inflow side too or the swept value is unaccounted for.
        # Re-credit and surface the error.
        log.error(
            "treasury sweep: add_inflow failed AFTER debit; re-crediting: %s", exc
        )
        try:
            from execution.state.apply_balance import credit  # local import to avoid cycle
            credit(
                state,
                treasury,
                balance,
                reason="aicf_treasury_sweep_rollback",
                height=height,
                callsite="execution.state.aicf_treasury.sweep_treasury_into_pool",
            )
        except Exception:
            pass
        return 0

    log.info(
        "treasury sweep: chain_id=%s height=%s epoch=%s amount=%s",
        chain_id, height, epoch, balance,
    )
    return balance


__all__ = ["sweep_treasury_into_pool", "treasury_address_bytes"]
