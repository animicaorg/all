from __future__ import annotations

import math
import random

from consensus.difficulty import RetargetParams, init_state, micro_to_nats, update_theta


def test_block_time_converges_near_300s() -> None:
    params = RetargetParams(
        target_block_time_s=300.0,
        half_life_blocks=24.0,
        gain_beta=0.75,
        step_clamp_micro=400_000,
        theta_min_micro=500_000,
        theta_max_micro=None,
    )
    theta_init = 2_000_000
    state = init_state(params, theta_init_micro=theta_init)

    base_tau = micro_to_nats(theta_init)
    rng = random.Random(1337)
    dt_samples: list[float] = []

    for i in range(200):
        tau = micro_to_nats(state.theta_micro)
        expected_dt = params.target_block_time_s * math.exp(tau - base_tau)
        dt = rng.expovariate(1.0 / expected_dt)
        dt = max(params.target_block_time_s * 0.2, min(params.target_block_time_s * 5.0, dt))
        state = update_theta(state, dt_seconds=dt)
        if i >= 50:
            dt_samples.append(dt)

    avg_dt = sum(dt_samples) / len(dt_samples)
    assert abs(avg_dt - params.target_block_time_s) / params.target_block_time_s < 0.25
