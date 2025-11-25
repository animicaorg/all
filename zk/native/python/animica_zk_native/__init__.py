# Copyright (c) Animica
# SPDX-License-Identifier: Apache-2.0
"""
animica_zk_native (Python wrapper)

Thin convenience wrapper that tries to import the optional native accelerator
module (built with pyo3) and falls back to pure-Python implementations living
under `zk.verifiers.*` when the native extension is unavailable.

Environment toggles:
- ZK_DISABLE_NATIVE=1  → skip loading the native extension (force Python path)
- ZK_FORCE_PYECC=1     → advisory flag surfaced in version_info (no behavior change)

Exposed functions (stable API):
- available() -> dict[str, bool]
- version_info() -> dict[str, Any]
- pairing_product_check_bytes(pairs: list[tuple[bytes, bytes]]) -> bool
- kzg_verify_opening_bytes(commit_g1, proof_g1, z_fr, y_fr, g2_gen, g2_tau) -> bool
- sizes() -> dict[str,int]   # only when native is present; raises otherwise
"""

from __future__ import annotations

import os
import importlib
from typing import Any, Iterable, List, Tuple

_NATIVE_DISABLED = os.getenv("ZK_DISABLE_NATIVE", "0") == "1"

_core = None  # the compiled extension if present
_load_errors: list[str] = []

if not _NATIVE_DISABLED:
    # Try in-package module name first: animica_zk_native._native (preferred layout)
    try:
        _core = importlib.import_module(".\x5fnative", __name__)
    except Exception as e:  # noqa: BLE001
        _load_errors.append(f"in-package _native not found: {e!r}")
        # Try flat extension name (wheel that ships a top-level module)
        try:
            _core = importlib.import_module("animica_zk_native")  # type: ignore[assignment]
        except Exception as e2:  # noqa: BLE001
            _load_errors.append(f"top-level extension not found: {e2!r}")

# -------- helpers (public API) ------------------------------------------------


def _fallback_pairing_product_check_bytes(
    pairs: Iterable[Tuple[bytes, bytes]],
) -> bool:
    """
    Fallback to the pure-Python pairing checker (py_ecc backend).
    Tries both byte-oriented and object-oriented functions.
    """
    try:
        from zk.verifiers import pairing_bn254 as _pp  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "pairing fallback unavailable: zk.verifiers.pairing_bn254 not importable"
        ) from e

    fn = getattr(_pp, "pairing_product_check_bytes", None) or getattr(
        _pp, "pairing_product_check", None
    )
    if fn is None:
        raise RuntimeError(
            "pairing fallback missing function pairing_product_check(_bytes)"
        )
    return bool(fn(list(pairs)))  # type: ignore[misc]


def _fallback_kzg_verify_opening_bytes(
    commit_g1: bytes,
    proof_g1: bytes,
    z_fr: bytes,
    y_fr: bytes,
    g2_gen: bytes,
    g2_tau: bytes,
) -> bool:
    """
    Fallback to the pure-Python KZG verifier.
    """
    try:
        from zk.verifiers import kzg_bn254 as _kzg  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "KZG fallback unavailable: zk.verifiers.kzg_bn254 not importable"
        ) from e

    fn = getattr(_kzg, "kzg_verify_opening_bytes", None) or getattr(
        _kzg, "kzg_verify_opening", None
    )
    if fn is None:
        raise RuntimeError(
            "KZG fallback missing function kzg_verify_opening(_bytes)"
        )
    return bool(
        fn(commit_g1, proof_g1, z_fr, y_fr, g2_gen, g2_tau)  # type: ignore[misc]
    )


def available() -> dict:
    """
    Report which fast paths are available. If the native extension is not
    importable (or disabled), returns all False.

    Example:
        {'pairing': True, 'kzg': True, 'python': True, 'parallel': False}
    """
    if _core is not None and hasattr(_core, "available"):
        try:
            return dict(_core.available())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            # fall through to conservative report
            pass
    return {"pairing": False, "kzg": False, "python": False, "parallel": False}


def version_info() -> dict[str, Any]:
    """
    Build/runtime diagnostics. When native is missing, returns a minimal stub
    indicating Python-only mode and the reasons (if any) why native failed.
    """
    info: dict[str, Any] = {
        "crate": "animica_zk_native",
        "python_only": _core is None,
        "env": {
            "ZK_DISABLE_NATIVE": os.getenv("ZK_DISABLE_NATIVE", ""),
            "ZK_FORCE_PYECC": os.getenv("ZK_FORCE_PYECC", ""),
        },
    }
    if _core is not None and hasattr(_core, "version_info"):
        try:
            core_info = dict(_core.version_info())  # type: ignore[attr-defined]
            info.update(core_info)
        except Exception as e:  # noqa: BLE001
            info["native_error"] = repr(e)
    else:
        info["native_load_errors"] = list(_load_errors)
    return info


def pairing_product_check_bytes(
    pairs: Iterable[Tuple[bytes, bytes]],
) -> bool:
    """
    True iff ∏ e(P_i, Q_i) == 1 in GT, with inputs as canonical uncompressed
    ark-serialize bytes for (G1Affine, G2Affine) pairs.

    Falls back to pure-Python backend if native is not available.
    """
    if _core is not None and hasattr(_core, "pairing_product_check_bytes_py"):
        return bool(_core.pairing_product_check_bytes_py(list(pairs)))  # type: ignore[attr-defined]
    return _fallback_pairing_product_check_bytes(pairs)


def kzg_verify_opening_bytes(
    commit_g1: bytes,
    proof_g1: bytes,
    z_fr: bytes,
    y_fr: bytes,
    g2_gen: bytes,
    g2_tau: bytes,
) -> bool:
    """
    Minimal KZG single-opening verification over BN254 using canonical
    uncompressed encodings. Falls back to pure-Python path if native is not
    available.
    """
    if _core is not None and hasattr(_core, "kzg_verify_opening_bytes_py"):
        return bool(
            _core.kzg_verify_opening_bytes_py(
                commit_g1, proof_g1, z_fr, y_fr, g2_gen, g2_tau
            )  # type: ignore[attr-defined]
        )
    return _fallback_kzg_verify_opening_bytes(
        commit_g1, proof_g1, z_fr, y_fr, g2_gen, g2_tau
    )


def sizes() -> dict[str, int]:
    """
    Introspect canonical ark-serialize sizes for G1/G2/Fr (native only).
    Raises RuntimeError if native is not present.
    """
    if _core is None or not hasattr(_core, "sizes"):
        raise RuntimeError("sizes() is only available with native extension")
    return dict(_core.sizes())  # type: ignore[attr-defined]


# Convenience banner for quick smoke-tests.
BANNER = (
    getattr(_core, "BANNER", None)
    if _core is not None
    else b"animica_zk_native (python fallback)"
)

__all__ = [
    "available",
    "version_info",
    "pairing_product_check_bytes",
    "kzg_verify_opening_bytes",
    "sizes",
    "BANNER",
]
