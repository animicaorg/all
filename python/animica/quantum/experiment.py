"""Public interface for quantum-inspired experimentation.

The API deliberately keeps imports lazy to avoid forcing optional
quantum dependencies on consumers who do not opt into the feature.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .simulator import SimulationOutcome, simulate


@dataclass
class QuantumResult:
    """Structured output returned by :class:`QuantumExperiment`."""

    probability_one: float
    score: int
    angle: float
    backend: str
    fingerprint: str


class QuantumExperiment:
    """Run a tiny circuit seeded from PoW-like data."""

    def __init__(self, seed: Optional[int] = None, prefer_qiskit: bool = False):
        self.seed = seed
        self.prefer_qiskit = prefer_qiskit

    def _fingerprint_payload(self, payload: bytes) -> tuple[str, float]:
        digest = hashlib.sha256(payload).digest()
        # Map eight bytes onto a rotation angle in [0, 2π)
        angle_seed = int.from_bytes(digest[:8], "big")
        angle = (angle_seed % 6_283_185) / 1_000_000  # up to ~2π
        if self.seed is not None:
            # Simple deterministic jitter to avoid identical angles when the caller
            # wants to sweep through seeds.
            angle += (self.seed % 1000) / 1_000_000
        fingerprint = hashlib.sha256(digest).hexdigest()[:16]
        return fingerprint, angle

    def run(self, payload: Mapping[str, Any]) -> QuantumResult:
        payload_bytes = json.dumps(payload, sort_keys=True).encode()
        fingerprint, angle = self._fingerprint_payload(payload_bytes)
        outcome: SimulationOutcome = simulate(angle, prefer_qiskit=self.prefer_qiskit)
        score = int(outcome.probability_one * 1_000_000) ^ int(self.seed or 0)
        return QuantumResult(
            probability_one=outcome.probability_one,
            score=score,
            angle=outcome.angle,
            backend=outcome.backend,
            fingerprint=fingerprint,
        )


def simulate_from_pow_input(block_hash: str, nonce: int, difficulty: int = 1, *, prefer_qiskit: bool = False, seed: Optional[int] = None) -> QuantumResult:
    """Helper for PoW-like caller data.

    The payload is a light representation of block header fields and a
    nonce. The output includes a deterministic score derived from the
    circuit measurement probability.
    """

    payload = {
        "block_hash": block_hash,
        "nonce": nonce,
        "difficulty": difficulty,
    }
    experiment = QuantumExperiment(seed=seed, prefer_qiskit=prefer_qiskit)
    return experiment.run(payload)
