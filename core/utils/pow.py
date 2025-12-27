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


def compact_bits_to_target(bits: int) -> int:
    """
    Decode Bitcoin-style compact difficulty bits into a 256-bit target integer.

    The compact format uses:
      - exponent: high 8 bits (base-256 exponent)
      - mantissa: low 23 bits (sign bit masked out)

    Negative (sign bit set) or zero mantissa values are treated as invalid and
    return 0 to fail validation.
    """
    if bits <= 0:
        return 0
    exponent = (bits >> 24) & 0xFF
    mantissa = bits & 0x007FFFFF
    if bits & 0x00800000:
        return 0
    if mantissa == 0:
        return 0
    if exponent <= 3:
        return mantissa >> (8 * (3 - exponent))
    return mantissa << (8 * (exponent - 3))
