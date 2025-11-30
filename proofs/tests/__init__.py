"""
Common helpers for the proofs/ test-suite.

Import from tests like:

    from proofs.tests import data_path, fixture_path, read_json, liboqs_available

This module deliberately avoids heavy deps so it can be imported very early.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

# Directories
THIS_FILE = Path(__file__).resolve()
TESTS_DIR = THIS_FILE.parent
PROOFS_DIR = TESTS_DIR.parent
VECTORS_DIR = PROOFS_DIR / "test_vectors"
FIXTURES_DIR = PROOFS_DIR / "fixtures"
SCHEMAS_DIR = PROOFS_DIR / "schemas"
CLI_DIR = PROOFS_DIR / "cli"

__all__ = [
    "PROOFS_DIR",
    "VECTORS_DIR",
    "FIXTURES_DIR",
    "SCHEMAS_DIR",
    "data_path",
    "fixture_path",
    "schema_path",
    "read_json",
    "read_bytes",
    "liboqs_available",
    "skip_if_no_liboqs",
    "require_env",
]

# ---------- path helpers ----------


def data_path(*parts: str) -> Path:
    """Return an absolute path under test_vectors/."""
    return (VECTORS_DIR / Path(*parts)).resolve()


def fixture_path(*parts: str) -> Path:
    """Return an absolute path under fixtures/."""
    return (FIXTURES_DIR / Path(*parts)).resolve()


def schema_path(name: str) -> Path:
    """Return an absolute path to a schema under schemas/."""
    return (SCHEMAS_DIR / name).resolve()


# ---------- file readers ----------


def read_json(path: Path | str) -> Any:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_bytes(path: Path | str) -> bytes:
    p = Path(path)
    with p.open("rb") as f:
        return f.read()


# ---------- environment helpers ----------


def liboqs_available() -> bool:
    """
    Best-effort check for liboqs backend availability (used by optional PQ paths).
    Returns False if the module or library cannot be loaded.
    """
    try:
        # Prefer our wrapper if present.
        from pq.py.algs.oqs_backend import load as _oqs_load  # type: ignore
    except Exception:
        return False
    try:
        return _oqs_load() is not None
    except Exception:
        return False


def skip_if_no_liboqs(pytest) -> None:
    """pytest helper: skip test if liboqs is not available."""
    if not liboqs_available():
        pytest.skip("liboqs backend not available (optional)")


def require_env(key: str, default: Optional[str] = None) -> str:
    """
    Fetch an env var with a helpful error if missing and no default is provided.
    Useful for tests that rely on external paths/keys (rare in this module).
    """
    val = os.getenv(key, default)
    if val is None:
        raise RuntimeError(f"Required environment variable {key!r} is not set")
    return val


# Make sure canonical directories exist when running tests in isolation
for _d in (VECTORS_DIR, FIXTURES_DIR, SCHEMAS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
