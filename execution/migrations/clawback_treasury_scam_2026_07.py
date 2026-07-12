"""ANM-2026-07 treasury-scam clawback — height-gated state migration.

Context: three accounts obtained foundation-treasury funds by scamming the
operator. The funds now sit where they cannot be recovered by a normal
transaction (total ~5,653,662.648 ANM):

  * anim1zqpuzgv9away6yyttjxrl957epvgsg3pdyl3hmcavhmc443n2gqgjjq8k0ers
    (account 0xc12185eb…, ml_dsa_65 / 0x1003) — the bulk, ~4,000,799.837 ANM.
  * anim1zqpru7w4xdp6g9s4vrd0s4f7ypx82vrtejf2n46jgq56u2a98p0uvkqgknned
    (account 0x3e79d533…, SPHINCS+ / 0x1002 *stranded* scheme) — ~9,248.762
    ANM. A 0x1002 wallet cannot spend at all on this chain, so this balance is
    permanently frozen otherwise; recovery is the only way it ever moves.
  * 0x9b4da8ae7f7385da48b2fffcf45ad3216834615100185dbb68f4db5b774a8352
    — ~1,643,614.049 ANM (third scam recipient).

Both balances are REAL executed state (read from the authoritative
state.getBalance at build time), so moving them is value-preserving recovery —
not issuance. The destination is the foundation treasury, per the standing
future-clawback policy (``_recovery_policy``).

Design contract (identical to ``clawback_2026_07``):
  * Deterministic — every node computes the same move, so balances stay
    consistent across the network.
  * Value-preserving — sum of source debits == treasury credit; total supply
    unchanged.
  * Defensive — NEVER raises out of block execution. Moves whatever balance is
    actually present at the activation height (an account already emptied
    contributes 0); logs and continues.
  * Reorg-safe — runs as part of block H's state transition, re-executed
    against pre-H state after any revert; applies exactly once per execution of
    H, no external idempotency marker needed.
  * Forward-only — activation height is in the FUTURE (default 44,444,
    env-overridable before it is reached). Grandfathered below H. Ships in 7.1.9;
    every node that produces or validates blocks at/after H must run it.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# Source accounts (32-byte on-chain account keys) that hold the scammed treasury
# funds. Balances noted are the authoritative state.getBalance at build time
# (2026-07-12); the migration moves whatever is actually present at height H.
_SCAM_SOURCES: tuple[bytes, ...] = (
    # anim1zqpuzgv9…  ml_dsa_65, ~4,000,799.837 ANM
    bytes.fromhex("c12185ebba4d108b5c8c3f969ec858882221693f1bef1d65f78ad63352008948"),
    # anim1zqpru7w4…  SPHINCS+ 0x1002 stranded (unspendable), ~9,248.762 ANM
    bytes.fromhex("3e79d53343a4161560daf8553e204c75306bcc92a9d7524029ae2ba5385fc658"),
    # 0x9b4da8ae…  ~1,643,614.049 ANM (third scam recipient)
    bytes.fromhex("9b4da8ae7f7385da48b2fffcf45ad3216834615100185dbb68f4db5b774a8352"),
)

# Mainnet (chain_id 1) activation height. Forward-only: MUST be above the head
# when this ships. Retune before the height via
# ANIMICA_CLAWBACK_TREASURY_SCAM_HEIGHT (int, accepts 0x…). MUST match on every
# node at/after the height or account balances diverge.
_DEFAULT_MAINNET_HEIGHT = 44_444


def activation_height(chain_id: int):
    """Activation height for this migration on ``chain_id`` (None if not applicable)."""
    env = os.getenv("ANIMICA_CLAWBACK_TREASURY_SCAM_HEIGHT", "").strip()
    if env:
        try:
            return int(env, 0)
        except ValueError:
            log.warning(
                "ignoring non-integer ANIMICA_CLAWBACK_TREASURY_SCAM_HEIGHT=%r", env
            )
    return _DEFAULT_MAINNET_HEIGHT if int(chain_id) == 1 else None


def apply_clawback_if_active(state, height: int, chain_id: int) -> None:
    """Move the scam-source balances to the foundation treasury at the activation
    height. Non-fatal by contract: any error is logged, never raised."""
    try:
        H = activation_height(chain_id)
        if H is None or int(chain_id) != 1 or int(height) != int(H):
            return

        # Sanctioned balance writer (passes the state_db chokepoint guard) and the
        # standing future-clawback destination (foundation treasury).
        from execution.state.apply_balance import confirmed_balance_mutate as _mut
        from execution.migrations._recovery_policy import (
            FUTURE_CLAWBACK_RECOVERY_KEY as _DEST,
        )

        recovered = 0
        for src in _SCAM_SOURCES:
            avail = int(state.get_balance(src))
            if avail <= 0:
                continue
            # Debit the whole source balance (-> 0), then accumulate for a single
            # treasury credit so debits == credit exactly (value-preserving).
            _mut(
                state,
                src,
                -avail,
                reason="ANM_2026_07_TREASURY_SCAM_CLAWBACK_DEBIT",
                tx_hash=None,
                height=height,
                callsite="execution.migrations.clawback_treasury_scam_2026_07",
            )
            recovered += avail

        if recovered <= 0:
            log.error(
                "ANM-2026-07 treasury-scam clawback H=%d: sources empty; nothing to recover",
                H,
            )
            return

        _mut(
            state,
            _DEST,
            recovered,
            reason="ANM_2026_07_TREASURY_SCAM_CLAWBACK_CREDIT",
            tx_hash=None,
            height=height,
            callsite="execution.migrations.clawback_treasury_scam_2026_07",
        )
        log.error(
            "ANM-2026-07 TREASURY-SCAM CLAWBACK APPLIED at H=%d: recovered %d nano "
            "(%.3f ANM) from %d scam source(s) -> foundation treasury",
            H,
            recovered,
            recovered / 1e9,
            len(_SCAM_SOURCES),
        )
    except Exception as e:  # never halt block execution on a recovery migration
        log.error(
            "ANM-2026-07 treasury-scam clawback error at height=%s (non-fatal, block continues): %r",
            height,
            e,
        )
