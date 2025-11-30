from __future__ import annotations

import math
import random

import pytest


def _binomial_p(n: int, k: int, p: float) -> float:
    """Binomial(n, p) probability of exactly k successes."""
    return math.comb(n, k) * (p**k) * ((1.0 - p) ** (n - k))


def da_sampling_failure_probability(
    total_shards: int,
    data_shards: int,
    withholding_prob: float,
    sample_count: int,
) -> float:
    """
    Compute exact probability that data-availability sampling *accepts* an
    unavailable blob (i.e., reconstruction is impossible, but all sampled
    shards look fine).

    Model:
      * Each of `total_shards` shards is independently withheld with
        probability `withholding_prob`.
      * Sampling: pick `sample_count` distinct shard indices uniformly
        without replacement.
      * DA check: if any sampled shard is missing -> FAIL (we detect DA
        failure). If all sampled shards are present -> ACCEPT.

    Failure event:
      - The blob is *not reconstructible* (available < data_shards), AND
      - All sampled shards are available (we never see a missing one).
    """
    t = total_shards
    k_data = data_shards
    k_samp = sample_count
    p = withholding_prob

    if k_samp > t:
        raise ValueError("sample_count cannot exceed total_shards")

    total_fail_prob = 0.0

    for missing in range(0, t + 1):
        available = t - missing

        # Probability that exactly `missing` shards are withheld.
        p_missing = _binomial_p(t, missing, p)

        # If we still have enough shards to reconstruct, then even if we
        # don't sample any missing shards, that's *not* a DA failure.
        if available >= k_data:
            fail_given_missing = 0.0
        else:
            # Blob is unreconstructible. DA sampling fails exactly if
            # none of the sampled shards are missing. That requires we
            # can choose all sampled shards from the available set.
            if available < k_samp:
                fail_given_missing = 0.0
            else:
                fail_given_missing = math.comb(available, k_samp) / math.comb(t, k_samp)

        total_fail_prob += p_missing * fail_given_missing

    return total_fail_prob


def simulate_da_sampling_failure(
    *,
    total_shards: int,
    data_shards: int,
    withholding_prob: float,
    sample_count: int,
    trials: int = 20_000,
    seed: int = 12345,
) -> float:
    """
    Monte Carlo simulation of the DA failure probability with a fixed seed,
    so the test is deterministic.

    Returns the fraction of trials where:
      - The blob was unreconstructible, AND
      - The sampling procedure still *accepted* it.
    """
    rng = random.Random(seed)
    t = total_shards
    k_data = data_shards
    k_samp = sample_count
    p = withholding_prob

    if k_samp > t:
        raise ValueError("sample_count cannot exceed total_shards")

    failures = 0

    for _ in range(trials):
        # Random withholding per shard
        unavailable = [rng.random() < p for _ in range(t)]
        available_count = t - sum(unavailable)

        reconstructible = available_count >= k_data

        # Sampling without replacement
        sample_indices = rng.sample(range(t), k_samp)
        saw_missing = any(unavailable[i] for i in sample_indices)
        accepted = not saw_missing

        if (not reconstructible) and accepted:
            failures += 1

    return failures / trials


# These parameters should roughly reflect a realistic DA config for a block's
# erasure-coded blob set.
TOTAL_SHARDS = 12  # data + parity
DATA_SHARDS = 8  # minimum required for reconstruction
WITHHOLDING_PROB = 0.30  # per-shard withholding probability
SAMPLE_COUNT = 6  # how many shards a light client samples
P_FAIL_TARGET = 5e-3  # maximum acceptable DA failure probability


def test_theoretical_failure_probability_below_target() -> None:
    """
    Analytically compute DA failure probability and ensure it is at or below
    the configured P_FAIL_TARGET.

    This test does not rely on randomness; it's a pure combinatorial check.
    """
    p_fail = da_sampling_failure_probability(
        total_shards=TOTAL_SHARDS,
        data_shards=DATA_SHARDS,
        withholding_prob=WITHHOLDING_PROB,
        sample_count=SAMPLE_COUNT,
    )

    # Sanity: probability must be in [0, 1].
    assert 0.0 <= p_fail <= 1.0

    # The whole point of the DA parameter choice is that this bound holds.
    assert (
        p_fail <= P_FAIL_TARGET
    ), f"DA sampling failure probability {p_fail:.6g} exceeds target {P_FAIL_TARGET:.6g}"


def test_empirical_failure_probability_matches_theory() -> None:
    """
    Monte Carlo sanity check that empirical failure probability matches the
    analytic result within a small tolerance.

    This mainly ensures the theoretical model is not completely detached
    from a more realistic random process.
    """
    p_theory = da_sampling_failure_probability(
        total_shards=TOTAL_SHARDS,
        data_shards=DATA_SHARDS,
        withholding_prob=WITHHOLDING_PROB,
        sample_count=SAMPLE_COUNT,
    )

    p_empirical = simulate_da_sampling_failure(
        total_shards=TOTAL_SHARDS,
        data_shards=DATA_SHARDS,
        withholding_prob=WITHHOLDING_PROB,
        sample_count=SAMPLE_COUNT,
        trials=20_000,
        seed=123,
    )

    # They should be reasonably close.
    diff = abs(p_empirical - p_theory)
    # With 20k trials and a true p ~ 1e-3, the standard error is about 5e-4.
    # A tolerance of 1e-3 is comfortably larger than that.
    assert (
        diff < 1e-3
    ), f"Empirical p_fail={p_empirical:.6g} deviates too much from theory={p_theory:.6g} (diff={diff:.6g})"

    # And empirical should also respect the configured bound (with a tiny cushion).
    assert p_empirical <= P_FAIL_TARGET * 1.2
