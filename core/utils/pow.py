from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN, localcontext

UINT256_MAX = (1 << 256) - 1
MICRO = 1_000_000


def micro_threshold_to_target256(t_micro: int) -> int:
    """
    Convert a µ-nats threshold into a 256-bit integer target T such that:
      accept(digest)  iff  int256(digest) <= T

    This uses Decimal math with a fixed precision to avoid float rounding
    inconsistencies across platforms.
    """
    if t_micro <= 0:
        return UINT256_MAX

    with localcontext() as ctx:
        ctx.prec = 80
        ctx.rounding = ROUND_HALF_EVEN
        t = Decimal(t_micro) / Decimal(MICRO)
        p = (-t).exp()
        if p <= 0:
            return 0
        if p >= 1:
            return UINT256_MAX
        return int(p * Decimal(UINT256_MAX))
