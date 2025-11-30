# Animica • proofs.quantum_attest.benchmarks
# ------------------------------------------
# Reference scaling for quantum-jobs:
#   depth × width × shots  →  "quantum_units" (work units)
#
# Deterministic, stdlib-only. This module supplies:
#  • DeviceProfile: nominal gate times & error rates (per QPU class)
#  • UnitPricing: coefficients to convert counts/time → units (+quality shaping)
#  • gate counting heuristics for generic circuits (density_1q, density_2q)
#  • fidelity / quality estimation (gate errors + readout + decoherence)
#  • runtime estimate with parallel shots
#  • estimate_units(...) → units + rich breakdown
#  • a tiny ridge-regression calibrator to fit UnitPricing from measurements
#
# Notes:
#   - Parameters are illustrative defaults (not vendor claims).
#   - Everything here is monotone & deterministic — suitable as ψ-input
#     material for the PoIES scorer after attestation & trap checks.

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import (Dict, Iterable, List, Mapping, NamedTuple, Optional,
                    Sequence, Tuple)

# ---------- Device & pricing models ----------


@dataclass(frozen=True)
class DeviceProfile:
    # Error rates (per gate / per qubit):
    eps_1q: float  # 1-qubit gate error (stochastic)
    eps_2q: float  # 2-qubit gate error
    eps_readout: float  # per-qubit readout error

    # Characteristic times (seconds):
    t_1q: float  # 1-qubit gate duration
    t_2q: float  # 2-qubit gate duration
    t_readout: float  # readout duration (per shot, amortized)

    # Coherence (seconds):
    T2: float  # effective dephasing time (worst-case across qubits)

    # Parallelism:
    parallel_shots: int  # shots that can be pipelined/parallelized effectively

    # Metadata:
    qpu_class: str  # e.g., "superconducting_nisq_v1", "ion_trap_v1", "neutral_atom_v1"


@dataclass(frozen=True)
class UnitPricing:
    # Linear coefficients converting counts/time into units:
    u_per_1q: float = 1.0e-6
    u_per_2q: float = 8.0e-6
    u_per_shot: float = 2.0e-6
    u_per_second: float = 1.0e-3

    # Quality shaping:
    quality_gamma: float = (
        1.0  # multiply units by quality^gamma (quality in [q_floor,1])
    )
    quality_floor: float = 0.05  # never below this multiplicative floor

    # Caps / bounds for safety:
    max_units_per_job: float = 1e6  # hard cap to prevent pathological inputs
    max_runtime_seconds: float = 1e6  # sanity guard


class GateCounts(NamedTuple):
    n_1q: int
    n_2q: int


@dataclass(frozen=True)
class BenchmarkInput:
    width: int
    depth: int
    shots: int
    density_1q: float = 1.0  # ~ #1q gates per qubit per depth layer
    density_2q: float = 0.5  # ~ fraction of (width-1) entangling pairs per layer


@dataclass(frozen=True)
class QualityBreakdown:
    p_gates: float  # product of (1-eps) across 1q & 2q gates
    p_readout: float  # (1-e_ro)^width
    p_coherence: float  # exp(-t_circuit / T2)
    p_shot: float  # per-shot correctness = p_gates * p_readout * p_coherence


@dataclass(frozen=True)
class RuntimeBreakdown:
    seconds_per_shot: float
    total_seconds: float
    parallel_batches: int


@dataclass(frozen=True)
class UnitBreakdown:
    raw_linear_units: float
    quality: float
    quality_gamma: float
    quality_floor: float
    adjusted_units: float


@dataclass(frozen=True)
class BenchmarkResult:
    profile: DeviceProfile
    inp: BenchmarkInput
    counts: GateCounts
    quality: QualityBreakdown
    runtime: RuntimeBreakdown
    units: UnitBreakdown

    def as_dict(self) -> Dict[str, object]:
        return {
            "profile": asdict(self.profile),
            "input": asdict(self.inp),
            "counts": self.counts._asdict(),
            "quality": asdict(self.quality),
            "runtime": asdict(self.runtime),
            "units": asdict(self.units),
        }


