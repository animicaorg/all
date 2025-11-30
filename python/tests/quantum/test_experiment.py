import math

import pytest
from animica.quantum.experiment import (QuantumExperiment,
                                        simulate_from_pow_input)
from animica.quantum.simulator import run_statevector


@pytest.fixture
def sample_payload():
    return {
        "block_hash": "0xabc123",
        "nonce": 99,
        "difficulty": 3,
    }


def test_statevector_normalized():
    outcome = run_statevector(theta=0.5)
    norm = sum(abs(amplitude) ** 2 for amplitude in outcome.state)
    assert math.isclose(norm, 1.0, rel_tol=1e-9)


def test_experiment_determinism(sample_payload):
    experiment = QuantumExperiment(seed=42)
    first = experiment.run(sample_payload)
    second = experiment.run(sample_payload)
    assert first == second


def test_pow_helper_reproducible(sample_payload):
    result_one = simulate_from_pow_input(
        sample_payload["block_hash"],
        sample_payload["nonce"],
        sample_payload["difficulty"],
        seed=7,
    )
    result_two = simulate_from_pow_input(
        sample_payload["block_hash"],
        sample_payload["nonce"],
        sample_payload["difficulty"],
        seed=7,
    )
    assert result_one.score == result_two.score
    assert result_one.fingerprint == result_two.fingerprint
