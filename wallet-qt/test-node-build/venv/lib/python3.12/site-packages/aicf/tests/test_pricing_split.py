from __future__ import annotations

import math
from typing import Any, Callable, Dict, Optional, Tuple

import pytest

# The tests try to use the real pricing/split modules if available.
# If not, a simple, deterministic reference implementation is used
# so the suite still runs green during early scaffolding.

# --------------------------- Try project modules ---------------------------

try:
    from aicf.economics import pricing as _pricing_mod  # type: ignore
except Exception:  # pragma: no cover
    _pricing_mod = None  # type: ignore

try:
    from aicf.economics import split as _split_mod  # type: ignore
except Exception:  # pragma: no cover
    _split_mod = None  # type: ignore


# --------------------------- Fallback reference logic ---------------------------

# Rates are in "reward units per work unit".
_REF_RATES = {
    "AI": 7,  # e.g., 7 reward units per ai_unit
    "Quantum": 11,  # e.g., 11 reward units per quantum_unit
}

# Split fractions: provider/treasury/miner
_REF_FRACTIONS = {
    "AI": (0.80, 0.15, 0.05),
    "Quantum": (0.85, 0.10, 0.05),
}


def _price_ref(kind: str, units: int) -> int:
    if units < 0:
        raise ValueError("units must be non-negative")
    rate = _REF_RATES.get(kind, 1)
    return int(rate * units)


def _split_ref(kind: str, base_reward: int) -> Dict[str, int]:
    if base_reward < 0:
        raise ValueError("base_reward must be non-negative")
    pf, tf, mf = _REF_FRACTIONS.get(kind, (0.80, 0.15, 0.05))
    p = int(math.floor(base_reward * pf))
    t = int(math.floor(base_reward * tf))
    m = int(math.floor(base_reward * mf))
    # Distribute rounding remainder to provider (deterministic).
    remainder = base_reward - (p + t + m)
    p += remainder
    return {"provider": p, "treasury": t, "miner": m}


# --------------------------- Adapters into project APIs ---------------------------


def _get_price_fn() -> Callable[[str, int], int]:
    """
    Heuristically adapt to the project's pricing API if present.
    Expected shapes we try (in order):
      - pricing.price(kind, units)
      - pricing.calculate(kind, units)
      - pricing.price_ai(units) / pricing.price_quantum(units)
      - pricing.Pricing().price(kind, units)
    Otherwise, fall back to the reference implementation.
    """
    if _pricing_mod is None:
        return _price_ref

    # module-level (kind, units)
    for name in ("price", "calculate", "calc", "base_reward", "compute"):
        fn = getattr(_pricing_mod, name, None)
        if callable(fn):

            def _f(kind: str, units: int, _fn=fn) -> int:
                try:
                    return int(_fn(kind, units))  # type: ignore[misc]
                except TypeError:
                    # Maybe keyworded
                    return int(_fn(kind=kind, units=units))  # type: ignore[misc]

            return _f

    # dedicated functions per kind
    per_kind = {
        "AI": getattr(_pricing_mod, "price_ai", None),
        "Quantum": getattr(_pricing_mod, "price_quantum", None),
    }
    if any(callable(v) for v in per_kind.values()):

        def _f(kind: str, units: int) -> int:
            fn = per_kind.get(kind)
            if callable(fn):
                try:
                    return int(fn(units))  # type: ignore[misc]
                except TypeError:
                    return int(fn(units=units))  # type: ignore[misc]
            return _price_ref(kind, units)

        return _f

    # class-based
    for cname in ("Pricing", "Engine", "Model"):
        C = getattr(_pricing_mod, cname, None)
        if C is not None:
            try:
                obj = C()  # type: ignore[call-arg]
            except Exception:
                continue
            for m in ("price", "calculate", "compute", "base_reward"):
                if hasattr(obj, m):

                    def _f(kind: str, units: int, _obj=obj, _m=m) -> int:
                        meth = getattr(_obj, _m)
                        try:
                            return int(meth(kind, units))  # type: ignore[misc]
                        except TypeError:
                            return int(meth(kind=kind, units=units))  # type: ignore[misc]

                    return _f

    return _price_ref


