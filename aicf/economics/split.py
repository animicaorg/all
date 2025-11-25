from __future__ import annotations
"""
Split rules: divide a job's total reward among provider / treasury / miner.

This module implements deterministic, integer-safe split logic that is
configurable per job kind (AI / Quantum). Percentages are expressed in basis
points (bps, 1/100 of a percent), and integer rounding is handled by assigning
any remainder to a chosen recipient (default: provider).

Design goals
------------
- Deterministic: pure arithmetic; no floating point, no randomness.
- Integer-safe: all inputs/outputs are integers in the chain's base unit.
- Policy-driven per kind: AI and Quantum can use different splits.
- Defensive validation: splits must sum to 10_000 bps (100%).

Example
-------
>>> policy = DEFAULT_SPLIT_POLICY
>>> split_for_kind("AI", total=1_000_000, policy=policy)
(850000, 100000, 50000)
"""


from dataclasses import dataclass
from typing import Final,Tuple

from ..errors import AICFError

# Optional: if the typed Payout/RewardSplit exist, we can interop with them.
try:  # pragma: no cover - convenience import
    from ..types.payout import Payout, RewardSplit  # type: ignore
except Exception:  # pragma: no cover - keep this module standalone
    Payout = None  # type: ignore
    RewardSplit = None  # type: ignore

Amount = int  # token amount in smallest unit, integer


class SplitError(AICFError):
    """Raised when split policies are invalid or inputs are out of range."""


ResidualTarget = Literal["provider", "treasury", "miner"]


@dataclass(frozen=True)
class SplitRule:
    """
    Per-kind split rule in basis points (sum MUST equal 10_000).

    Attributes
    ----------
    provider_bps : int
        Basis points allocated to the off-chain provider.
    treasury_bps : int
        Basis points allocated to the protocol treasury.
    miner_bps : int
        Basis points allocated to the block producer / miner.
    residual_to : {'provider','treasury','miner'}
        Who receives any remainder from integer division.
    """

    provider_bps: int
    treasury_bps: int
    miner_bps: int
    residual_to: ResidualTarget = "provider"

    def __post_init__(self) -> None:
        for name, v in (
            ("provider_bps", self.provider_bps),
            ("treasury_bps", self.treasury_bps),
            ("miner_bps", self.miner_bps),
        ):
            if not isinstance(v, int):
                raise SplitError(f"{name} must be int basis points, got {type(v)!r}")
            if v < 0:
                raise SplitError(f"{name} must be non-negative, got {v}")
        total = self.provider_bps + self.treasury_bps + self.miner_bps
        if total != 10_000:
            raise SplitError(
                f"Split bps must sum to 10_000, got {total} "
                f"(provider={self.provider_bps}, treasury={self.treasury_bps}, miner={self.miner_bps})"
            )
        if self.residual_to not in ("provider", "treasury", "miner"):
            raise SplitError(f"Invalid residual target: {self.residual_to!r}")


@dataclass(frozen=True)
class SplitPolicy:
    """Per-kind split policy."""
    ai: SplitRule
    quantum: SplitRule


# Conservative, miner-friendly defaults for devnet:
# - AI:    85% provider / 10% treasury / 5% miner
# - Quantum: 80% provider / 15% treasury / 5% miner
DEFAULT_SPLIT_POLICY: Final[SplitPolicy] = SplitPolicy(
    ai=SplitRule(provider_bps=8_500, treasury_bps=1_000, miner_bps=500, residual_to="provider"),
    quantum=SplitRule(provider_bps=8_000, treasury_bps=1_500, miner_bps=500, residual_to="provider"),
)


def _apply_rule(total: Amount, rule: SplitRule) -> Tuple[Amount, Amount, Amount]:
    if not isinstance(total, int):
        raise SplitError(f"total must be int, got {type(total)!r}")
    if total < 0:
        raise SplitError(f"total must be non-negative, got {total}")

    # Base shares (integer division)
    prov = (total * rule.provider_bps) // 10_000
    tres = (total * rule.treasury_bps) // 10_000
    mine = (total * rule.miner_bps) // 10_000

    # Deterministic residual assignment
    remainder = total - (prov + tres + mine)
    if remainder:
        if rule.residual_to == "provider":
            prov += remainder
        elif rule.residual_to == "treasury":
            tres += remainder
        elif rule.residual_to == "miner":
            mine += remainder
        else:  # pragma: no cover - guarded in __post_init__
            raise SplitError(f"Unknown residual target: {rule.residual_to!r}")

    # Defensive sanity checks
    assert prov + tres + mine == total, "split invariant violated"
    assert min(prov, tres, mine) >= 0, "negative split share"

    return prov, tres, mine


