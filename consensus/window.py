"""
Retarget-window & epoch helpers + observed inter-block rate (λ)
================================================================

This module provides small, deterministic utilities used by consensus.difficulty
and friends:

- Epoch math: index and bounds for fixed-length epochs.
- Window bounds: select a [start, end] inclusive height window for retargeting.
- Observed inter-block rate λ in fixed-point SCALE (1e9 = 1.0), from block
  timestamp sequences using robust integer-only estimators.

All arithmetic is integer/fixed-point to guarantee bit-for-bit determinism.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Mapping, Sequence, Tuple

# Fixed-point: 1.0 == 1_000_000_000
SCALE = 10**9
PPM = 1_000_000


# ------------------------------- Epoch helpers -------------------------------

def epoch_index(height: int, epoch_len: int) -> int:
    """
    Zero-based epoch index for a given block height.

    - height >= 0
    - epoch_len > 0
    """
    if epoch_len <= 0:
        raise ValueError("epoch_len must be > 0")
    if height < 0:
        raise ValueError("height must be >= 0")
    return height // epoch_len


def epoch_bounds(index: int, epoch_len: int) -> Tuple[int, int]:
    """
    Inclusive height bounds for the given epoch index:
    returns (start_height, end_height).

    Epoch 0: [0, epoch_len-1]
    Epoch k: [k*epoch_len, (k+1)*epoch_len - 1]
    """
    if epoch_len <= 0:
        raise ValueError("epoch_len must be > 0")
    if index < 0:
        raise ValueError("index must be >= 0")
    start = index * epoch_len
    end = (index + 1) * epoch_len - 1
    return start, end


# ---------------------------- Retarget window math ---------------------------

@dataclass(frozen=True)
class WindowSpec:
    """
    Retarget window specification.

    Attributes
    ----------
    size_blocks : int
        Number of consecutive blocks considered in the window (W).
    include_tip : bool
        If True, the window ends at the tip height (inclusive).
        If False, the window ends at tip-1 (useful when computing a window
        before the next block is sealed).
    """
    size_blocks: int
    include_tip: bool = True

    def __post_init__(self) -> None:
        if self.size_blocks <= 0:
            raise ValueError("size_blocks must be > 0")


def window_bounds_for_height(tip_height: int, spec: WindowSpec) -> Tuple[int, int]:
    """
    Return inclusive [start, end] bounds for the retarget window at a given tip.

    Examples (W=5):
      - include_tip=True,  tip=12 → [8, 12]
      - include_tip=False, tip=12 → [7, 11]  (window anchored before the tip)
    """
    if tip_height < 0:
        raise ValueError("tip_height must be >= 0")

    end = tip_height if spec.include_tip else tip_height - 1
    start = max(0, end - (spec.size_blocks - 1))
    return start, max(end, start)  # ensure non-decreasing


# ----------------------------- Timestamp utilities ---------------------------

def diffs_from_timestamps(ts: Sequence[int]) -> List[int]:
    """
    Convert a non-decreasing sequence of block timestamps (integers, seconds)
    into a sequence of inter-block intervals Δt_i = max(1, ts[i] - ts[i-1]).

    - Clamps negative/zero diffs to 1 to preserve determinism and avoid
      division-by-zero in rate estimators.
    - For len(ts) < 2 returns [].
    """
    n = len(ts)
    if n < 2:
        return []
    out: List[int] = []
    prev = int(ts[0])
    for i in range(1, n):
        cur = int(ts[i])
        dt = cur - prev
        if dt <= 0:
            dt = 1
        out.append(dt)
        prev = cur
    return out


# -------------------------- Fixed-point rate estimators -----------------------

def lambda_from_mean_interval(dt_list: Sequence[int]) -> int:
    """
    Compute λ (blocks/sec) in SCALE fixed-point from a list of integer intervals
    (seconds) using the *arithmetic* mean of Δt and then λ = 1 / mean(Δt).

    λ = 1 / (Σ Δt / N) = N / Σ Δt

    Returns SCALE * λ = floor( SCALE * N / Σ Δt ).
    If dt_list is empty, returns 0.
    """
    n = len(dt_list)
    if n == 0:
        return 0
    total = 0
    for dt in dt_list:
        d = int(dt)
        if d <= 0:
            d = 1
        total += d
    return (SCALE * n) // total


def lambda_harmonic(dt_list: Sequence[int]) -> int:
    """
    Compute λ (blocks/sec) in SCALE fixed-point using the *harmonic* estimator:

      λ̂ = (1/N) * Σ (1/Δt_i)

    Implemented as fixed-point average of per-interval rates:
      r_i_fp = floor(SCALE / Δt_i)
      λ̂_fp   = floor( Σ r_i_fp / N )

    More robust under outliers when a few Δt are very large.
    For empty list, returns 0.
    """
    n = len(dt_list)
    if n == 0:
        return 0
    acc = 0
    for dt in dt_list:
        d = int(dt)
        if d <= 0:
            d = 1
        acc += SCALE // d
    return acc // n


def ema_interval_update(prev_ema_dt: int, new_dt: int, shift: int) -> int:
    """
    Exponential moving average of Δt with 2^shift window (integer-only):

        ema <- ema - (ema >> shift) + (new_dt << shift)

    The returned value is the scaled accumulator. To get the current EMA
    interval in seconds, call `ema_interval_value(ema, shift)`.
    """
    if shift < 0:
        raise ValueError("shift must be >= 0")
    if new_dt <= 0:
        new_dt = 1
    ema = int(prev_ema_dt)
    ema = ema - (ema >> shift) + (int(new_dt) << shift)
    # ema is scaled by 1<<shift
    return ema


def ema_interval_value(ema_scaled: int, shift: int) -> int:
    """
    Convert scaled EMA accumulator back to an integer interval (seconds):
        value = floor(ema_scaled / 2^shift)
    """
    if shift < 0:
        raise ValueError("shift must be >= 0")
    return int(ema_scaled) >> shift


def lambda_from_ema_interval(ema_scaled: int, shift: int) -> int:
    """
    Compute λ from an EMA accumulator of Δt (seconds):

        Δt_ema = ema_scaled >> shift
        λ      = 1 / Δt_ema

    Returns λ in SCALE fixed-point. If Δt_ema == 0 (uninitialized), returns 0.
    """
    dt = ema_interval_value(ema_scaled, shift)
    if dt <= 0:
        return 0
    return SCALE // dt


# ------------------------- Convenience end-to-end helpers --------------------

def observed_lambda_from_timestamps(
    timestamps_sec: Sequence[int],
    method: str = "harmonic",
) -> int:
    """
    Convenience wrapper:
      - builds Δt list from a sequence of integer timestamps (seconds),
      - computes λ using the chosen method: "harmonic" (default) or "mean".

    Returns λ in SCALE fixed-point.
    """
    dts = diffs_from_timestamps(timestamps_sec)
    if method == "harmonic":
        return lambda_harmonic(dts)
    if method == "mean":
        return lambda_from_mean_interval(dts)
    raise ValueError("method must be 'harmonic' or 'mean'")


def clamp_retgt_ppm(ratio_ppm: int, up_ppm: int, down_ppm: int) -> int:
    """
    Clamp a multiplicative retarget ratio expressed in ppm (1e6 = 1.0) with
    asymmetric bounds (step-up and step-down limits in ppm).

    Example: up=1_050_000 (1.05x), down=950_000 (0.95x).
    """
    if up_ppm < PPM or down_ppm > PPM or up_ppm <= 0 or down_ppm <= 0:
        raise ValueError("invalid bounds")
    if ratio_ppm > up_ppm:
        return up_ppm
    if ratio_ppm < down_ppm:
        return down_ppm
    return ratio_ppm


def retarget_ratio_ppm(target_interval_sec: int, observed_interval_sec: int) -> int:
    """
    Compute the multiplicative retarget ratio in ppm to move the current target
    interval toward the observed interval:

        ratio = target / observed

    Returned as floor(1e6 * target / observed).
    """
    t = max(1, int(target_interval_sec))
    o = max(1, int(observed_interval_sec))
    return (PPM * t) // o


def apply_ratio_int(value: int, ratio_ppm: int) -> int:
    """
    Apply a multiplicative ratio in ppm to an integer value, deterministically:

        new_value = floor(value * ratio_ppm / 1e6)
    """
    return (int(value) * int(ratio_ppm)) // PPM


# --------------------------------- Demo --------------------------------------

if __name__ == "__main__":  # pragma: no cover
    # Tiny self-check demonstrating window bounds and λ estimators.
    print("== window bounds ==")
    ws = WindowSpec(size_blocks=5, include_tip=True)
    print("tip=12, W=5:", window_bounds_for_height(12, ws))
    ws2 = WindowSpec(size_blocks=5, include_tip=False)
    print("tip=12, W=5, pre-tip:", window_bounds_for_height(12, ws2))

    print("\n== epoch math ==")
    for h in [0, 1, 9, 10, 19, 20]:
        ei = epoch_index(h, 10)
        print(f"h={h} → epoch {ei}, bounds={epoch_bounds(ei, 10)}")

    print("\n== λ from timestamps ==")
    # Synthetic timestamps (seconds): target 10s/block with some jitter
    ts = [0, 10, 21, 30, 41, 51, 61, 71, 82, 92, 101]
    dts = diffs_from_timestamps(ts)
    print("dts:", dts)
    lam_h = observed_lambda_from_timestamps(ts, "harmonic")
    lam_m = observed_lambda_from_timestamps(ts, "mean")
    print(f"λ_harmonic={lam_h/SCALE:.6f} blocks/sec; λ_mean={lam_m/SCALE:.6f} blocks/sec")

    # EMA demo
    shift = 8
    ema = 0
    for dt in dts:
        ema = ema_interval_update(ema, dt, shift)
    dt_ema = ema_interval_value(ema, shift)
    lam_ema = lambda_from_ema_interval(ema, shift)
    print(f"EMA Δt={dt_ema}s, λ_ema={lam_ema/SCALE:.6f} blocks/sec")
