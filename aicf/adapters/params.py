from __future__ import annotations

"""
AICF params adapter
===================

Loads *chain params* relevant to AICF economics and split rules from the
canonical core params module (if available) and exposes a small, typed
object used by the AICF economics pipeline (pricing, split, settlement).

Search order
------------
1) Explicit dict passed to `load(...)`
2) Env override `AICF_PARAMS_JSON` (JSON object, quick for tests)
3) core.types.params module (e.g., `get_chain_params()` or `CHAIN_PARAMS`)
4) Built-in sane defaults for devnets

Shape expected (flexible):
{
  "modules": {
    "aicf": {
      "epoch_duration_blocks": 3600,
      "max_epoch_payout_cap": 10_000_000_000,
      "treasury_account": "anim1treasury....",
      "ai": {
        "base_per_unit": 1000,
        "split_bps": {"provider": 8500, "miner": 1000, "treasury": 500}
      },
      "quantum": {
        "base_per_unit": 5000,
        "split_bps": {"provider": 8800, "miner": 800, "treasury": 400}
      }
    }
  }
}

Notes
-----
- Splits are expressed in basis points (BPS), validating to 10_000 total.
- This module is intentionally dependency-light to avoid import cycles.
"""

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Mapping, Optional, Tuple, TypedDict


BPS_DENOM = 10_000


class SplitDict(TypedDict):
    provider: int
    miner: int
    treasury: int


@dataclass(frozen=True)
class Split:
    """Provider/Miner/Treasury split in basis points (sum must be 10_000)."""

    provider_bps: int
    miner_bps: int
    treasury_bps: int

    def validate(self) -> None:
        total = self.provider_bps + self.miner_bps + self.treasury_bps
        if total != BPS_DENOM:
            raise ValueError(f"split must sum to {BPS_DENOM}, got {total}")
        for name, v in (("provider", self.provider_bps), ("miner", self.miner_bps), ("treasury", self.treasury_bps)):
            if v < 0:
                raise ValueError(f"{name} bps must be >= 0")


@dataclass(frozen=True)
class EconKind:
    """Pricing + split for a single job kind (AI or Quantum)."""

    base_per_unit: int  # base reward per normalized unit (e.g., tokens per ai_unit)
    split: Split


@dataclass(frozen=True)
class AICFEconParams:
    """Resolved AICF economics parameters."""

    epoch_duration_blocks: int
    max_epoch_payout_cap: int  # Γ_fund cap per epoch
    treasury_account: str
    ai: EconKind
    quantum: EconKind

    # Convenience helpers
    def price_units(self, kind: str, units: int) -> int:
        if units < 0:
            raise ValueError("units must be >= 0")
        k = kind.lower()
        if k == "ai":
            return units * self.ai.base_per_unit
        if k == "quantum":
            return units * self.quantum.base_per_unit
        raise KeyError(f"unknown kind {kind!r}")

    def split_amount(self, kind: str, amount: int) -> Tuple[int, int, int]:
        """Return (provider, miner, treasury) amounts for `amount` at `kind`."""
        if amount < 0:
            raise ValueError("amount must be >= 0")
        s = self.ai.split if kind.lower() == "ai" else self.quantum.split
        # integer math with floor; any dust remains in treasury by convention
        provider = amount * s.provider_bps // BPS_DENOM
        miner = amount * s.miner_bps // BPS_DENOM
        # ensure sum <= amount; assign remainder to treasury
        treasury = amount - provider - miner
        return provider, miner, treasury


# ---- defaults ----------------------------------------------------------------

_DEFAULTS: Dict[str, Any] = {
    "epoch_duration_blocks": 3600,              # ~1-day at 24s blocks, tune per network
    "max_epoch_payout_cap": 10_000_000_000,     # example cap
    "treasury_account": "anim1treasury0000000000000000000000000",
    "ai": {
        "base_per_unit": 1_000,
        "split_bps": {"provider": 8_500, "miner": 1_000, "treasury": 500},
    },
    "quantum": {
        "base_per_unit": 5_000,
        "split_bps": {"provider": 8_800, "miner": 800, "treasury": 400},
    },
}


# ---- loaders -----------------------------------------------------------------


def _load_env_json() -> Optional[Mapping[str, Any]]:
    raw = os.environ.get("AICF_PARAMS_JSON")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError("AICF_PARAMS_JSON is not valid JSON") from e


