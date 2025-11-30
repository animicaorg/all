from __future__ import annotations

from aicf.queue.jobkind import JobKind

"""
Payout builders: derive Payout entries from proof claims.

Given a normalized proof-claim (produced when a provider submits a proof that
links to a job/task), compute the *base reward* from its work units and then
apply the configured split policy to produce a `Payout` record.

This module intentionally does not perform any state mutation or transfers.
It only turns finalized work-claims into deterministic accounting outputs.
"""


from dataclasses import asdict
from typing import Any, Dict, Iterable, Mapping, Tuple

from .split import DEFAULT_SPLIT_POLICY, SplitPolicy, split_for_kind

# Pricing adapter: we try a few canonical names so this works even if the pricing
# module exposes a slightly different function in early iterations.
try:  # pragma: no cover - exercised indirectly in higher-level tests
    from .pricing import \
        base_reward as _base_reward  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    _base_reward = None  # type: ignore[assignment]

try:  # pragma: no cover
    from .pricing import \
        reward_for_units as _reward_for_units  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    _reward_for_units = None  # type: ignore[assignment]

try:  # type: ignore[override]
    from ..types.payout import Payout  # type: ignore
except Exception:  # pragma: no cover
    # Minimal inline fallback if types are unavailable at import time.
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Payout:  # type: ignore[redefinition]
        kind: str
        total: int
        provider: int
        treasury: int
        miner: int


def _units_from_claim(claim: Any) -> int:
    """
    Heuristically extract work 'units' from a claim.
    Accepts multiple attribute spellings to be robust across refactors.
    """
    for attr in ("units", "work_units", "ai_units", "quantum_units"):
        if hasattr(claim, attr):
            val = getattr(claim, attr)
            if not isinstance(val, int) or val < 0:
                raise ValueError(f"claim.{attr} must be non-negative int, got {val!r}")
            return val
    raise AttributeError("claim lacks a 'units' (or compatible) integer field")


def _kind_from_claim(claim: Any) -> str:
    """
    Extract the JobKind ("AI" or "Quantum") from the claim.
    """
    for attr in ("kind", "job_kind", "proof_kind"):
        if hasattr(claim, attr):
            val = getattr(claim, attr)
            if not isinstance(val, str):
                raise ValueError(f"claim.{attr} must be a string, got {type(val)}")
            return val
    raise AttributeError("claim lacks a 'kind' string field (e.g., 'AI' or 'Quantum')")


def _task_id_from_claim(claim: Any) -> str:
    for attr in ("task_id", "job_id", "id"):
        if hasattr(claim, attr):
            val = getattr(claim, attr)
            if not isinstance(val, str):
                raise ValueError(f"claim.{attr} must be a string, got {type(val)}")
            return val
    raise AttributeError("claim lacks a task identifier (task_id/job_id/id)")


def _provider_id_from_claim(claim: Any) -> str:
    for attr in ("provider_id", "provider", "prover_id"):
        if hasattr(claim, attr):
            val = getattr(claim, attr)
            if not isinstance(val, str):
                raise ValueError(f"claim.{attr} must be a string, got {type(val)}")
            return val
    # Provider id is optional for payout math, but useful for attribution.
    return ""


def _compute_base_reward(kind: str, units: int) -> int:
    """
    Compute the base reward (before splits) for a given kind/units.
    Delegates to pricing if available.
    """
    if _base_reward is not None:  # type: ignore[truthy-function]
        return int(_base_reward(kind, units))  # type: ignore[misc]
    if _reward_for_units is not None:  # type: ignore[truthy-function]
        return int(_reward_for_units(kind, units))  # type: ignore[misc]
    # Conservative fallback: 1 unit -> 1 base token (devnet-friendly).
    return int(units)


def payout_from_claim(
    claim: Any,
    *,
    policy: SplitPolicy = DEFAULT_SPLIT_POLICY,
) -> Tuple[str, str, Payout]:
    """
    Build a Payout from a single proof-claim.

    Returns:
        (task_id, provider_id, Payout)

    Notes:
        - The *miner* share is the amount reserved for the block producer that
          included the proof. Attribution of that share is handled by the caller.
    """
    kind = _kind_from_claim(claim)
    units = _units_from_claim(claim)
    task_id = _task_id_from_claim(claim)
    provider_id = _provider_id_from_claim(claim)

    total = _compute_base_reward(kind, units)
    prov, tres, miner = split_for_kind(kind, total=total, policy=policy)

    payout = Payout(kind=kind, total=total, provider=prov, treasury=tres, miner=miner)
    return task_id, provider_id, payout


def payouts_from_claims(
    claims: Iterable[Any],
    *,
    policy: SplitPolicy = DEFAULT_SPLIT_POLICY,
) -> Dict[str, Mapping[str, Any]]:
    """
    Build payouts for a batch of claims.

    Returns:
        dict keyed by task_id with:
        {
          "provider_id": str,
          "payout": Payout,
          "payout_dict": Mapping[str,int],  # convenience materialization
        }
    """
    out: Dict[str, Mapping[str, Any]] = {}
    for claim in claims:
        task_id, provider_id, payout = payout_from_claim(claim, policy=policy)
        out[task_id] = {
            "provider_id": provider_id,
            "payout": payout,
            "payout_dict": asdict(payout),
        }
    return out


__all__ = [
    "payout_from_claim",
    "payouts_from_claims",
    "Payout",
]
