"""
execution.gas.refund — gas refund tracker & finalization rules.

Animica keeps gas-refund logic separate from the GasMeter so networks can tune
refund policy (caps, floors, and categories) without touching core metering.

This module provides:

- RefundPolicy: policy knobs (cap ratio, optional min charge).
- RefundTracker: category-aware accumulator with safe u256 semantics.
- finalize_refund: compute refund_applied and charged from `used` and tracker.
- load_policy: optionally load policy overrides from spec/params.yaml (or JSON).

The default policy mirrors common practice (max 50% refund of used gas).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Tuple

# Optional YAML (graceful if unavailable)
try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

from execution.types.gas import U256_MAX, is_u256, saturating_add

# ------------------------------- Policy --------------------------------------


@dataclass(frozen=True)
class RefundPolicy:
    """
    Policy controlling refund finalization.

    Attributes
    ----------
    cap_ratio : float
        Max fraction of *used* gas that may be refunded (e.g., 0.5 == 50% cap).
    min_charge : int
        Optional floor on the final charge (in gas). If > 0, ensures that some
        gas is always charged even if refundable amounts are large. Default 0.
    """

    cap_ratio: float = 0.50
    min_charge: int = 0

    def validate(self) -> "RefundPolicy":
        cr = (
            0.0
            if self.cap_ratio < 0.0
            else (1.0 if self.cap_ratio > 1.0 else self.cap_ratio)
        )
        mc = int(self.min_charge)
        if mc < 0:
            raise ValueError("min_charge must be non-negative")
        if not is_u256(mc):
            raise OverflowError("min_charge exceeds u256")
        # Normalize to a canonical instance
        return RefundPolicy(cap_ratio=cr, min_charge=mc)


def load_policy(
    path: Optional[str | Path] = None,
    *,
    overrides: Optional[Mapping[str, object]] = None,
) -> RefundPolicy:
    """
    Build a RefundPolicy from defaults, optional file, and overrides.

    The params file may contain either:
      refund:
        cap_ratio: 0.5
        min_charge: 0
    or flat keys at the root.

    Unknown keys are ignored.
    """
    base = RefundPolicy()
    data: Dict[str, object] = {}

    if path is not None:
        p = Path(path)
        if p.exists():
            text = p.read_text(encoding="utf-8")
            if p.suffix.lower() in {".yaml", ".yml"} and yaml is not None:
                raw = yaml.safe_load(text)  # type: ignore
            else:
                try:
                    raw = json.loads(text)
                except Exception:
                    if yaml is None:
                        raise
                    raw = yaml.safe_load(text)  # type: ignore
            if not isinstance(raw, dict):
                raise ValueError(f"{p}: expected mapping at root")
            if "refund" in raw and isinstance(raw["refund"], dict):
                data.update(raw["refund"])  # type: ignore[arg-type]
            else:
                data.update(raw)  # type: ignore[arg-type]

    if overrides:
        data.update({str(k): v for k, v in overrides.items()})

    # Filter known keys
    known = {k: data[k] for k in ("cap_ratio", "min_charge") if k in data}
    merged = RefundPolicy(**{**base.__dict__, **known})  # type: ignore[arg-type]
    return merged.validate()


# ------------------------------- Tracker -------------------------------------


@dataclass
class RefundTracker:
    """
    Category-aware accumulator for refundable gas.

    Use `add("category", amount)` to record refundable units during execution.
    Categories are advisory (used for metrics/debug); only the summed amount
    affects settlement.

    All arithmetic is capped to u256.
    """

    _by_cat: Dict[str, int] = field(default_factory=dict)

    def add(self, category: str, amount: int) -> None:
        if amount < 0:
            raise ValueError("refund amount must be non-negative")
        prev = int(self._by_cat.get(category, 0))
        self._by_cat[category] = min(
            U256_MAX, saturating_add(prev, amount, cap=U256_MAX)
        )

    def extend(self, items: Iterable[Tuple[str, int]]) -> None:
        for cat, amt in items:
            self.add(cat, int(amt))

    def total(self) -> int:
        total = 0
        for amt in self._by_cat.values():
            total = saturating_add(total, int(amt), cap=U256_MAX)
        return total

    def breakdown(self) -> Dict[str, int]:
        return dict(self._by_cat)

    def clear(self) -> None:
        self._by_cat.clear()


# ------------------------------ Finalization ---------------------------------


def finalize_refund(
    used: int, tracker: RefundTracker, policy: Optional[RefundPolicy] = None
) -> tuple[int, int]:
    """
    Apply policy to compute `(refund_applied, charged)` given `used` and tracker.

    Parameters
    ----------
    used : int
        Gas used before refunds (must be non-negative u256).
    tracker : RefundTracker
        Accumulated refundable gas components.
    policy : RefundPolicy | None
        Policy to apply. Defaults to `RefundPolicy()` if None.

    Returns
    -------
    (refund_applied, charged)

    Notes
    -----
    - `refund_applied` is capped by both `used * cap_ratio` and u256.
    - `charged = max(used - refund_applied, policy.min_charge)`.
    """
    if used < 0 or not is_u256(used):
        raise ValueError("used must be a non-negative u256")

    pol = (policy or RefundPolicy()).validate()
    refundable = tracker.total()

    # Policy cap based on used * cap_ratio
    cap_by_used = int(used * pol.cap_ratio)
    if cap_by_used < 0:
        cap_by_used = 0
    if cap_by_used > U256_MAX:
        cap_by_used = U256_MAX

    refund_applied = min(refundable, cap_by_used, U256_MAX)
    charged = used - refund_applied
    if charged < pol.min_charge:
        charged = pol.min_charge
    return (refund_applied, charged)


# ------------------------------ Convenience ----------------------------------


def merge_policy_from_params(params_path: Optional[str | Path]) -> RefundPolicy:
    """
    Convenience helper mirroring other gas modules' loaders.
    """
    return load_policy(params_path)


__all__ = [
    "RefundPolicy",
    "RefundTracker",
    "load_policy",
    "finalize_refund",
    "merge_policy_from_params",
]