def _load_core_chain_params() -> Optional[Mapping[str, Any]]:
    """
    Try importing core.types.params to fetch chain params.

    Supports either:
      - get_chain_params() -> Mapping
      - CHAIN_PARAMS: Mapping
    """
    try:
        from core.types import params as core_params  # type: ignore
    except Exception:
        return None

    # get function
    if hasattr(core_params, "get_chain_params"):
        try:
            cp = core_params.get_chain_params()  # type: ignore[attr-defined]
            if isinstance(cp, Mapping):
                return cp
        except Exception:
            pass

    # fallback attribute
    if hasattr(core_params, "CHAIN_PARAMS"):
        cp = getattr(core_params, "CHAIN_PARAMS")  # type: ignore[attr-defined]
        if isinstance(cp, Mapping):
            return cp

    return None


def _project_to_aicf_section(root: Mapping[str, Any]) -> Mapping[str, Any]:
    """
    Find the AICF section in a flexible way, tolerating a few shapes.
    """
    if not isinstance(root, Mapping):
        return {}
    # common nesting: modules -> aicf
    modules = root.get("modules") or root.get("module_params") or {}
    if isinstance(modules, Mapping) and "aicf" in modules:
        sec = modules["aicf"]
        if isinstance(sec, Mapping):
            return sec
    # flat
    if "aicf" in root and isinstance(root["aicf"], Mapping):
        return root["aicf"]
    # maybe already the section
    return root


def _parse_split(obj: Mapping[str, Any]) -> Split:
    # accept "split_bps" or "split"
    raw = obj.get("split_bps", obj.get("split", {}))
    if not isinstance(raw, Mapping):
        raise ValueError("split must be a mapping with provider/miner/treasury")
    try:
        provider = int(raw.get("provider"))
        miner = int(raw.get("miner"))
        treasury = int(raw.get("treasury"))
    except Exception as e:
        raise ValueError("split fields must be integers") from e
    s = Split(provider_bps=provider, miner_bps=miner, treasury_bps=treasury)
    s.validate()
    return s


def _parse_kind(name: str, obj: Mapping[str, Any]) -> EconKind:
    try:
        bpu = int(obj["base_per_unit"])
    except Exception as e:
        raise ValueError(f"{name}.base_per_unit missing or invalid") from e
    split = _parse_split(obj)
    return EconKind(base_per_unit=bpu, split=split)


def _coalesce(source: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    if not source:
        return _DEFAULTS
    # Shallow-merge defaults with provided source for robustness.
    def deep_merge(d: Dict[str, Any], u: Mapping[str, Any]) -> Dict[str, Any]:
        out = dict(d)
        for k, v in u.items():
            if isinstance(v, Mapping) and isinstance(out.get(k), Mapping):
                out[k] = deep_merge(out[k], v)  # type: ignore[index]
            else:
                out[k] = v
        return out
    return deep_merge(_DEFAULTS, source)  # type: ignore[arg-type]


def _build_params(section: Mapping[str, Any]) -> AICFEconParams:
    epoch_blocks = int(section.get("epoch_duration_blocks", _DEFAULTS["epoch_duration_blocks"]))
    cap = int(section.get("max_epoch_payout_cap", _DEFAULTS["max_epoch_payout_cap"]))
    treasury = str(section.get("treasury_account", _DEFAULTS["treasury_account"]))
    ai = _parse_kind("ai", section.get("ai", _DEFAULTS["ai"]))  # type: ignore[arg-type]
    q = _parse_kind("quantum", section.get("quantum", _DEFAULTS["quantum"]))  # type: ignore[arg-type]
    return AICFEconParams(
        epoch_duration_blocks=epoch_blocks,
        max_epoch_payout_cap=cap,
        treasury_account=treasury,
        ai=ai,
        quantum=q,
    )


def load(source: Optional[Mapping[str, Any]] = None) -> AICFEconParams:
    """
    Load AICF parameters.

    - If `source` is provided, it is used (merged over defaults).
    - Else environment JSON override is considered.
    - Else attempts to import core chain params.
    - Else falls back to built-in defaults.
    """
    if source is not None:
        merged = _coalesce(source)
        return _build_params(_project_to_aicf_section(merged))

    env_obj = _load_env_json()
    if env_obj:
        merged = _coalesce(env_obj)
        return _build_params(_project_to_aicf_section(merged))

    core = _load_core_chain_params()
    if core:
        merged = _coalesce(core)
        return _build_params(_project_to_aicf_section(merged))

    # fallback
    return _build_params(_DEFAULTS)


@lru_cache(maxsize=1)
def current() -> AICFEconParams:
    """
    Cached resolver using the default search order.
    """
    return load()


__all__ = [
    "AICFEconParams",
    "EconKind",
    "Split",
    "BPS_DENOM",
    "load",
    "current",
]
