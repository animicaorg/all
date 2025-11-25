"""
Test package for animica.mining

- Sets deterministic RNG seeds for reproducibility.
- Enables "fast" test mode for mining (lower Θ and tiny inner-loop bounds)
  via environment flags that the mining code honors where applicable.
- Skips GPU-marked tests unless ANIMICA_GPU_TESTS=1 is set.
"""

from __future__ import annotations
import os
import random
import logging
from typing import Iterator

# Keep test logs sane by default (individual tests can raise levels)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# Fast paths / deterministic knobs used by tests
os.environ.setdefault("ANIMICA_TESTING", "1")
os.environ.setdefault("ANIMICA_MINING_TEST_FAST", "1")   # inner loops use tiny bounds
os.environ.setdefault("ANIMICA_DISABLE_NUMBA", "1")      # avoid JIT warmup in CI unless explicitly enabled

try:
    import pytest  # type: ignore
except Exception:  # pragma: no cover
    pytest = None  # type: ignore[misc]


if pytest:
    @pytest.fixture(scope="session", autouse=True)
    def _deterministic_rng_session() -> Iterator[None]:
        """
        Session-wide deterministic RNG seeding. Tests that need their own
        randomness should create a local Random() to avoid global coupling.
        """
        random.seed(0xA11CE)  # consistent across runs
        try:
            import numpy as _np  # type: ignore
            _np.random.seed(1337)
        except Exception:
            pass
        yield

    def pytest_collection_modifyitems(config, items):
        """
        Skip tests marked with @pytest.mark.gpu unless ANIMICA_GPU_TESTS=1.
        """
        if os.getenv("ANIMICA_GPU_TESTS") not in ("1", "true", "TRUE", "yes", "YES"):
            import pytest as _pytest  # lazy import for hook
            skip_gpu = _pytest.mark.skip(reason="GPU tests disabled (set ANIMICA_GPU_TESTS=1 to enable)")
            for item in items:
                if "gpu" in item.keywords:
                    item.add_marker(skip_gpu)
