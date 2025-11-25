# Animica • proofs.quantum_attest.traps
# -------------------------------------
# Trap-circuit sampling & verification math:
#  - hit ratio (observed correctness on trap circuits)
#  - confidence intervals (Wilson, Clopper–Pearson exact, Hoeffding)
#  - one-sided p-value vs target ratio
#  - minimal sample sizing helpers
#
# This module is deterministic and stdlib-only.

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple


# ---------- Data structures ----------

@dataclass(frozen=True)
class TrapStats:
    n: int                   # number of traps evaluated
    k: int                   # number of correct trap outcomes (matches)
    p_hat: float             # empirical success rate k/n


@dataclass(frozen=True)
class ConfidenceInterval:
    lower: float
    upper: float
    method: str
    alpha: float


@dataclass(frozen=True)
class TrapVerificationResult:
    stats: TrapStats
    ci: ConfidenceInterval
    target_ratio: float
    passed: bool            # True if (lower bound) >= target_ratio
    p_value_one_sided: float  # P_{H0:p=target}(X >= k)  (binomial right tail)
    notes: str


# ---------- Helpers ----------

def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _z_from_alpha(alpha: float) -> float:
    """
    Two-sided α -> z_{1-α/2} using Acklam rational approximation for Φ^{-1}.
    Good to ~1e-9. (No external deps.)
    """
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0,1)")
    p = 1.0 - alpha / 2.0
    # Acklam constants
    a = [-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
            ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    elif p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
             ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    else:
        q = p - 0.5
        r = q*q
        x = (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5])*q / \
            (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)
    return x


def _comb(n: int, k: int) -> float:
    # math.comb returns int; cast to float for subsequent ops.
    return float(math.comb(n, k))


def _binom_pmf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 1.0 if k == 0 else 0.0
    if p >= 1.0:
        return 1.0 if k == n else 0.0
    return _comb(n, k) * (p**k) * ((1.0 - p)**(n - k))


def _binom_cdf(k: int, n: int, p: float) -> float:
    # Sum_{i=0..k} pmf(i;n,p)
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    s = 0.0
    for i in range(0, k + 1):
        s += _binom_pmf(i, n, p)
    return min(1.0, s)


def _binom_sf(k: int, n: int, p: float) -> float:
    # Survival function P[X >= k] = 1 - P[X <= k-1]
    return 1.0 - _binom_cdf(k - 1, n, p)


# ---------- Confidence intervals ----------

def wilson_interval(k: int, n: int, alpha: float) -> ConfidenceInterval:
    """
    Wilson score interval (two-sided) for binomial proportion.
    """
    if n <= 0:
        raise ValueError("n must be > 0")
    p = k / n
    z = _z_from_alpha(alpha)
    z2 = z*z
    denom = 1.0 + z2 / n
    center = (p + z2/(2*n)) / denom
    half = z * math.sqrt((p*(1-p)/n) + (z2/(4*n*n))) / denom
    lo, hi = _clamp01(center - half), _clamp01(center + half)
    return ConfidenceInterval(lower=lo, upper=hi, method="wilson", alpha=alpha)


def hoeffding_interval(k: int, n: int, alpha: float) -> ConfidenceInterval:
    """
    Hoeffding's inequality → two-sided confidence band.
    P(|p_hat - p| >= eps) <= 2 exp(-2 n eps^2)  => eps = sqrt( (1/(2n)) ln(2/alpha) )
    """
    if n <= 0:
        raise ValueError("n must be > 0")
    p_hat = k / n
    eps = math.sqrt(0.5 * math.log(2.0/alpha) / n)
    return ConfidenceInterval(lower=_clamp01(p_hat - eps),
                              upper=_clamp01(p_hat + eps),
                              method="hoeffding",
                              alpha=alpha)