# ---------- Reference device profiles (illustrative) ----------


def reference_profiles() -> Mapping[str, DeviceProfile]:
    """
    Nominal reference profiles. Numbers are illustrative & conservative.

    superconducting_nisq_v1:
        eps_1q≈1e-4, eps_2q≈1.2e-3, readout≈2e-2
        t_1q≈2e-8 s (20 ns), t_2q≈2e-7 s (200 ns), readout≈4e-7 s (400 ns)
        T2≈5e-5 s (50 μs), parallel_shots≈64
    ion_trap_v1:
        eps_1q≈5e-5, eps_2q≈2e-3, readout≈5e-3
        t_1q≈1e-5 s (10 μs), t_2q≈2e-4 s (200 μs), readout≈4e-4 s (400 μs)
        T2≈1.0 s, parallel_shots≈8
    neutral_atom_v1:
        eps_1q≈3e-4, eps_2q≈3e-3, readout≈3e-2
        t_1q≈1e-6 s (1 μs), t_2q≈5e-6 s (5 μs), readout≈2e-4 s (200 μs)
        T2≈1e-4 s (100 μs), parallel_shots≈16
    """
    return {
        "superconducting_nisq_v1": DeviceProfile(
            eps_1q=1.0e-4,
            eps_2q=1.2e-3,
            eps_readout=2.0e-2,
            t_1q=2.0e-8,
            t_2q=2.0e-7,
            t_readout=4.0e-7,
            T2=5.0e-5,
            parallel_shots=64,
            qpu_class="superconducting_nisq_v1",
        ),
        "ion_trap_v1": DeviceProfile(
            eps_1q=5.0e-5,
            eps_2q=2.0e-3,
            eps_readout=5.0e-3,
            t_1q=1.0e-5,
            t_2q=2.0e-4,
            t_readout=4.0e-4,
            T2=1.0,
            parallel_shots=8,
            qpu_class="ion_trap_v1",
        ),
        "neutral_atom_v1": DeviceProfile(
            eps_1q=3.0e-4,
            eps_2q=3.0e-3,
            eps_readout=3.0e-2,
            t_1q=1.0e-6,
            t_2q=5.0e-6,
            t_readout=2.0e-4,
            T2=1.0e-4,
            parallel_shots=16,
            qpu_class="neutral_atom_v1",
        ),
    }


# ---------- Counting, quality, runtime ----------


def count_gates(inp: BenchmarkInput) -> GateCounts:
    """Heuristic gate counts for a generic circuit layerization."""
    if inp.width <= 0 or inp.depth <= 0 or inp.shots <= 0:
        raise ValueError("width, depth, shots must all be positive")

    n_1q = int(math.ceil(inp.width * inp.depth * max(0.0, float(inp.density_1q))))
    # 2q: approximate (width-1) possible pairwise interactions per layer, scaled by density.
    n_2q = int(
        math.ceil(max(0, inp.width - 1) * inp.depth * max(0.0, float(inp.density_2q)))
    )
    return GateCounts(n_1q=n_1q, n_2q=n_2q)


def quality_breakdown(
    counts: GateCounts, inp: BenchmarkInput, dev: DeviceProfile
) -> QualityBreakdown:
    # Independent-error model (upper bound on correctness)
    p_1q = (1.0 - dev.eps_1q) ** counts.n_1q
    p_2q = (1.0 - dev.eps_2q) ** counts.n_2q
    p_readout = (1.0 - dev.eps_readout) ** inp.width

    # Circuit time ≈ depth × max-gate-duration + readout
    t_layer = max(dev.t_1q, dev.t_2q)
    t_circ = inp.depth * t_layer + dev.t_readout
    p_coh = math.exp(-t_circ / max(1e-15, dev.T2))  # survival factor

    p_shot = max(0.0, min(1.0, p_1q * p_2q * p_readout * p_coh))
    return QualityBreakdown(
        p_gates=p_1q * p_2q, p_readout=p_readout, p_coherence=p_coh, p_shot=p_shot
    )


