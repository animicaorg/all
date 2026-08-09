"""FORK_SERVICE_CARVE (9.5.0) — split the reserved service slice, deterministically.

The rule in one line: from the activation height the miner subsidy loses a fixed
percentage whether or not any service provider claims it, and settlement anchors decide
only whether that slice reaches providers or falls to escrow.

Why not "pay miners who serve": nothing in a block can prove a miner served inference.
There is no on-chain worker keyring, no job ids, no result hashes, and `header.extra` is
miner-authored — a stub box with no ML stack could sign "I served 100 jobs" and no
validator could contradict it. A rule that pays on self-attestation pays everyone who
edits a config value, out of the honest miners' subsidy. So the chain never measures
serving; it only declines to hand the service slice to the block producer.

The arithmetic lives here, apart from block_import, because it is the part that must be
provably emission-conserving and is worth reading on its own:

    carve    = floor(TOTAL_subsidy * pct / 100)     <- the TOTAL, not the miner remainder
    paid     = sum of anchor outputs, each clamped so paid never exceeds carve
    residual = carve - paid                          (>= 0 by construction)
    miner    = miner_reward - carve                  (NOT - paid: that is the rule)

so paid + residual == carve exactly, and miner + carve == the pre-carve miner slice.
Nothing is minted, nothing is burned, and there is no floating point anywhere.

THE BASE MATTERS. The carve is 25% OF THE BLOCK, so it is measured against the
reconstructed pre-split subsidy (miner + treasury + aicf), not against the post-treasury
miner slice — 25% of 75% would be 18.75% of the block. With the treasury already taking
25%, the resulting split is exactly miner 50% / treasury 25% / inference 25%.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

Output = Tuple[bytes, int]


class ServiceCarveError(ValueError):
    """The carve arithmetic could not be completed safely."""


def carve_amount(total_subsidy: int, pct: int) -> int:
    """floor(total_subsidy * pct / 100). Integer only.

    `total_subsidy` is the PRE-SPLIT block subsidy (miner + treasury + aicf), because the
    carve is a share of the block rather than of what is left after the treasury split.
    """
    total = int(total_subsidy)
    p = int(pct)
    if total <= 0 or p <= 0:
        return 0
    if p >= 100:
        # A 100% carve would leave the producer nothing and is certainly a mistake;
        # refuse rather than silently zero a miner's income.
        raise ServiceCarveError(f"service carve pct must be < 100, got {p}")
    return (total * p) // 100


def split_carve(
    *,
    miner_reward: int,
    total_subsidy: int,
    pct: int,
    anchor_outputs: Optional[Sequence[Output]] = None,
    escrow_address: bytes,
) -> Tuple[int, List[Output], int]:
    """Return (new_miner_reward, service_outputs, carve_total).

    `anchor_outputs` are the already-capped, already-scaled settlement outputs for this
    block — whatever `scale_settlement_outputs` produced. They are trusted to be within
    the cap; this function only ensures they never exceed the carve, and routes whatever
    is left to escrow.

    An empty or absent anchor set is the ordinary case today and is NOT an error: the
    whole carve goes to escrow, and the miner still loses it. That is the point.
    """
    carve = carve_amount(total_subsidy, pct)
    if carve <= 0:
        return int(miner_reward), [], 0
    if carve > int(miner_reward):
        # Cannot happen with the shipped ratios (the carve is 25% of the block while the
        # miner holds 75% of it), and the proportions survive the end-of-emission clamp.
        # Asserted anyway: silently driving a miner's reward negative would be far worse
        # than halting, and this is the only arithmetic that could do it.
        raise ServiceCarveError(
            f"service carve {carve} exceeds the miner slice {int(miner_reward)}"
        )

    outputs: List[Output] = []
    paid = 0
    for addr, amount in (anchor_outputs or []):
        amt = int(amount)
        if amt <= 0:
            continue
        # Never let anchors draw more than the carve, however they were scaled. Without
        # this a mis-scaled anchor set could pay more than was withheld, minting coin.
        if paid + amt > carve:
            amt = carve - paid
        if amt <= 0:
            break
        outputs.append((bytes(addr), amt))
        paid += amt

    residual = carve - paid
    if residual > 0:
        outputs.append((bytes(escrow_address), residual))

    total = sum(a for _, a in outputs)
    if total != carve:
        # Unreachable by construction; asserted because a mismatch here would mint or
        # burn coin, and failing closed is the only acceptable response.
        raise ServiceCarveError(
            f"service carve does not conserve emission: outputs {total} != carve {carve}"
        )
    return int(miner_reward) - carve, outputs, carve
