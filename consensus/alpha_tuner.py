"""
Alpha Tuner (fairness weights across proof types)
=================================================

This module maintains slow-moving multiplicative weights α_t[type] that
rebalance acceptance pressure across proof types (HASH/AI/QUANTUM/STORAGE/VDF)
toward a target mix. It is *deterministic* and uses integer math only (no
floating point), so all honest nodes converge to the same α given the same
observations.

Design goals
------------
- Preserve determinism (pure integer arithmetic, stable rounding).
- React slowly (EWMA over blocks + bounded per-update change) to avoid oscillations.
- Prevent runaway when a type is rare (ε floor + step clamps).
- Keep overall "difficulty" budget neutral by normalizing α so the target-weighted
  average stays at 1.0 in fixed-point.

Terminology
-----------
Let M types indexed by ProofType. For each block we observe per-type "micro-units"
(e.g., Hash difficulty micro-shares, AI/Quantum units after policy scaling).
We feed them into an integer EWMA:

    ema_i <- ema_i - (ema_i >> S) + (units_i << S)

where S = SHIFT controls the smoothing window (~2^S blocks).

Periodically (every COOLDOWN blocks) we compute observed share:

    share_i_ppm = max(ε_ppm, round(1e6 * ema_i / sum_j ema_j))

Target shares are given in ppm (parts-per-million) and sum to 1e6.

We adjust α by a capped multiplicative rule:

    ratio_ppm   = clamp( target_i_ppm * 1e6 / share_i_ppm, 1/STEP_DOWN .. STEP_UP )
    alpha'_i    = clamp_global( alpha_i * ratio_ppm / 1e6, MIN_ALPHA .. MAX_ALPHA )

Finally, to keep aggregate difficulty neutral, we normalize α so that the
target-weighted average equals SCALE (1e9).

Persistence
-----------
Only two pieces of state are required:
- current α_i (scaled fixed-point)
- ema_i (scaled by 1<<S)
These can be checkpointed with the head and replayed deterministically.

Integration
-----------
- consensus.policy can provide target_mix_ppm.
- consensus.scorer reads α via `get_alpha(type)` to scale its ψ inputs.
- consensus.validator can call `record_block()` with per-type units when sealing,
  and `maybe_update(height)` on block boundaries to roll α forward.

All math is integer and deterministic.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Tuple

try:
    from consensus.types import ProofType  # canonical enum
except Exception:  # pragma: no cover - allow standalone tests
    from enum import IntEnum

    class ProofType(IntEnum):
        HASH = 0
        AI = 1
        QUANTUM = 2
        STORAGE = 3
        VDF = 4


# ----------------------------- Fixed-point scales ----------------------------

SCALE = 10**9  # α fixed-point scale: 1.0 == 1_000_000_000
PPM = 1_000_000  # shares in parts-per-million
DEFAULT_TYPES: Tuple[ProofType, ...] = (
    ProofType.HASH,
    ProofType.AI,
    ProofType.QUANTUM,
    ProofType.STORAGE,
    ProofType.VDF,
)

# ------------------------------- Configuration ------------------------------


@dataclass(frozen=True)
class AlphaTunerConfig:
    # Smoothing: EMA shift (window ~ 2^SHIFT blocks)
    SHIFT: int = 8  # ~256-block effective memory

    # Update cadence: only attempt an update every COOLDOWN blocks
    COOLDOWN: int = 32

    # Target mix in ppm (must sum to 1_000_000). Missing types get 0.
    target_mix_ppm: Mapping[ProofType, int] = field(
        default_factory=lambda: {
            ProofType.HASH: 600_000,  # 60%
            ProofType.AI: 200_000,  # 20%
            ProofType.QUANTUM: 120_000,  # 12%
            ProofType.STORAGE: 50_000,  # 5%
            ProofType.VDF: 30_000,  # 3%
        }
    )

    # Small floor to avoid division blow-ups when a type is nearly absent.
    epsilon_share_ppm: int = 10  # 0.001%

    # Global clamps for alpha (in SCALE units)
    min_alpha: int = SCALE // 4  # 0.25x
    max_alpha: int = SCALE * 4  # 4.00x

    # Per-update multiplicative clamps in ppm (e.g., 1.05x up, 0.95x down)
    step_up_ppm: int = 1_050_000
    step_down_ppm: int = 950_000

    # When True, renormalize α so target-weighted average is exactly SCALE
    normalize: bool = True


# ------------------------------- State object --------------------------------


@dataclass
class AlphaTunerState:
    # α weights per type in SCALE units
    alphas: Dict[ProofType, int] = field(
        default_factory=lambda: {t: SCALE for t in DEFAULT_TYPES}
    )
    # EMA accumulator per type, scaled by 1<<SHIFT
    ema_units_scaled: Dict[ProofType, int] = field(
        default_factory=lambda: {t: 0 for t in DEFAULT_TYPES}
    )
    # Last height we successfully applied an update
    last_update_height: int = -1

    def copy(self) -> "AlphaTunerState":
        return AlphaTunerState(
            alphas=dict(self.alphas),
            ema_units_scaled=dict(self.ema_units_scaled),
            last_update_height=self.last_update_height,
        )


# ------------------------------- Tuner logic ---------------------------------


class AlphaTuner:
    """
    Deterministic fairness tuner controlling α per proof type.

    Typical usage each block:
        tuner.record_block(units_by_type)          # feed observed micro-units
        if tuner.ready_to_update(height):
            delta = tuner.update(height)           # returns details of the change
    """

    def __init__(self, cfg: AlphaTunerConfig, state: AlphaTunerState | None = None):
        self.cfg = cfg
        self.state = state or AlphaTunerState()
        self._validate_config()

    # --- public API ---

    def get_alpha(self, t: ProofType) -> int:
        """Return α_t in SCALE units (deterministic fixed-point)."""
        return self.state.alphas.get(t, SCALE)

    def record_block(self, units_by_type: Mapping[ProofType, int]) -> None:
        """
        Feed observed *integral* micro-units for the just-sealed block.
        units_by_type is sparse; missing types are treated as zero.
        """
        S = self.cfg.SHIFT
        for t in DEFAULT_TYPES:
            prev = self.state.ema_units_scaled.get(t, 0)
            x = int(units_by_type.get(t, 0))
            # ema <- ema - (ema >> S) + (x << S)
            new = prev - (prev >> S) + (x << S)
            self.state.ema_units_scaled[t] = new

    def ready_to_update(self, height: int) -> bool:
        """Return True if an update is allowed at this height."""
        last = self.state.last_update_height
        if last < 0:
            # Allow an initial update after genesis+COOLDOWN to accumulate EMA.
            return height >= self.cfg.COOLDOWN
        return (height - last) >= self.cfg.COOLDOWN

    def maybe_update(self, height: int) -> "AlphaDelta | None":
        """If cooldown elapsed, apply an update and return the delta; else None."""
        if not self.ready_to_update(height):
            return None
        return self.update(height)

    def update(self, height: int) -> "AlphaDelta":
        """
        Compute observed shares from EMA, adjust α within per-step clamps,
        then normalize (optional) and persist the new α. Returns a delta summary.
        """
        shares_ppm = self._observed_shares_ppm()
        if shares_ppm is None:
            # No signal yet; keep α unchanged
            self.state.last_update_height = height
            return AlphaDelta(
                height=height,
                before={},
                after={},
                normalized_factor=SCALE,
                shares_ppm={},
            )

        before = dict(self.state.alphas)

        # First pass: per-type multiplicative adjustment within step clamps
        tentative: Dict[ProofType, int] = {}
        for t in DEFAULT_TYPES:
            a = before.get(t, SCALE)
            target_ppm = int(self.cfg.target_mix_ppm.get(t, 0))
            observed_ppm = max(self.cfg.epsilon_share_ppm, shares_ppm.get(t, 0))

            # ratio_ppm = target / observed, in ppm scale (i.e., 1.0 == 1_000_000)
            ratio_ppm = _div_ppm(
                target_ppm, observed_ppm
            )  # == (target_ppm * 1e6) // observed_ppm

            # Clamp the multiplicative step
            up = self.cfg.step_up_ppm
            dn = self.cfg.step_down_ppm
            ratio_ppm = max(min(ratio_ppm, up), dn)

            # alpha' = a * ratio_ppm / 1e6
            a_new = _mul_div(a, ratio_ppm, PPM)
            a_new = _clamp(a_new, self.cfg.min_alpha, self.cfg.max_alpha)
            tentative[t] = a_new

        # Optional normalization so target-weighted average α == SCALE
        if self.cfg.normalize:
            norm = _target_weighted_avg_ppm(tentative, self.cfg.target_mix_ppm)
            if norm != PPM:  # norm is in ppm of SCALE; PPM == 1.0
                # rescale by SCALE * PPM / norm (i.e., multiply by PPM/norm)
                # But our α are already in SCALE; multiplying by PPM/norm keeps them in SCALE.
                tentative = {t: _mul_div(a, PPM, norm) for t, a in tentative.items()}
                # Re-apply global clamps after normalization
                tentative = {
                    t: _clamp(a, self.cfg.min_alpha, self.cfg.max_alpha)
                    for t, a in tentative.items()
                }
            normalized_factor = _mul_div(SCALE, PPM, norm)  # informational
        else:
            normalized_factor = SCALE

        # Persist
        self.state.alphas.update(tentative)
        self.state.last_update_height = height

        return AlphaDelta(
            height=height,
            before=before,
            after=dict(self.state.alphas),
            normalized_factor=normalized_factor,
            shares_ppm=shares_ppm,
        )

    # --- helpers ---

    def _observed_shares_ppm(self) -> Dict[ProofType, int] | None:
        """Return per-type shares in ppm from EMA; None if total is zero."""
        total = 0
        for t in DEFAULT_TYPES:
            total += int(self.state.ema_units_scaled.get(t, 0))
        if total <= 0:
            return None
        out: Dict[ProofType, int] = {}
        for t in DEFAULT_TYPES:
            v = int(self.state.ema_units_scaled.get(t, 0))
            # ppm = round(1e6 * v / total)
            out[t] = _mul_div(v, PPM, total)
        # fix rounding drift to sum exactly 1e6 by nudging the largest component
        diff = PPM - sum(out.values())
        if diff != 0:
            # nudge the argmax
            t_max = max(DEFAULT_TYPES, key=lambda k: out.get(k, 0))
            out[t_max] = max(0, out[t_max] + diff)
        return out

    def _validate_config(self) -> None:
        # Ensure targets sum to 1e6
        s = sum(int(self.cfg.target_mix_ppm.get(t, 0)) for t in DEFAULT_TYPES)
        if s != PPM:
            # normalize deterministically
            tgt = {}
            acc = 0
            for i, t in enumerate(DEFAULT_TYPES):
                raw = int(self.cfg.target_mix_ppm.get(t, 0))
                # proportional share
                val = _mul_div(raw, PPM, s) if s > 0 else (PPM // len(DEFAULT_TYPES))
                tgt[t] = val
                acc += val
            # distribute drift
            if acc != PPM:
                # nudge the earliest types by +1 until sum==PPM
                d = PPM - acc
                for t in DEFAULT_TYPES:
                    if d == 0:
                        break
                    tgt[t] += 1 if d > 0 else -1
                    d += -1 if d > 0 else 1
            object.__setattr__(
                self.cfg, "target_mix_ppm", tgt
            )  # frozen dataclass override


# ------------------------------- Delta record --------------------------------


@dataclass(frozen=True)
class AlphaDelta:
    height: int
    before: Dict[ProofType, int]  # α before update (SCALE units)
    after: Dict[ProofType, int]  # α after update (SCALE units)
    normalized_factor: int  # informational: factor used in normalization (SCALE units)
    shares_ppm: Dict[ProofType, int]  # observed shares used for this update (ppm)


# ------------------------------- Math helpers --------------------------------


def _clamp(x: int, lo: int, hi: int) -> int:
    return lo if x < lo else hi if x > hi else x


def _mul_div(a: int, num: int, den: int) -> int:
    """
    Deterministic floor((a * num) / den) without overflow assumptions.
    Uses 128-bit-like widening via Python bigints.
    """
    if den <= 0:
        raise ValueError("den must be > 0")
    return (a * num) // den


def _div_ppm(num_ppm: int, den_ppm: int) -> int:
    """
    Return (num/den) in ppm scale, i.e., floor( (num_ppm * 1e6) / den_ppm ).
    Both arguments may be <= 1e6.
    """
    if den_ppm <= 0:
        # When denominator is zero/invalid, return a very large ratio but it will be clamped later.
        return 10_000_000  # 10x as a sentinel; will be clamped by step limits.
    return (num_ppm * PPM) // den_ppm


def _target_weighted_avg_ppm(
    alphas: Mapping[ProofType, int], targets_ppm: Mapping[ProofType, int]
) -> int:
    """
    Compute target-weighted average of α, expressed in ppm of SCALE, i.e.,
    avg_ppm = floor( 1e6 * ( Σ_t α_t * w_t ) / (SCALE * 1e6) ) where Σ w_t = 1e6.
    This simplifies to floor( Σ α_t * w_t / SCALE ).
    """
    s = 0
    for t in DEFAULT_TYPES:
        a = int(alphas.get(t, SCALE))
        w = int(targets_ppm.get(t, 0))
        s += a * w  # <= ~SCALE * 1e6 * #types; fine in Python
    # Divide by SCALE to express as ppm of SCALE (i.e., 1.0 == 1e6)
    return s // SCALE if SCALE > 0 else PPM


# ------------------------------- Serialization -------------------------------


def export_state(state: AlphaTunerState) -> Dict[str, Dict[str, int] | int]:
    """Serialize the tuner state to a plain dict (for KV/JSON storage)."""
    return {
        "alphas": {str(int(t)): v for t, v in state.alphas.items()},
        "ema_units_scaled": {str(int(t)): v for t, v in state.ema_units_scaled.items()},
        "last_update_height": state.last_update_height,
    }


def import_state(d: Mapping[str, object]) -> AlphaTunerState:
    """Inverse of export_state (lenient to missing fields)."""

    def _pt(k: str) -> ProofType:
        return ProofType(int(k))

    alphas = {_pt(k): int(v) for k, v in dict(d.get("alphas", {})).items()}  # type: ignore[arg-type]
    ema = {_pt(k): int(v) for k, v in dict(d.get("ema_units_scaled", {})).items()}  # type: ignore[arg-type]
    last = int(d.get("last_update_height", -1))  # type: ignore[arg-type]
    # Fill missing types with defaults to keep arrays dense and stable
    for t in DEFAULT_TYPES:
        alphas.setdefault(t, SCALE)
        ema.setdefault(t, 0)
    return AlphaTunerState(alphas=alphas, ema_units_scaled=ema, last_update_height=last)


# ------------------------------- Tiny self-test ------------------------------

if __name__ == "__main__":  # pragma: no cover
    # Demonstrate stability and bounded updates
    cfg = AlphaTunerConfig()
    tuner = AlphaTuner(cfg, AlphaTunerState())

    # Simulate 1000 blocks where HASH dominates (90%), AI (8%), QUANTUM (2%)
    import random

    rng = random.Random(42)
    for h in range(1, 1001):
        units = {
            ProofType.HASH: 900 + rng.randrange(0, 5),
            ProofType.AI: 80 + rng.randrange(0, 3),
            ProofType.QUANTUM: 20 + (rng.randrange(0, 2) if (h % 10) == 0 else 0),
            ProofType.STORAGE: 0,
            ProofType.VDF: 0,
        }
        tuner.record_block(units)
        if tuner.ready_to_update(h):
            delta = tuner.update(h)
            # Print a compact line every update
            aH = delta.after[ProofType.HASH] / SCALE
            aA = delta.after[ProofType.AI] / SCALE
            aQ = delta.after[ProofType.QUANTUM] / SCALE
            print(
                f"h={h:4d} shares(H/A/Q)={delta.shares_ppm[ProofType.HASH]}/{delta.shares_ppm[ProofType.AI]}/{delta.shares_ppm[ProofType.QUANTUM]}  "
                f"alpha(H/A/Q)={aH:.3f}/{aA:.3f}/{aQ:.3f}"
            )