def runtime_breakdown(inp: BenchmarkInput, dev: DeviceProfile) -> RuntimeBreakdown:
    t_layer = max(dev.t_1q, dev.t_2q)
    sec_per_shot = inp.depth * t_layer + dev.t_readout
    batches = int(math.ceil(inp.shots / max(1, dev.parallel_shots)))
    total = sec_per_shot * batches
    total = min(
        total, dev.T2 * 0.0 + total
    )  # just to keep structure; no extra T2 constraint here
    return RuntimeBreakdown(
        seconds_per_shot=sec_per_shot, total_seconds=total, parallel_batches=batches
    )


# ---------- Units ----------


def _shape_quality(q: float, pricing: UnitPricing) -> float:
    q = max(pricing.quality_floor, min(1.0, q))
    if pricing.quality_gamma == 1.0:
        return q
    return q**pricing.quality_gamma


def estimate_units(
    inp: BenchmarkInput,
    dev: DeviceProfile,
    pricing: UnitPricing = UnitPricing(),
) -> BenchmarkResult:
    counts = count_gates(inp)
    qual = quality_breakdown(counts, inp, dev)
    run = runtime_breakdown(inp, dev)

    # Linear raw units:
    raw = (
        pricing.u_per_1q * counts.n_1q
        + pricing.u_per_2q * counts.n_2q
        + pricing.u_per_shot * inp.shots
        + pricing.u_per_second * min(run.total_seconds, pricing.max_runtime_seconds)
    )
    qshape = _shape_quality(qual.p_shot, pricing)
    adj = min(raw * qshape, pricing.max_units_per_job)

    units = UnitBreakdown(
        raw_linear_units=raw,
        quality=qual.p_shot,
        quality_gamma=pricing.quality_gamma,
        quality_floor=pricing.quality_floor,
        adjusted_units=adj,
    )

    return BenchmarkResult(
        profile=dev, inp=inp, counts=counts, quality=qual, runtime=run, units=units
    )


# ---------- Convenience presets ----------


def estimate_with_profile(
    width: int,
    depth: int,
    shots: int,
    profile_key: str = "superconducting_nisq_v1",
    density_1q: float = 1.0,
    density_2q: float = 0.5,
    pricing: UnitPricing = UnitPricing(),
) -> BenchmarkResult:
    dev = reference_profiles().get(profile_key)
    if dev is None:
        raise KeyError(f"unknown profile: {profile_key}")
    inp = BenchmarkInput(
        width=width,
        depth=depth,
        shots=shots,
        density_1q=density_1q,
        density_2q=density_2q,
    )
    return estimate_units(inp, dev, pricing)


# ---------- Tiny ridge-regression calibrator (optional) ----------


@dataclass(frozen=True)
class CalibSample:
    # Minimal sufficient statistics for a job:
    width: int
    depth: int
    shots: int
    measured_seconds: float  # observed wall-clock (or vendor-reported)
    measured_units: float  # desired target units for this job (e.g., payouts baseline)


def _gate_counts_from_scalar(w: int, d: int) -> GateCounts:
    return count_gates(BenchmarkInput(width=w, depth=d, shots=1))


