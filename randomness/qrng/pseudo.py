"""
randomness.qrng.pseudo
=====================

A **pseudo-quantum** entropy source: a simulated QRNG that models quantum
measurement (a qubit prepared in superposition, then measured — collapse to 0/1)
so the whole Quantum Useful Work / randomness lane keeps working on machines with
no real quantum hardware. It is explicitly, unmistakably NON-quantum and
NON-attested (`is_quantum=False`, `attested=False`); it exists so the network and
demos function, and the source manager (``randomness.qrng.manager``) flips away
from it the moment a real provider connects.

The measurement model uses ``animica.quantum.simulator`` when available: a qubit
prepared with Hadamard + Rz(theta) has P(measure=1) = (1 - cos(theta))/2 + ...;
at the default ideal theta=0 this is a fair coin (uniform, passes the health
battery). A nonzero ``bias_theta`` lets you model a decohered/biased source (useful
for testing the health gate). The collapse outcomes are drawn from the OS CSPRNG —
the "quantum" part is the model, not the entropy, hence *pseudo*.
"""

from __future__ import annotations

import os
from typing import Optional

from .providers import QuantumEntropySource, SourceInfo


def _measure_p1(theta: float) -> float:
    """P(measure |1>) for a qubit after Hadamard then Rz(theta), via the simulator."""
    if abs(theta) < 1e-9:
        return 0.5
    try:
        from animica.quantum.simulator import simulate  # optional
        outcome = simulate(theta)
        for attr in ("p1", "prob_one", "p_one", "probability_one"):
            v = getattr(outcome, attr, None)
            if isinstance(v, (int, float)):
                return max(0.0, min(1.0, float(v)))
        probs = getattr(outcome, "probabilities", None) or getattr(outcome, "probs", None)
        if probs and len(probs) >= 2:
            return max(0.0, min(1.0, float(probs[1])))
    except Exception:
        pass
    # Analytic fallback: |+> rotated by Rz doesn't change measurement prob; model a
    # simple visibility/decoherence bias from theta so the knob still does something.
    import math
    return max(0.0, min(1.0, 0.5 + 0.5 * math.sin(theta) * 0.5))


class PseudoQuantumSource(QuantumEntropySource):
    """Simulated-QRNG entropy source (pseudo, non-attested)."""

    def __init__(self, *, bias_theta: float = 0.0) -> None:
        self._theta = float(bias_theta)
        self._p1 = _measure_p1(self._theta)

    def info(self) -> SourceInfo:
        return SourceInfo(
            name="pseudo-quantum",
            vendor="animica",
            model="simulated QRNG (Hadamard-basis measurement)",
            is_hardware=False,
            is_quantum=False,
            device_path=None,
            attested=False,
            notes=f"pseudo (p1={self._p1:.4f}); flips to real QRNG when a provider connects",
        )

    def random_bytes(self, n: int) -> bytes:
        if n < 0:
            raise ValueError("n must be non-negative")
        if n == 0:
            return b""
        # Ideal fair-coin measurements == OS CSPRNG bytes (fast path).
        if abs(self._p1 - 0.5) < 1e-6:
            return os.urandom(n)
        # Biased measurement model: sample each bit as a collapse with P(1)=p1.
        p1 = self._p1
        out = bytearray(n)
        # Draw 8 collapse decisions per byte from a wide CSPRNG draw to avoid bias.
        rnd = os.urandom(n * 8)
        thr = int(p1 * 256)
        bi = 0
        for i in range(n):
            b = 0
            for _ in range(8):
                bit = 1 if rnd[bi] < thr else 0
                bi += 1
                b = (b << 1) | bit
            out[i] = b
        return bytes(out)
