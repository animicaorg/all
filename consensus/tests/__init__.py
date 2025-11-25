"""
consensus.tests helpers

- Provides deterministic test defaults (RNG, Hypothesis profile).
- Convenience loaders for fixture files:
    load_policy_example(), load_genesis_header()
- Path helpers: ROOT, FIXTURES
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Any, Dict, Optional

# ----- Paths -----
HERE = Path(__file__).resolve()
PKG_ROOT = HERE.parents[1]          # ~/animica/consensus
ROOT = PKG_ROOT.parents[0]          # ~/animica
FIXTURES = PKG_ROOT / "fixtures"

# ----- Determinism knobs -----
# Ensure stable hashing across runs (important for policy hashing / receipts)
os.environ.setdefault("PYTHONHASHSEED", "0")

# Seed Python RNG (used only in tests; consensus code should be pure)
DEFAULT_TEST_SEED = int(os.environ.get("ANIMICA_TEST_SEED", "1337"))
random.seed(DEFAULT_TEST_SEED)

# Try to seed NumPy if present (optional)
try:  # pragma: no cover - optional dependency
    import numpy as _np  # type: ignore

    _np.random.seed(DEFAULT_TEST_SEED)
except Exception:
    pass

# Hypothesis defaults: faster local runs, deeper CI runs
try:  # pragma: no cover - optional dependency
    from hypothesis import settings

    # Local: fewer examples for snappy feedback; no global deadline to avoid flakiness on CI
    settings.register_profile("local", settings(max_examples=60, deadline=None))
    # CI: more coverage
    settings.register_profile("ci", settings(max_examples=200, deadline=None))
    # Pick profile by env, default to local unless CI is set
    _profile = os.environ.get("HYPOTHESIS_PROFILE") or ("ci" if os.environ.get("CI") else "local")
    settings.load_profile(_profile)
except Exception:
    pass

# ----- Fixture helpers -----
def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml  # PyYAML is in requirements-dev
    except Exception as e:  # pragma: no cover
        raise RuntimeError("PyYAML is required for test fixtures") from e
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def policy_example_path() -> Path:
    """Path to consensus/fixtures/poies_policy.example.yaml."""
    p = FIXTURES / "poies_policy.example.yaml"
    if not p.exists():  # Helpful error if tests are invoked from wrong CWD
        raise FileNotFoundError(f"Fixture missing: {p}")
    return p

def genesis_header_path() -> Path:
    """Path to consensus/fixtures/genesis_header.json."""
    p = FIXTURES / "genesis_header.json"
    if not p.exists():
        raise FileNotFoundError(f"Fixture missing: {p}")
    return p

def load_policy_example() -> Dict[str, Any]:
    """Load the small test policy used across consensus unit tests."""
    return _load_yaml(policy_example_path())

def load_genesis_header() -> Dict[str, Any]:
    """Load the tiny deterministic genesis header sample."""
    return _load_json(genesis_header_path())

# Export a small, typed surface for tests to import
__all__ = [
    "ROOT",
    "PKG_ROOT",
    "FIXTURES",
    "DEFAULT_TEST_SEED",
    "policy_example_path",
    "genesis_header_path",
    "load_policy_example",
    "load_genesis_header",
]
