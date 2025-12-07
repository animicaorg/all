"""
Integration test: PoIES scoring with quantum job contributions.

Demonstrates:
1. Block with no quantum jobs but H(u) > Θ → accepted
2. Block with low H(u) and no quantum jobs → rejected
3. Block with quantum job completion crosses threshold (rejected→accepted)
"""

from __future__ import annotations

import pytest

from consensus.scorer import aggregate_and_accept, default_score_hooks
from consensus.types import ProofType


class MockPolicy:
    """Mock PoIES policy for testing."""

    def __init__(self):
        # Per-type caps (micro-nats)
        from collections import namedtuple

        Cap = namedtuple("Cap", "per_type_micro per_proof_micro_max")
        self.caps = {
            ProofType.HASH: Cap(5_000_000, 3_000_000),
            ProofType.AI: Cap(7_000_000, 5_000_000),
            ProofType.QUANTUM: Cap(7_000_000, 5_000_000),
            ProofType.STORAGE: Cap(6_000_000, 4_000_000),
            ProofType.VDF: Cap(6_000_000, 4_000_000),
        }
        # Total Γ cap (micro-nats)
        self.gamma_cap = 12_000_000
        # Weights for quantum scoring
        self.weights = {
            ProofType.QUANTUM: {
                "k_units": 1.5,  # quantum units weight
                "t_min": 0.65,  # minimum trap ratio
                "t_target": 0.9,  # target trap ratio
            }
        }


@pytest.fixture
def policy():
    return MockPolicy()


def test_block_accepted_with_sufficient_hash_only(policy):
    """Block with sufficient H(u) but no quantum jobs should be accepted."""
    # H(u) contribution from hash mining (micro-nats)
    base_entropy_micro = 3_000_000  # 3.0 nats

    # Threshold (micro-nats)
    theta_micro = 2_500_000  # 2.5 nats

    # No proofs at all, just base entropy
    proofs = []

    hooks = default_score_hooks(policy)
    outcome = aggregate_and_accept(
        proofs,
        policy,
        theta_micro=theta_micro,
        base_entropy_micro=base_entropy_micro,
        hooks=hooks,
    )

    assert outcome.accepted, f"Should accept: S={outcome.score_micro} >= Θ={theta_micro}"
    assert outcome.score_micro >= theta_micro
    assert outcome.base_entropy_micro == base_entropy_micro


def test_block_rejected_below_threshold(policy):
    """Block below threshold should be rejected."""
    # Low H(u)
    base_entropy_micro = 1_000_000  # 1.0 nat

    # High threshold
    theta_micro = 5_000_000  # 5.0 nats

    # No external proofs
    proofs = []

    hooks = default_score_hooks(policy)
    outcome = aggregate_and_accept(
        proofs,
        policy,
        theta_micro=theta_micro,
        base_entropy_micro=base_entropy_micro,
        hooks=hooks,
    )

    assert not outcome.accepted, f"Should reject: S={outcome.score_micro} < Θ={theta_micro}"
    assert outcome.score_micro < theta_micro


def test_quantum_job_flips_validity(policy):
    """Quantum job completion should flip block from invalid to valid."""
    # Borderline case: base entropy just below threshold
    base_entropy_micro = 2_800_000  # 2.8 nats
    theta_micro = 3_000_000  # 3.0 nats

    # Case 1: No quantum jobs → rejected
    proofs_without_quantum = []

    hooks = default_score_hooks(policy)
    outcome_no_quantum = aggregate_and_accept(
        proofs_without_quantum,
        policy,
        theta_micro=theta_micro,
        base_entropy_micro=base_entropy_micro,
        hooks=hooks,
    )

    assert (
        not outcome_no_quantum.accepted
    ), f"Without quantum: S={outcome_no_quantum.score_micro} < Θ={theta_micro}"
    assert outcome_no_quantum.score_micro < theta_micro

    # Case 2: Add quantum job → accepted
    # Quantum job: 5 qubits, depth 10, 100 shots
    # quantum_units ≈ 5 * 10 * log(101) ≈ 230
    # With good trap ratio (0.85) and QoS (0.95):
    # ψ_quantum ≈ k_units * units * qos * q_traps
    # q_traps = (0.85 - 0.65) / (0.9 - 0.65) = 0.8
    # ψ ≈ 1.5 * 1.0 * 0.95 * 0.8 ≈ 1.14 nats ≈ 1_140_000 micro-nats
    proofs_with_quantum = [
        {
            "proof_id": b"\x01" * 32,
            "proof_type": ProofType.QUANTUM,
            "metrics": {
                "quantum_units": 1.0,  # normalized units
                "traps_ratio": 0.85,
                "qos": 0.95,
            },
        }
    ]

    outcome_with_quantum = aggregate_and_accept(
        proofs_with_quantum,
        policy,
        theta_micro=theta_micro,
        base_entropy_micro=base_entropy_micro,
        hooks=hooks,
    )

    assert (
        outcome_with_quantum.accepted
    ), f"With quantum: S={outcome_with_quantum.score_micro} >= Θ={theta_micro}"
    assert outcome_with_quantum.score_micro >= theta_micro

    # Verify quantum contribution is non-zero and significant
    quantum_contribution = (
        outcome_with_quantum.score_micro - outcome_no_quantum.score_micro
    )
    assert (
        quantum_contribution > 0
    ), f"Quantum should add positive contribution: {quantum_contribution}"
    assert quantum_contribution > 200_000, f"Quantum contribution should be significant"