def _invert_cdf_for_lower(k: int, n: int, alpha: float) -> float:
    # Find p_lower s.t. P[X <= k-1 | p_lower] = alpha/2  (monotone in p)
    if k <= 0:
        return 0.0
    lo, hi = 0.0, (k / n)  # lower must be ≤ p_hat
    target = alpha / 2.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        cdf = _binom_cdf(k - 1, n, mid)
        if cdf > target:
            hi = mid
        else:
            lo = mid
    return lo


def _invert_cdf_for_upper(k: int, n: int, alpha: float) -> float:
    # Find p_upper s.t. P[X >= k | p_upper] = alpha/2
    if k >= n:
        return 1.0
    lo, hi = (k / n), 1.0  # upper must be ≥ p_hat
    target = alpha / 2.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        sf = _binom_sf(k, n, mid)
        if sf > target:
            lo = mid
        else:
            hi = mid
    return hi


def clopper_pearson_interval(k: int, n: int, alpha: float) -> ConfidenceInterval:
    """
    Exact (Clopper–Pearson) confidence interval by inverting binomial cdf.
    No SciPy needed; uses monotone binary search over p with explicit binomial sums.
    """
    if n <= 0:
        raise ValueError("n must be > 0")
    lo = _invert_cdf_for_lower(k, n, alpha)
    hi = _invert_cdf_for_upper(k, n, alpha)
    return ConfidenceInterval(lower=_clamp01(lo), upper=_clamp01(hi),
                              method="clopper-pearson", alpha=alpha)


# ---------- Trap stats & verification ----------

def count_trap_hits(observed: Sequence[int | bool], expected: Sequence[int | bool]) -> TrapStats:
    if len(observed) != len(expected):
        raise ValueError("observed and expected must have equal length")
    n = len(observed)
    if n == 0:
        raise ValueError("no traps provided")
    k = 0
    for a, b in zip(observed, expected):
        a1 = 1 if bool(a) else 0
        b1 = 1 if bool(b) else 0
        if a1 == b1:
            k += 1
    return TrapStats(n=n, k=k, p_hat=k/n)


def one_sided_p_value(k: int, n: int, target_ratio: float) -> float:
    """
    Binomial right-tail p-value for H0: p = target_ratio.
    p = P[X >= k | n, p0]
    """
    p0 = _clamp01(target_ratio)
    return _binom_sf(k, n, p0)


def choose_interval(method: str, k: int, n: int, alpha: float) -> ConfidenceInterval:
    m = method.lower()
    if m in ("wilson", "w"):
        return wilson_interval(k, n, alpha)
    if m in ("clopper-pearson", "cp", "exact"):
        return clopper_pearson_interval(k, n, alpha)
    if m in ("hoeffding", "h"):
        return hoeffding_interval(k, n, alpha)
    raise ValueError(f"unknown CI method: {method}")


def verify_traps(
    observed: Sequence[int | bool],
    expected: Sequence[int | bool],
    target_ratio: float,
    alpha: float = 0.01,
    method: str = "wilson",
) -> TrapVerificationResult:
    """
    Compute trap hit statistics, confidence interval, and decision.

    Decision rule (conservative):
       PASS iff CI.lower >= target_ratio at confidence 1-α.

    Returns TrapVerificationResult with one-sided p-value against H0:p=target_ratio for
    additional reporting (not part of the pass rule).
    """
    stats = count_trap_hits(observed, expected)
    ci = choose_interval(method, stats.k, stats.n, alpha)
    passed = ci.lower >= target_ratio - 1e-15  # numeric guard
    pval = one_sided_p_value(stats.k, stats.n, target_ratio)
    notes = (
        f"method={ci.method}, alpha={alpha}, "
        f"ci=[{ci.lower:.6f},{ci.upper:.6f}], "
        f"p_hat={stats.p_hat:.6f}, k={stats.k}, n={stats.n}"
    )
    return TrapVerificationResult(
        stats=stats,
        ci=ci,
        target_ratio=target_ratio,
        passed=passed,
        p_value_one_sided=pval,
        notes=notes,
    )


# ---------- Sample sizing ----------

