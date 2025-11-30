"""Lightweight quantum-like simulator used for deterministic tests.

The simulator is intentionally small to avoid pulling heavyweight
optional dependencies into the Animica core. A Qiskit-based execution
path is available when the ``quantum`` extra is installed, but the
built-in statevector path is sufficient for CI and local testing.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, sin, sqrt
from typing import Optional, Sequence


@dataclass
class SimulationOutcome:
    """Container for simulator results."""

    state: Sequence[complex]
    probability_one: float
    angle: float
    backend: str


def hadamard(state: Sequence[complex]) -> list[complex]:
    norm = 1 / sqrt(2)
    return [norm * (state[0] + state[1]), norm * (state[0] - state[1])]


def rz(state: Sequence[complex], theta: float) -> list[complex]:
    phase_plus = complex(cos(theta / 2), -sin(theta / 2))
    phase_minus = complex(cos(theta / 2), sin(theta / 2))
    return [state[0] * phase_plus, state[1] * phase_minus]


def run_statevector(theta: float) -> SimulationOutcome:
    state: list[complex] = [1 + 0j, 0 + 0j]
    state = hadamard(state)
    state = rz(state, theta)
    probability_one = abs(state[1]) ** 2
    return SimulationOutcome(
        state=state, probability_one=probability_one, angle=theta, backend="builtin"
    )


def run_qiskit(theta: float) -> Optional[SimulationOutcome]:
    try:
        from qiskit import QuantumCircuit
        from qiskit.quantum_info import Statevector
    except ImportError:  # pragma: no cover - optional dependency
        return None

    circuit = QuantumCircuit(1)
    circuit.h(0)
    circuit.rz(theta, 0)
    sv = Statevector.from_instruction(circuit)
    probability_one = abs(sv.data[1]) ** 2
    return SimulationOutcome(
        state=list(sv.data),
        probability_one=probability_one,
        angle=theta,
        backend="qiskit",
    )


def simulate(theta: float, prefer_qiskit: bool = False) -> SimulationOutcome:
    if prefer_qiskit:
        outcome = run_qiskit(theta)
        if outcome:
            return outcome
    return run_statevector(theta)