def test_multiple_quantum_jobs_aggregated(policy):
    """Multiple quantum jobs should be aggregated with caps applied."""
    base_entropy_micro = 1_000_000
    theta_micro = 3_000_000

    # Two quantum jobs
    proofs = [
        {
            "proof_id": b"\x01" * 32,
            "proof_type": ProofType.QUANTUM,
            "metrics": {
                "quantum_units": 2.0,
                "traps_ratio": 0.88,
                "qos": 0.95,
            },
        },
        {
            "proof_id": b"\x02" * 32,
            "proof_type": ProofType.QUANTUM,
            "metrics": {
                "quantum_units": 1.5,
                "traps_ratio": 0.90,
                "qos": 0.92,
            },
        },
    ]

    hooks = default_score_hooks(policy)
    outcome = aggregate_and_accept(
        proofs,
        policy,
        theta_micro=theta_micro,
        base_entropy_micro=base_entropy_micro,
        hooks=hooks,
    )

    assert outcome.accepted, "Multiple quantum jobs should aggregate to pass threshold"

    # Check breakdown includes quantum contributions
    assert "per_type_after_gamma" in outcome.breakdown
    quantum_total = outcome.breakdown["per_type_after_gamma"].get("QUANTUM", 0)
    assert quantum_total > 0, "Quantum contribution should be non-zero"


def test_quantum_with_poor_traps_rejected(policy):
    """Quantum job with poor trap ratio should contribute minimal ψ."""
    base_entropy_micro = 2_900_000
    theta_micro = 3_000_000

    # Quantum job with poor trap ratio (below t_min)
    proofs = [
        {
            "proof_id": b"\x01" * 32,
            "proof_type": ProofType.QUANTUM,
            "metrics": {
                "quantum_units": 10.0,  # High units
                "traps_ratio": 0.50,  # Below t_min (0.65)
                "qos": 0.99,
            },
        }
    ]

    hooks = default_score_hooks(policy)
    outcome = aggregate_and_accept(
        proofs,
        policy,
        theta_micro=theta_micro,
        base_entropy_micro=base_entropy_micro,
        hooks=hooks,
    )

    # Should not be accepted because trap quality is too low
    assert (
        not outcome.accepted
    ), "Poor trap ratio should result in minimal contribution"

    # Quantum contribution should be minimal (trap quality = 0 when traps < t_min)
    quantum_contrib = outcome.breakdown["per_type_after_gamma"].get("QUANTUM", 0)
    assert quantum_contrib < 100_000, "Poor traps should contribute minimally"


def test_quantum_caps_enforced(policy):
    """Quantum proofs should respect per-type and global caps."""
    base_entropy_micro = 1_000_000
    theta_micro = 20_000_000  # Very high threshold

    # Many quantum jobs with high scores
    proofs = [
        {
            "proof_id": bytes([i]) * 32,
            "proof_type": ProofType.QUANTUM,
            "metrics": {
                "quantum_units": 100.0,
                "traps_ratio": 0.95,
                "qos": 0.99,
            },
        }
        for i in range(10)
    ]

    hooks = default_score_hooks(policy)
    outcome = aggregate_and_accept(
        proofs,
        policy,
        theta_micro=theta_micro,
        base_entropy_micro=base_entropy_micro,
        hooks=hooks,
    )

    # Check caps are enforced
    quantum_after_caps = outcome.breakdown["per_type_after_gamma"].get("QUANTUM", 0)
    
    # Should not exceed per-type cap (7_000_000 micro-nats = 7.0 nats)
    assert (
        quantum_after_caps <= 7_000_000
    ), f"Quantum should respect per-type cap: {quantum_after_caps}"

    # Total should not exceed Γ (12_000_000)
    total_psi = outcome.breakdown["sum_after_gamma"]
    assert (
        total_psi <= 12_000_000
    ), f"Total ψ should respect Γ cap: {total_psi}"


if __name__ == "__main__":
    # Run with: python -m pytest tests/integration/test_poies_quantum_integration.py -v
    pytest.main([__file__, "-v"])