def min_samples_for_margin(
    target_ratio: float,
    margin: float,
    alpha: float = 0.01,
    method: str = "wilson",
) -> int:
    """
    Compute a conservative n so that (approximately) the two-sided CI half-width ≤ margin
    around p≈target_ratio. For Wilson, the common closed form is:
       n ≥ z^2 * p*(1-p) / margin^2
    We clamp p*(1-p) with p=0.5 when target near extremes to avoid tiny n.
    """
    if not (0 < margin < 0.5):
        raise ValueError("margin should be in (0, 0.5)")
    z = _z_from_alpha(alpha)
    p = _clamp01(target_ratio)
    # Conservative at extremes: use max(p*(1-p), 0.25 * bias_guard)
    p_var = max(p * (1 - p), 0.25 if method.lower() in ("wilson", "cp", "exact") else p * (1 - p))
    n = math.ceil((z * z) * p_var / (margin * margin))
    return int(n)


def min_samples_hoeffding(
    margin: float,
    alpha: float = 0.01,
) -> int:
    """
    From Hoeffding: 2*exp(-2 n eps^2) ≤ α ⇒ n ≥ (1/(2 eps^2)) ln(2/α)
    (distribution-free; independent of target_ratio)
    """
    if not (0 < margin < 0.5):
        raise ValueError("margin should be in (0, 0.5)")
    n = math.ceil((math.log(2.0 / alpha)) / (2.0 * margin * margin))
    return int(n)


# ---------- Sequential (optional) ----------

@dataclass(frozen=True)
class SPRTDecision:
    decided: bool
    accept: bool   # accept H1 (p ≥ p1) if decided and True; else reject (accept H0)
    log_likelihood_ratio: float
    A: float
    B: float


def sprt_one_sided(
    k: int,
    n: int,
    p0: float,
    p1: float,
    alpha: float = 0.01,
    beta: float = 0.01,
) -> SPRTDecision:
    """
    Sequential Probability Ratio Test (SPRT), H0: p = p0 vs H1: p = p1 (p1 > p0).
    Decision thresholds:
       A = (1 - beta) / alpha
       B = beta / (1 - alpha)
    LLR after n trials, k successes:
       L = (p1/p0)^k * ((1-p1)/(1-p0))^{n-k}
       log LLR = k log(p1/p0) + (n-k) log((1-p1)/(1-p0))
    Decide:
       - if L >= A ⇒ accept H1 (good provider)
       - if L <= B ⇒ accept H0 (insufficient)
       - else continue sampling
    """
    if not (0 < p0 < p1 < 1):
        raise ValueError("require 0 < p0 < p1 < 1")
    A = (1.0 - beta) / alpha
    B = beta / (1.0 - alpha)
    llr = k * math.log(p1 / p0) + (n - k) * math.log((1.0 - p1) / (1.0 - p0))
    L = math.exp(llr)
    if L >= A:
        return SPRTDecision(True, True, llr, A, B)
    if L <= B:
        return SPRTDecision(True, False, llr, A, B)
    return SPRTDecision(False, False, llr, A, B)


# ---------- Convenience: boolean adapters ----------

def verify_traps_bool(
    observed_ok: Sequence[bool],
    target_ratio: float,
    alpha: float = 0.01,
    method: str = "wilson",
) -> TrapVerificationResult:
    """
    If the caller already computed "trap ok?" booleans, use this.
    """
    obs = [1 if b else 0 for b in observed_ok]
    exp = [1] * len(obs)  # expected "ok" for each trap
    return verify_traps(obs, exp, target_ratio, alpha=alpha, method=method)


# ---------- __all__ ----------

__all__ = [
    "TrapStats",
    "ConfidenceInterval",
    "TrapVerificationResult",
    "count_trap_hits",
    "wilson_interval",
    "clopper_pearson_interval",
    "hoeffding_interval",
    "verify_traps",
    "verify_traps_bool",
    "one_sided_p_value",
    "min_samples_for_margin",
    "min_samples_hoeffding",
    "SPRTDecision",
    "sprt_one_sided",
]
