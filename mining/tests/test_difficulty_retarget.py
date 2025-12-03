from __future__ import annotations

import random

import consensus.difficulty as diff


def test_difficulty_retarget_stability():
    params = diff.RetargetParams()
    theta_init = diff.nats_to_micro(6.0)
    state = diff.init_state(params, theta_init_micro=theta_init)

    rng = random.Random(424242)
    target = params.target_block_time_s
    thetas = []

    for i in range(120):
        base_jitter = rng.uniform(-0.25, 0.25)
        burst_factor = 0.6 if i % 17 == 0 else 1.0
        lag_factor = 1.7 if i % 29 == 0 else 1.0
        dt = target * (1.0 + base_jitter) * burst_factor * lag_factor
        dt = max(target * 0.35, min(target * 2.5, dt))

        state = diff.update_theta(state, dt)
        thetas.append(state.theta_micro)

    theta_min, theta_max = min(thetas), max(thetas)
    band = theta_max / theta_min

    assert params.theta_min_micro < theta_min
    assert theta_max < params.theta_max_micro
    assert band < 3.0, "Θ should stay within a modest range under mixed intervals"

    recent = thetas[-25:]
    recent_band = max(recent) / min(recent)
    assert recent_band < 1.6, "Θ should settle instead of oscillating wildly"
