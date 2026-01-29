import importlib
import inspect
import math
import random
from typing import Callable, Dict, Iterable, Optional, Tuple

import pytest

# =========================
# Local exact math (ground truth)
# =========================


def p_fail_exact_prod(N: int, bad: int, s: int) -> float:
    """
    Probability of sampling s items uniformly without replacement from a population of size N
    that contains `bad` adversarial items, and seeing ZERO bad items.
    Computed via product to avoid big binomials:
        ∏_{i=0}^{s-1} (N - bad - i) / (N - i)
    """
    if bad <= 0:
        return 1.0
    if s <= 0:
        return 1.0
    if s > N:
        s = N
    good = N - bad
    if s > good:
        return 0.0
    num = 1.0
    for i in range(s):
        num *= (good - i) / (N - i)
    return float(num)


def p_fail_with_replacement_upper(N: int, bad: int, s: int) -> float:
    """Loose upper bound (with-replacement independence)."""
    if bad <= 0:
        return 1.0
    if s <= 0:
        return 1.0
    return float(((N - bad) / N) ** s)


# =========================
# Module under test adapters
# =========================


def _mod():
    return importlib.import_module("da.sampling.probability")


def _has(name: str) -> bool:
    return hasattr(_mod(), name)


def _get(name: str):
    return getattr(_mod(), name)


def _try_call(fn: Callable, **kwargs):
    """
    Try to call `fn` with keyword names it accepts; if that fails, try a few positional orders.
    Expected logical args: N, bad, s or N, bad, target (when 'target' in kwargs).
    """
    sig = None
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        sig = None

    # Keyword attempt
    if sig is not None:
        params = sig.parameters
        kwmap: Dict[str, str] = {}
        for want, aliases in {
            "N": ("N", "n", "total", "population", "pop"),
            "bad": ("bad", "B", "corrupted", "adversarial", "faulty", "b"),
            "s": ("s", "samples", "k", "m"),
            "target": ("target", "p_target", "p", "p_fail", "epsilon", "max_p_fail"),
        }.items():
            for a in aliases:
                if a in params and want in kwargs:
                    kwmap[a] = kwargs[want]
                    break
        try:
            return fn(**kwmap)
        except TypeError:
            pass

    # Positional fallbacks (common permutations)
    vals = []
    if "target" in kwargs:
        N, bad, target = kwargs["N"], kwargs["bad"], kwargs["target"]
        candidates = [
            (N, bad, target),
            (target, N, bad),
            (N, target, bad),
            (bad, N, target),
        ]
    else:
        N, bad, s = kwargs["N"], kwargs["bad"], kwargs["s"]
        candidates = [
            (N, bad, s),
            (s, N, bad),
            (N, s, bad),
            (bad, N, s),
        ]
    for tup in candidates:
        try:
            return fn(*tup)
        except TypeError:
            continue
    # Give up
    return fn(**kwargs)  # let it raise for clarity


def _pf_exact_fn() -> Optional[Callable[[int, int, int], float]]:
    names = ("p_fail_exact", "p_fail_hypergeom", "p_fail_without_replacement")
    for nm in names:
        if _has(nm):
            fn = _get(nm)
            return lambda N, bad, s, _fn=fn: float(_try_call(_fn, N=N, bad=bad, s=s))
    return None


def _pf_bound_fn() -> Optional[Callable[[int, int, int], float]]:
    names = ("p_fail_upper_bound", "p_fail_bound", "p_fail_independent", "p_fail")
    for nm in names:
        if _has(nm):
            fn = _get(nm)
            return lambda N, bad, s, _fn=fn: float(_try_call(_fn, N=N, bad=bad, s=s))
    return None


def _samples_for_target_fn() -> Optional[Callable[[int, int, float], int]]:
    names = (
        "samples_for_target",
        "required_samples",
        "samples_needed_for_target",
        "sample_count_for_target",
    )
    for nm in names:
        if _has(nm):
            fn = _get(nm)
            return lambda N, bad, target, _fn=fn: int(
                _try_call(_fn, N=N, bad=bad, target=target)
            )
    return None


def _primary_pf() -> Callable[[int, int, int], float]:
    # Prefer a bound if present; otherwise exact; otherwise fallback to local exact.
    f = _pf_bound_fn()
    if f is not None:
        return f
    f = _pf_exact_fn()
    if f is not None:
        return f
    return lambda N, bad, s: p_fail_exact_prod(N, bad, s)