def split_for_kind(
    kind: Literal["AI", "Quantum"],
    *,
    total: Amount,
    policy: SplitPolicy = DEFAULT_SPLIT_POLICY,
) -> Tuple[Amount, Amount, Amount]:
    """
    Compute (provider, treasury, miner) amounts for the given kind and total.

    Returns
    -------
    (provider_amount, treasury_amount, miner_amount) as integers.
    """
    if kind == "AI":
        return _apply_rule(total, policy.ai)
    if kind == "Quantum":
        return _apply_rule(total, policy.quantum)
    raise SplitError(f"Unsupported job kind: {kind!r}")


def payout_for_kind(
    kind: Literal["AI", "Quantum"],
    *,
    total: Amount,
    policy: SplitPolicy = DEFAULT_SPLIT_POLICY,
):
    """
    Return a typed Payout if available, otherwise a simple dict fallback.

    The Payout dataclass (if present) is expected to accept keyword arguments:
    provider, treasury, miner, total, kind. If its signature differs, this
    function will raise a SplitError.
    """
    prov, tres, mine = split_for_kind(kind, total=total, policy=policy)

    if Payout is None:
        # Fallback structure keeps callers unblocked in minimal environments.
        return {
            "kind": kind,
            "total": total,
            "provider": prov,
            "treasury": tres,
            "miner": mine,
        }

    try:
        return Payout(  # type: ignore[call-arg]
            kind=kind, total=total, provider=prov, treasury=tres, miner=mine
        )
    except TypeError as e:
        # Surface a clean, actionable error if the shape doesn't match.
        raise SplitError(
            "aicf.aitypes.payout.Payout signature mismatch: expected fields "
            "(kind, total, provider, treasury, miner)"
        ) from e


# Optional helper to build a SplitPolicy from external RewardSplit configs
def policy_from_reward_splits(
    ai_split,
    quantum_split,
    *,
    ai_residual_to: ResidualTarget = "provider",
    quantum_residual_to: ResidualTarget = "provider",
) -> SplitPolicy:
    """
    Convenience bridge when upstream code provides RewardSplit-like objects.

    The function will read attributes 'provider_bps', 'treasury_bps', 'miner_bps'.
    If those are not present, it will try 'provider', 'treasury', 'miner' assuming
    they are already integers in basis points.

    Raises SplitError on missing attributes or invalid totals.
    """
    def to_rule(obj, residual_to: ResidualTarget) -> SplitRule:
        # First try explicit *_bps attributes
        attrs = ("provider_bps", "treasury_bps", "miner_bps")
        if all(hasattr(obj, a) for a in attrs):
            return SplitRule(
                provider_bps=int(getattr(obj, "provider_bps")),
                treasury_bps=int(getattr(obj, "treasury_bps")),
                miner_bps=int(getattr(obj, "miner_bps")),
                residual_to=residual_to,
            )
        # Fallback to plain names interpreted as bps integers
        attrs = ("provider", "treasury", "miner")
        if all(hasattr(obj, a) for a in attrs):
            return SplitRule(
                provider_bps=int(getattr(obj, "provider")),
                treasury_bps=int(getattr(obj, "treasury")),
                miner_bps=int(getattr(obj, "miner")),
                residual_to=residual_to,
            )
        raise SplitError(
            "RewardSplit-like object must have either *_bps or plain provider/treasury/miner attributes"
        )

    return SplitPolicy(
        ai=to_rule(ai_split, ai_residual_to),
        quantum=to_rule(quantum_split, quantum_residual_to),
    )


__all__ = [
    "Amount",
    "SplitError",
    "SplitRule",
    "SplitPolicy",
    "DEFAULT_SPLIT_POLICY",
    "split_for_kind",
    "payout_for_kind",
    "policy_from_reward_splits",
]
