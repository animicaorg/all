from __future__ import annotations
"""
AICF test suite package.

This module exposes tiny helpers shared across the AICF tests. We avoid
mutating global randomness on import; tests opt-in to determinism by calling
`reseed_random()` when needed.
"""


# Canonical deterministic seed for tests that need pseudo-randomness.
TEST_SEED: int = 0xA1CFF00D


def reseed_random() -> None:
    """
    Reseed Python's random module with TEST_SEED.

    Tests that involve shuffles or tie-breaking randomness should call this at
    the start to make results reproducible across platforms.
    """
    import random
    random.seed(TEST_SEED)


__all__ = ["TEST_SEED", "reseed_random"]
