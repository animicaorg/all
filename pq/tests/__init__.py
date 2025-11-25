"""
Animica PQ test package bootstrap.

- Sets deterministic-friendly defaults where safe.
- Detects optional native backends (liboqs) and exposes flags for tests.
- Provides tiny helpers to load local test vectors.

Tests can import:

    from pq.tests import HAS_LIBOQS, FORCE_PURE_PY, load_vectors, skip_if_no_oqs
"""

from __future__ import annotations
import os
import json
import warnings
from pathlib import Path

# ---- Environment knobs -------------------------------------------------------

# Allow forcing pure-Python fallbacks (slow, but CI-portable).
FORCE_PURE_PY: bool = os.getenv("ANIMICA_PQ_PURE_PY", "").lower() in {"1", "true", "yes"}

# Make hashing randomized seed reproducible if the test runner didn't set it.
os.environ.setdefault("PYTHONHASHSEED", "0")

# Quiet overly-noisy deprecation warnings in third-party libs during tests.
warnings.filterwarnings("ignore", category=DeprecationWarning)

# ---- Native backend detection ------------------------------------------------

def _has_liboqs() -> bool:
    """
    Best-effort detection of a usable liboqs backend for tests.
    We try both Python wheels ("oqs") and a system lib via ctypes.
    """
    if FORCE_PURE_PY:
        return False
    try:
        # Prefer Python module if present (e.g., oqs-python wheel)
        import importlib.util as _util
        if _util.find_spec("oqs") is not None:
            return True
    except Exception:
        pass
    try:
        # Fallback: direct shared lib probe
        import ctypes, sys
        names = ["liboqs.so", "liboqs.dylib", "oqs.dll"]
        for n in names:
            try:
                ctypes.CDLL(n)
                return True
            except OSError:
                continue
        # Try common locations from env
        for env_var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "PATH"):
            for p in os.getenv(env_var, "").split(os.pathsep):
                if not p:
                    continue
                for n in names:
                    try:
                        ctypes.CDLL(str(Path(p) / n))
                        return True
                    except OSError:
                        continue
    except Exception:
        return False
    return False

HAS_LIBOQS: bool = _has_liboqs()

# Some tests may want to skip when liboqs is unavailable.
def skip_if_no_oqs():  # pragma: no cover - tiny helper
    try:
        import pytest  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("pytest is required for skip_if_no_oqs()") from e
    if not HAS_LIBOQS:
        pytest.skip("liboqs backend not available; skipping native-backed test")

# ---- Test vectors loader -----------------------------------------------------

_VECTORS_DIR = Path(__file__).resolve().parent.parent / "test_vectors"

def load_vectors(name: str) -> dict:
    """
    Load a JSON test-vector file from pq/test_vectors/{name}.json
    and return its parsed dict. Raises FileNotFoundError on miss.
    """
    path = _VECTORS_DIR / f"{name}.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)

# ---- Pretty banner (optional) -----------------------------------------------

def _banner() -> str:
    mode = "pure-python" if FORCE_PURE_PY or not HAS_LIBOQS else "liboqs-native"
    return f"[pq.tests] backend={mode} PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED','?')}"

# Print once at import to aid CI logs.
print(_banner())