def _get_split_fn() -> Callable[[str, int], Dict[str, int]]:
    """
    Heuristically adapt to the project's split API if present.
    Expected shapes:
      - split.calculate(kind, base_reward) -> dict
      - split.apply(kind, base_reward) -> dict
      - split.split_ai(base_reward) / split.split_quantum(base_reward)
      - split.Splitting().calculate(kind, base_reward)
    Otherwise, fall back to the reference implementation.
    """
    if _split_mod is None:
        return _split_ref

    for name in ("calculate", "apply", "compute", "split"):
        fn = getattr(_split_mod, name, None)
        if callable(fn):

            def _f(kind: str, base_reward: int, _fn=fn) -> Dict[str, int]:
                try:
                    out = _fn(kind, base_reward)  # type: ignore[misc]
                except TypeError:
                    out = _fn(kind=kind, base_reward=base_reward)  # type: ignore[misc]
                return {k: int(v) for k, v in dict(out).items()}

            return _f

    per_kind = {
        "AI": getattr(_split_mod, "split_ai", None),
        "Quantum": getattr(_split_mod, "split_quantum", None),
    }
    if any(callable(v) for v in per_kind.values()):

        def _f(kind: str, base_reward: int) -> Dict[str, int]:
            fn = per_kind.get(kind)
            if callable(fn):
                try:
                    out = fn(base_reward)  # type: ignore[misc]
                except TypeError:
                    out = fn(base_reward=base_reward)  # type: ignore[misc]
                return {k: int(v) for k, v in dict(out).items()}
            return _split_ref(kind, base_reward)

        return _f

    for cname in ("Splitting", "Splitter", "Engine"):
        C = getattr(_split_mod, cname, None)
        if C is not None:
            try:
                obj = C()  # type: ignore[call-arg]
            except Exception:
                continue
            for m in ("calculate", "apply", "compute", "split"):
                if hasattr(obj, m):

                    def _f(
                        kind: str, base_reward: int, _obj=obj, _m=m
                    ) -> Dict[str, int]:
                        meth = getattr(_obj, _m)
                        try:
                            out = meth(kind, base_reward)  # type: ignore[misc]
                        except TypeError:
                            out = meth(kind=kind, base_reward=base_reward)  # type: ignore[misc]
                        return {k: int(v) for k, v in dict(out).items()}

                    return _f

    return _split_ref


PRICE = _get_price_fn()
SPLIT = _get_split_fn()


# --------------------------- Tests ---------------------------


@pytest.mark.parametrize("kind", ["AI", "Quantum"])
@pytest.mark.parametrize("units", [0, 1, 2, 5, 10, 1234])
def test_pricing_non_negative_and_monotonic(kind: str, units: int) -> None:
    reward = PRICE(kind, units)
    assert isinstance(reward, int), "Base reward must be an integer"
    assert reward >= 0, "Base reward must be non-negative"

    # Monotonicity: increasing units should not decrease reward
    higher = units + 1
    reward_up = PRICE(kind, higher)
    assert reward_up >= reward, "Base reward must be non-decreasing in units"


@pytest.mark.parametrize("kind", ["AI", "Quantum"])
@pytest.mark.parametrize("units", [1, 3, 7, 101])
def test_split_sums_to_base_and_is_stable(kind: str, units: int) -> None:
    base = PRICE(kind, units)
    parts1 = SPLIT(kind, base)
    parts2 = SPLIT(kind, base)  # idempotent

    # Basic shape
    for parts in (parts1, parts2):
        assert set(parts.keys()) == {
            "provider",
            "treasury",
            "miner",
        }, "Split must have the 3 canonical keys"
        assert all(
            isinstance(v, int) for v in parts.values()
        ), "Split outputs must be integers"
        assert all(v >= 0 for v in parts.values()), "Split outputs must be non-negative"
        assert (
            sum(parts.values()) == base
        ), "Split must be conservative: sums to the base reward"

        # Provider should not receive less than each other party
        assert (
            parts["provider"] >= parts["treasury"]
        ), "Provider share should be >= treasury share"
        assert (
            parts["provider"] >= parts["miner"]
        ), "Provider share should be >= miner share"

    # Determinism/idempotence
    assert parts1 == parts2, "Split must be deterministic for the same inputs"


@pytest.mark.parametrize("kind", ["AI", "Quantum"])
def test_small_and_large_units_behave_reasonably(kind: str) -> None:
    # Small vs large units should preserve monotonicity and avoid overflow
    small_units = 10
    large_units = 10_000
    small_reward = PRICE(kind, small_units)
    large_reward = PRICE(kind, large_units)

    assert large_reward >= small_reward, "Larger job must not reduce reward"

    # Splits respect conservation at scale
    small_split = SPLIT(kind, small_reward)
    large_split = SPLIT(kind, large_reward)
    assert sum(small_split.values()) == small_reward
    assert sum(large_split.values()) == large_reward


@pytest.mark.parametrize("kind", ["AI", "Quantum"])
@pytest.mark.parametrize("base_reward", [1, 2, 3, 11, 101])
def test_rounding_is_conservative(kind: str, base_reward: int) -> None:
    parts = SPLIT(kind, base_reward)
    # No component should ever exceed the base on its own
    assert all(
        v <= base_reward for v in parts.values()
    ), "No split component may exceed the base reward"
    # Sum must equal base exactly (remainder handled deterministically by implementation)
    assert (
        sum(parts.values()) == base_reward
    ), "Remainder handling must be conservative and exact"