def fit_pricing_from_samples(
    samples: Sequence[CalibSample],
    initial: UnitPricing = UnitPricing(),
    ridge_lambda: float = 1e-9,
) -> UnitPricing:
    """
    Fit (u_per_1q, u_per_2q, u_per_shot, u_per_second) by ridge regression on:
        y ≈ u1*n1 + u2*n2 + us*shots + ut*seconds
    Quality shaping is not fitted here (keep policy-driven).
    """
    if not samples:
        return initial

    # Build normal equations X^T X β = X^T y
    # β = [u1, u2, us, ut]
    XtX = [[0.0] * 4 for _ in range(4)]
    Xty = [0.0] * 4

    for s in samples:
        counts = _gate_counts_from_scalar(s.width, s.depth)
        x = [
            float(counts.n_1q),
            float(counts.n_2q),
            float(s.shots),
            float(max(0.0, s.measured_seconds)),
        ]
        y = float(max(0.0, s.measured_units))
        # XtX += x x^T ; Xty += x*y
        for i in range(4):
            Xty[i] += x[i] * y
            for j in range(4):
                XtX[i][j] += x[i] * x[j]

    # Ridge
    for i in range(4):
        XtX[i][i] += ridge_lambda

    beta = _solve_4x4(XtX, Xty)

    return UnitPricing(
        u_per_1q=max(0.0, beta[0]),
        u_per_2q=max(0.0, beta[1]),
        u_per_shot=max(0.0, beta[2]),
        u_per_second=max(0.0, beta[3]),
        quality_gamma=initial.quality_gamma,
        quality_floor=initial.quality_floor,
        max_units_per_job=initial.max_units_per_job,
        max_runtime_seconds=initial.max_runtime_seconds,
    )


def _solve_4x4(A: List[List[float]], b: List[float]) -> List[float]:
    """Gauss-Jordan elimination for a 4x4 system (stable enough for our scales)."""
    # Augment
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    n = 4
    # Pivot each column
    for col in range(n):
        # Find pivot
        piv = col
        piv_val = abs(M[piv][col])
        for r in range(col + 1, n):
            v = abs(M[r][col])
            if v > piv_val:
                piv, piv_val = r, v
        if piv_val <= 1e-18:
            # Singular — return zeros
            return [0.0, 0.0, 0.0, 0.0]
        # Swap
        if piv != col:
            M[col], M[piv] = M[piv], M[col]
        # Normalize pivot row
        pv = M[col][col]
        inv = 1.0 / pv
        for c in range(col, n + 1):
            M[col][c] *= inv
        # Eliminate other rows
        for r in range(n):
            if r == col:
                continue
            factor = M[r][col]
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                M[r][c] -= factor * M[col][c]
    # Extract solution
    return [M[i][n] for i in range(n)]


# ---------- Pretty summary ----------


def format_result(res: BenchmarkResult) -> str:
    c = res.counts
    q = res.quality
    r = res.runtime
    u = res.units
    return (
        f"[Animica QPU Bench]\n"
        f"  profile     : {res.profile.qpu_class}\n"
        f"  input       : width={res.inp.width} depth={res.inp.depth} shots={res.inp.shots}\n"
        f"  gate counts : 1q={c.n_1q:,}  2q={c.n_2q:,}\n"
        f"  quality     : p_gates={q.p_gates:.6f}  p_ro={q.p_readout:.6f}  p_coh={q.p_coherence:.6f}  p_shot={q.p_shot:.6f}\n"
        f"  runtime     : per-shot={r.seconds_per_shot:.6e}s  batches={r.parallel_batches}  total={r.total_seconds:.6f}s\n"
        f"  units       : raw={u.raw_linear_units:.6f}  quality={u.quality:.6f}  "
        f"adj={u.adjusted_units:.6f}  (γ={u.quality_gamma}, floor={u.quality_floor})\n"
    )


# ---------- __all__ ----------

__all__ = [
    "DeviceProfile",
    "UnitPricing",
    "GateCounts",
    "BenchmarkInput",
    "QualityBreakdown",
    "RuntimeBreakdown",
    "UnitBreakdown",
    "BenchmarkResult",
    "reference_profiles",
    "count_gates",
    "quality_breakdown",
    "runtime_breakdown",
    "estimate_units",
    "estimate_with_profile",
    "CalibSample",
    "fit_pricing_from_samples",
    "format_result",
]
