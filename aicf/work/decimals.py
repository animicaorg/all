"""
aicf.work.decimals
------------------

ANM decimal arithmetic for the work layer. Atomic units are nano-ANM
(9 decimals) tracked as Python ``int`` so we never lose precision to
float. All callers go through these helpers; nothing else in the layer
is allowed to ``float(amount)``.

Same shape as the TS prototype's ``src/server/work/decimals.ts`` — change
one, change both.
"""

from __future__ import annotations

import re
from typing import Sequence

_ATOMIC_PER_ANM = 10**9
_ANM_RE = re.compile(r"^(\d+)(?:\.(\d+))?$")


def anm_to_atomic(amount: str) -> int:
    """Convert a decimal ANM string into integer nano-ANM."""
    m = _ANM_RE.match(amount)
    if not m:
        raise ValueError(f"bad ANM amount: {amount!r}")
    whole = m.group(1) or "0"
    frac = (m.group(2) or "").ljust(9, "0")[:9]
    return int(whole) * _ATOMIC_PER_ANM + int(frac)


def atomic_to_anm(atomic: int) -> str:
    """Convert integer nano-ANM back into a canonical decimal string."""
    if atomic < 0:
        raise ValueError("negative ANM")
    whole, frac_int = divmod(atomic, _ATOMIC_PER_ANM)
    frac = str(frac_int).rjust(9, "0").rstrip("0")
    return f"{whole}.{frac}" if frac else str(whole)


def split_reward(total_anm: str, weights: Sequence[float]) -> list[str]:
    """Split a total ANM amount into weighted shares without losing dust.

    Weights need not sum to 1 — they're normalized internally. If all
    weights are zero, the total is split equally with any remainder
    landing on the first share. The remainder from integer division
    always goes to the first share so the post-split sum equals the
    pre-split total exactly.
    """
    if not weights:
        return []
    total = anm_to_atomic(total_anm)
    # Scale weights to integer parts-per-million so division is exact.
    ppm = [int(round(max(0.0, w) * 1_000_000)) for w in weights]
    sum_ppm = sum(ppm)
    if sum_ppm == 0:
        each = total // len(weights)
        return [
            atomic_to_anm(
                each + (total - each * len(weights)) if i == 0 else each
            )
            for i, _ in enumerate(weights)
        ]
    shares = [(total * p) // sum_ppm for p in ppm]
    assigned = sum(shares)
    remainder = total - assigned
    if remainder > 0:
        shares[0] += remainder  # dust goes to the first task
    return [atomic_to_anm(s) for s in shares]


def add_anm(a: str, b: str) -> str:
    return atomic_to_anm(anm_to_atomic(a) + anm_to_atomic(b))


__all__ = ["anm_to_atomic", "atomic_to_anm", "split_reward", "add_anm"]
