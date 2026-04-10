"""Experimental quantum simulation hooks.

This package contains simulator-only utilities that demonstrate how Animica's
useful-work ideas could consume quantum-inspired features. Imports are kept
lazy to avoid pulling optional dependencies into the core runtime.
"""

from .experiment import (QuantumExperiment, QuantumResult,
                         simulate_from_pow_input)

__all__ = ["QuantumExperiment", "QuantumResult", "simulate_from_pow_input"]