# =========================
# Test parameters
# =========================

CASES = [
    (8192, int(0.10 * 8192), 10),
    (8192, int(0.10 * 8192), 32),
    (8192, int(0.25 * 8192), 48),
    (16384, int(0.20 * 16384), 64),
    (16384, int(0.40 * 16384), 96),
]


# =========================
# Tests
# =========================


@pytest.mark.parametrize("N,bad,s", CASES)
def test_upper_bound_sandwich_vs_exact_and_with_replacement(N: int, bad: int, s: int):
    pf_exact_local = p_fail_exact_prod(N, bad, s)
    pf_upper_ind = p_fail_with_replacement_upper(N, bad, s)

    pf_bound = _pf_bound_fn()
    pf_exact_mod = _pf_exact_fn()

    if pf_bound is None and pf_exact_mod is None and not _has("p_fail"):
        pytest.skip("No sampling probability functions exposed; test cannot run")

    if pf_bound is None and _has("p_fail"):
        # Treat generic p_fail as the bound for the purposes of this test
        pf_bound = lambda N, bad, s: float(_try_call(_get("p_fail"), N=N, bad=bad, s=s))

    assert 0.0 <= pf_exact_local <= 1.0
    assert 0.0 <= pf_upper_ind <= 1.0 + 1e-15

    # If the module exposes an exact function, it should match our exact within tiny epsilon.
    if pf_exact_mod is not None:
        val = pf_exact_mod(N, bad, s)
        assert (
            abs(val - pf_exact_local) <= 1e-12
        ), "module exact probability must match hypergeometric product"

    # The (declared) bound must be >= exact and <= with-replacement upper bound.
    if pf_bound is not None:
        val = pf_bound(N, bad, s)
        assert 0.0 <= val <= 1.0
        assert val + 1e-12 >= pf_exact_local, "upper bound below exact probability"
        assert val <= pf_upper_ind + 1e-12, "upper bound exceeds with-replacement bound"


@pytest.mark.parametrize("N,bad", [(8192, int(0.2 * 8192)), (16384, int(0.3 * 16384))])
def test_monotone_in_samples(N: int, bad: int):
    pf = _primary_pf()
    seq = [pf(N, bad, s) for s in (0, 4, 8, 16, 32, 64)]
    # non-increasing as s grows
    for a, b in zip(seq, seq[1:]):
        assert (
            b <= a + 1e-12
        ), f"p_fail must be non-increasing in samples (got {a} -> {b})"


@pytest.mark.parametrize("N,s", [(8192, 32), (16384, 64)])
def test_monotone_in_bad_fraction(N: int, s: int):
    pf = _primary_pf()
    vals = [pf(N, int(frac * N), s) for frac in (0.05, 0.10, 0.20, 0.35)]
    # With more bad shares, probability of missing them all should go DOWN.
    for a, b in zip(vals, vals[1:]):
        assert (
            b <= a + 1e-12
        ), f"p_fail must decrease as adversarial fraction increases (got {a} -> {b})"


def _minimal_s_exact(N: int, bad: int, target: float) -> int:
    """Small helper to compute the minimal s such that exact p_fail <= target."""
    s = 0
    while True:
        if p_fail_exact_prod(N, bad, s) <= target:
            return s
        s += 1


@pytest.mark.parametrize(
    "N,bad,target",
    [
        (4096, int(0.20 * 4096), 1e-6),
        (8192, int(0.10 * 8192), 1e-9),
    ],
)
def test_samples_for_target_is_sound_and_close(N: int, bad: int, target: float):
    s_fn = _samples_for_target_fn()
    if s_fn is None:
        pytest.skip("samples_for_target function not exposed; skipping sizing test")
    pf = _primary_pf()
    s = s_fn(N, bad, target)
    assert isinstance(s, int) and s >= 0

    # Soundness: returned s should satisfy the module's own probability function.
    assert pf(N, bad, s) <= target * (1.0 + 1e-12)

    # Near-minimality: s-1 should not already be under target according to the same pf().
    if s > 0:
        assert pf(N, bad, s - 1) > target * (1.0 - 1e-12)

    # Additionally, compare to exact minimal s (allow a small slack if module uses an upper bound).
    s_exact = _minimal_s_exact(N, bad, target)
    assert s >= s_exact, "Bound-based s must be at least the exact minimal samples"
    assert (
        s <= s_exact + 8
    ), "Returned samples are unexpectedly loose vs exact requirement (slack 8)"
