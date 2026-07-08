"""ANM-2026-07 incident clawback — height-gated state migration.

Context: the public, unauthenticated ``/wallet-export`` sidecar on
wallet.animica.org leaked the private keys of the node's custodial wallets. An
attacker downloaded two victims' keys and swept their balances to a single
collection address. ml_dsa_65 (0x1003) is a real, secure signature scheme, so
there is no way to move the funds back with a normal transaction (we do not hold
the attacker's key). The only network-valid recovery is a coordinated,
protocol-level state move that every node applies identically — this module.

At the activation height on mainnet, it moves the attacker's whole balance from
the collection address back to two *fresh* victim recovery addresses (ml_dsa_65
keys generated after the leak, held offline root-only, never in the exposed
store). See memory ``project-wallet-export-key-leak``.

Design contract:
  * Deterministic — every node running this code computes the identical move, so
    balances stay consistent across the network. Ships in pip 6.0.5.
  * Value-preserving — total supply is unchanged (debit == sum of credits).
  * Defensive — this function NEVER raises out of block execution. If the
    attacker balance is already 0 or partially spent, it moves whatever is
    available (split proportionally) and logs. A recovery migration must never
    halt block import.
  * Reorg-safe — it runs as part of block H's state transition, which the
    importer re-executes against pre-H state after any revert
    (``_apply_state_reorg`` / ``_rebuild_state_from_canonical``). So it applies
    exactly once per execution of H; no external idempotency marker is needed.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# Attacker collection digest (32-byte on-chain account key). Held 867,387.244 ANM
# with zero outgoing transactions at incident time (2026-07-08).
_THIEF = bytes.fromhex(
    "ce0c733bab5f7c86fe106f4f817892adc952ec6312c8378c6b45ebd230d4870c"
)

# (recovery_digest, owed_nano). Sum == the two victims' drained totals. The
# recovery addresses are fresh ml_dsa_65 wallets held offline (root-only), never
# in the leaked store:
#   victim1 (was 'pool'  anim1zqparpe3c…, drained 496,685.385) ->
#       anim1zqpkqtstw3uhku4s9evdzpafp9lx2aul5pvv0ug0agf453cqq65d6dq3nu6lz
#   victim2 (was 'lajot' anim1zqpjkzqy5…, drained 370,701.859) ->
#       anim1zqpm3rah0x25gu9n5crxagvh6skfc3fr6mwpm0ztnwuhqlqs2h5fkuc8nm4a7
_RECOVERIES = (
    (
        bytes.fromhex(
            "602e0b74797b72b02e58d107a9097e65779fa058c7f10fea135a470006a8dd34"
        ),
        496_685_385_000_000,
    ),
    (
        bytes.fromhex(
            "b88fb779954470b3a6066ea197d42c9c4523d6dc1dbc4b9bb9707c1055e89b73"
        ),
        370_701_859_000_000,
    ),
)
_TOTAL_OWED = sum(a for _, a in _RECOVERIES)

# Mainnet (chain_id 1) activation height. Operators may retune before the height
# is reached via ANIMICA_CLAWBACK_2026_07_HEIGHT (int, accepts 0x…). MUST match on
# every node that produces or validates blocks at/after the height, or their state
# diverges (root_commitment is not enforced until 40,000, so a mismatch does not
# reject blocks — it silently forks account balances; ship this to every node).
_DEFAULT_MAINNET_HEIGHT = 39_584


def activation_height(chain_id: int):
    """Activation height for this migration on ``chain_id`` (None if not applicable)."""
    env = os.getenv("ANIMICA_CLAWBACK_2026_07_HEIGHT", "").strip()
    if env:
        try:
            return int(env, 0)
        except ValueError:
            log.warning(
                "ignoring non-integer ANIMICA_CLAWBACK_2026_07_HEIGHT=%r", env
            )
    return _DEFAULT_MAINNET_HEIGHT if int(chain_id) == 1 else None


def apply_clawback_if_active(state, height: int, chain_id: int) -> None:
    """Move the attacker's balance to the victim recovery addresses at the
    activation height. Non-fatal by contract: any error is logged, never raised."""
    try:
        H = activation_height(chain_id)
        if H is None or int(chain_id) != 1 or int(height) != int(H):
            return

        # Sanctioned balance writer (passes the state_db chokepoint guard).
        from execution.state.apply_balance import confirmed_balance_mutate as _mut

        avail = int(state.get_balance(_THIEF))
        if avail <= 0:
            log.error(
                "ANM-2026-07 clawback H=%d: attacker balance is 0; nothing to recover",
                H,
            )
            return

        # Move the WHOLE attacker balance, split proportionally to what each victim
        # was owed (the last recipient absorbs the rounding remainder). This empties
        # the attacker account so no spendable remnant can remain, and it is exact
        # when avail == owed_total (the confirmed incident case).
        moved = 0
        splits = []
        n = len(_RECOVERIES)
        for i, (dig, owed) in enumerate(_RECOVERIES):
            share = (avail * owed // _TOTAL_OWED) if i < n - 1 else (avail - moved)
            splits.append((dig, share))
            moved += share

        # Debit the attacker exactly `moved` (== avail -> 0), then credit victims.
        _mut(
            state,
            _THIEF,
            -moved,
            reason="ANM_2026_07_CLAWBACK_DEBIT",
            tx_hash=None,
            height=height,
            callsite="execution.migrations.clawback_2026_07",
        )
        for dig, share in splits:
            if share > 0:
                _mut(
                    state,
                    dig,
                    share,
                    reason="ANM_2026_07_CLAWBACK_CREDIT",
                    tx_hash=None,
                    height=height,
                    callsite="execution.migrations.clawback_2026_07",
                )

        log.error(
            "ANM-2026-07 CLAWBACK APPLIED at H=%d: recovered %d nano (%.3f ANM) "
            "from attacker -> %d victim recovery addrs (owed_total=%d)",
            H,
            moved,
            moved / 1e9,
            len([s for s in splits if s[1] > 0]),
            _TOTAL_OWED,
        )
    except Exception as e:  # never halt block execution on a recovery migration
        log.error(
            "ANM-2026-07 clawback error at height=%s (non-fatal, block continues): %r",
            height,
            e,
        )
