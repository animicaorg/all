"""
animica_native
==============

Thin Python wrapper around the compiled PyO3 extension module ``_animica_native``.
This package exposes a stable, Pythonic surface while keeping the heavy lifting
in optimized Rust (with optional C fastpaths).

What you get (if the native module was built with all submodules):
- ``hash``:     BLAKE3, Keccak-256, SHA-256 (bytes-in → bytes-out), streaming APIs
- ``nmt``:      Namespaced Merkle Tree helpers (root/open/verify)
- ``rs``:       Reed–Solomon encode/reconstruct helpers
- ``utils``:    Zero-copy buffer conversions and misc utilities
- ``cpu``:      CPU feature discovery (AVX2/SHA/NEON/etc.)
- ``version``:  (function) returns (major, minor, patch)
- ``__version__``: semantic version string (e.g. "0.1.0")

This module is intentionally tolerant to slight changes in the native symbol
layout across builds. It detects available submodules at import time and
re-exports them when present.

Typical usage
-------------
    from animica_native import hash, nmt, rs, __version__

    digest = hash.blake3_hash(b"hello")   # -> 32 bytes
    root   = nmt.nmt_root([b"a", b"b"])   # available when built with NMT
    shards = rs.rs_encode(b"...", data_shards=10, parity_shards=4)
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Callable, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Load the native extension (built via maturin/pyo3)
# ---------------------------------------------------------------------------


def _load_native() -> Any:
    """
    Attempt to import the bundled extension module. On failure, raise an
    informative ImportError with actionable hints.
    """
    try:
        # Preferred: local package-relative import (wheel layout).
        return import_module(".\x5fanimica_native", package=__name__)
    except Exception as exc:
        # Fallback: direct import (editable/dev installs sometimes flatten it).
        try:
            return import_module("_animica_native")
        except Exception:
            msg = (
                "Failed to import native extension '_animica_native'.\n"
                "Build it with:\n"
                "  - Python wheels:    make -C native wheel  (or)  maturin build -m native/Cargo.toml\n"
                "  - Dev editable:     maturin develop -m native/Cargo.toml\n"
                "Ensure your Python interpreter matches the wheel you built (ABI/platform)."
            )
            raise ImportError(msg) from exc


_native = _load_native()

# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def _probe(attr_chain: Tuple[str, ...]) -> Optional[Any]:
    """Safely walk an attribute chain on the native module, or return None."""
    obj: Any = _native
    for name in attr_chain:
        if not hasattr(obj, name):
            return None
        obj = getattr(obj, name)
    return obj


def version() -> Tuple[int, int, int]:
    """
    Return semantic version (major, minor, patch) of the native core.

    This tries multiple symbol shapes for robustness:
      - _animica_native.version() -> (major, minor, patch)
      - _animica_native.version_tuple() -> (major, minor, patch)
      - _animica_native.version_string() -> "x.y.z"
    """
    # Common forms exported by PyO3 modules
    for cand in ("version", "version_tuple"):
        fn = _probe((cand,))
        if callable(fn):
            tup = tuple(fn())  # type: ignore[arg-type]
            if len(tup) == 3:
                return (int(tup[0]), int(tup[1]), int(tup[2]))
    s = _probe(("version_string",))
    if callable(s):
        text = str(s())
        parts = text.strip().split(".")
        if len(parts) >= 3:
            try:
                return (int(parts[0]), int(parts[1]), int(parts[2]))
            except ValueError:
                pass
    # Last resort
    return (0, 0, 0)


def _version_string_from_tuple(t: Tuple[int, int, int]) -> str:
    return f"{t[0]}.{t[1]}.{t[2]}"


# Prefer native-provided string if available; otherwise derive from tuple.
_version_str_fn = _probe(("version_string",))
if callable(_version_str_fn):
    __version__: str = str(_version_str_fn())
else:
    __version__ = _version_string_from_tuple(version())

# ---------------------------------------------------------------------------
# Re-export available native submodules (hash/nmt/rs/utils/cpu)
# ---------------------------------------------------------------------------

# We bind names conditionally so "from animica_native import nmt" works
# only if the submodule exists in the built artifact.
hash: Any = _probe(("hash",)) or _native  # some builds put hash fns at top-level
nmt: Optional[Any] = _probe(("nmt",))
rs: Optional[Any] = _probe(("rs",))
utils: Optional[Any] = _probe(("utils",))
cpu: Optional[Any] = _probe(("cpu",))

# ---------------------------------------------------------------------------
# Friendly CPU feature accessor (maps to a dict) if available.
# ---------------------------------------------------------------------------


def cpu_features() -> Dict[str, bool]:
    """
    Return CPU feature flags as a dict. If the native module does not expose
    a CPU feature API, returns an empty dict.

    Expected keys when available (all booleans):
      - x86_avx2, x86_sha, arm_neon, arm_sha3
    """
    # Try a few potential symbol layouts.
    candidates: Tuple[Tuple[str, ...], ...] = (
        ("cpu", "features"),
        ("cpu", "get_features"),
        ("utils", "cpu_features"),
        ("cpu_features",),
    )
    for chain in candidates:
        fn = _probe(chain)
        if callable(fn):
            feat = fn()
            # Normalize to dict (PyO3 may return a dataclass-like object or dict)
            if isinstance(feat, dict):
                return {str(k): bool(v) for k, v in feat.items()}
            # Fallback: introspect attrs
            out: Dict[str, bool] = {}
            for key in ("x86_avx2", "x86_sha", "arm_neon", "arm_sha3"):
                out[key] = bool(getattr(feat, key, False))
            return out
    return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "__version__",
    "version",
    "hash",
    "nmt",
    "rs",
    "utils",
    "cpu",
    "cpu_features",
]


# Helpful runtime note (visible in repr)
def __repr__() -> str:  # type: ignore[override]
    subs = []
    for name in ("hash", "nmt", "rs", "utils", "cpu"):
        subs.append(f"{name}={'yes' if globals().get(name) is not None else 'no'}")
    submods = ", ".join(subs)
    return f"<animica_native {__version__} ({submods})>"
