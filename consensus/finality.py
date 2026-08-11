"""FORK_FINALITY_DEPTH (9.5.0) — one reorg bound that every node agrees on.

A depth bound already existed and was already enforced (`DEFAULT_MAX_REORG_DEPTH = 96`,
checked in `consensus.fork_choice.WeightForkChoice.add_block`). What did not exist was
agreement about the number: `ANIMICA_MAX_REORG_DEPTH` is an unbounded operator override,
so two honest nodes could hold wildly different views of what is reversible.

Both directions of that misconfiguration are harmful, in opposite ways:

  * Too PERMISSIVE (say 10^9) accepts an arbitrarily deep reorg, so an attacker with a
    heavier private chain can rewrite months of history.
  * Too STRICT (0) refuses EVERY reorg. On a chain that produces natural one-block
    forks — this one has produced at least three — the node strands itself on the losing
    sibling and needs a hand-pinned checkpoint to recover. That is the outage that has
    happened repeatedly here.

From the fork height the effective bound is clamped into [MIN_REORG_DEPTH,
FINALITY_DEPTH]. The ceiling is the finality property: a block FINALITY_DEPTH deep is
irreversible on every node, whatever the local config says. The floor makes the
self-wedge impossible.

Two properties worth being explicit about:

  * This never REJECTS a block. The guard only declines to make a tip canonical, so a
    node that is merely behind can still catch up later; it can never be converted into
    a permanently split one. That is the whole reason a bound is safe to make uniform
    while a rejection rule would not be.
  * It is a pure function of (configured value, height, chain_id) — no clocks, no
    node-local state.
"""

from __future__ import annotations

from typing import Optional

from core.network_params import FORK_FINALITY_DEPTH, is_fork_active

# A block this many blocks deep is final: no node will reorg it away once the fork is
# active. 100 is far above anything this chain has produced naturally (its forks have
# been one block deep) and far below the depth at which an honest node could plausibly
# be behind and still need to reorganise.
FINALITY_DEPTH: int = 100

# The shallowest bound a node may run. Below this a natural one-block fork could strand
# it, which is the failure this chain keeps hitting. 8 leaves ample room for the
# multi-block reorgs that ordinary propagation delay can produce.
MIN_REORG_DEPTH: int = 8


def finality_active(height: int, *, chain_id: int = 1) -> bool:
    return bool(is_fork_active(FORK_FINALITY_DEPTH, int(height), chain_id=int(chain_id)))


def effective_reorg_depth(
    configured: Optional[int],
    height: int,
    *,
    chain_id: int = 1,
) -> Optional[int]:
    """The reorg bound to enforce for a candidate at `height`.

    Below the fork height the configured value is returned untouched, including None
    (meaning "no bound"), so historical behaviour is byte-identical and replay is safe.

    From the fork height the value is clamped into [MIN_REORG_DEPTH, FINALITY_DEPTH],
    and an unset bound becomes FINALITY_DEPTH rather than "unlimited" — an operator who
    never configured one should still get finality.
    """
    if not finality_active(height, chain_id=chain_id):
        return configured
    if configured is None:
        return FINALITY_DEPTH
    try:
        value = int(configured)
    except (TypeError, ValueError):
        return FINALITY_DEPTH
    if value < MIN_REORG_DEPTH:
        return MIN_REORG_DEPTH
    if value > FINALITY_DEPTH:
        return FINALITY_DEPTH
    return value


def is_final(block_height: int, head_height: int, *, chain_id: int = 1) -> bool:
    """True when a block at `block_height` can no longer be reorganised away.

    Reported against the current head, so it answers the question a wallet or an
    exchange actually asks: "is this deep enough to treat as settled?"
    """
    depth = int(head_height) - int(block_height)
    if depth < 0:
        return False
    if not finality_active(head_height, chain_id=chain_id):
        return False
    return depth >= FINALITY_DEPTH
