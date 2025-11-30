"""
Animica zk.adapters
===================

Thin, friendly adapters that expose a **uniform entrypoint** to our verifier
implementations in `zk.verifiers.*`. This module gives you:

- Simple `verify("<name>", vk, proof, public=...) -> bool` dispatch.
- Concrete helpers for popular schemes:
    * Groth16 on BN254 (snarkjs-compatible JSON)
    * PLONK with KZG (BN254) — final single-opening pairing check
    * Raw KZG opening (BN254) — JSON wrapper → group types
    * A tiny educational STARK (Toy Merkle AIR + mini FRI)
- Re-exports of useful primitives (Merkle, Poseidon, Transcript).

This file contains **no heavy logic**—it mostly wires JSON-y inputs into the
lower-level verifiers and hosts a registry you can extend.

License: MIT
"""

from __future__ import annotations

from typing import (Any, Callable, Dict, Mapping, MutableMapping, Optional,
                    Sequence, Tuple, Union)

# Groth16 (BN254), PLONK(KZG), STARK(Toy Merkle)
from ..verifiers.groth16_bn254 import \
    verify_groth16 as _verify_groth16_bn254  # type: ignore
# KZG core + curve utils
from ..verifiers.kzg_bn254 import VerifyingKey as _KZGVK  # type: ignore
from ..verifiers.kzg_bn254 import kzg_verify as _kzg_verify
# Merkle utilities
from ..verifiers.merkle import (blake2s_256, merkle_root, merkle_verify,
                                sha2_256, sha3_256)
from ..verifiers.pairing_bn254 import curve_order as _curve_order
from ..verifiers.pairing_bn254 import g1_generator as _g1_gen  # type: ignore
from ..verifiers.pairing_bn254 import g2_generator as _g2_gen
from ..verifiers.plonk_kzg_bn254 import \
    verify_plonk_kzg as _verify_plonk_kzg_bn254  # type: ignore
# Poseidon + Fiat–Shamir transcript
from ..verifiers.poseidon import Poseidon, poseidon_hash  # type: ignore
from ..verifiers.stark_fri import \
    verify_toy_stark_merkle as _verify_stark_toy_merkle  # type: ignore
from ..verifiers.transcript_fs import Transcript  # type: ignore

# --- Re-export user-facing helpers from verifiers ----------------------------





# py_ecc field/point builders (optimized preferred)
try:  # pragma: no cover
    from py_ecc.optimized_bn128 import FQ, FQ2
    from py_ecc.optimized_bn128 import add as _add  # type: ignore
    from py_ecc.optimized_bn128 import is_on_curve as _is_on_curve

    _BACKEND = "py_ecc.optimized_bn128"
except Exception:  # pragma: no cover
    from py_ecc.bn128 import FQ, FQ2
    from py_ecc.bn128 import add as _add  # type: ignore
    from py_ecc.bn128 import is_on_curve as _is_on_curve

    _BACKEND = "py_ecc.bn128"


# -----------------------------------------------------------------------------
# JSON → group/field parsing helpers (local, tiny)
# -----------------------------------------------------------------------------

_Fr = int(_curve_order())


def _to_int(z: Union[int, str]) -> int:
    if isinstance(z, int):
        return z
    s = str(z).strip().lower()
    if s.startswith("0x"):
        return int(s, 16)
    return int(s)


def _fr(z: Union[int, str]) -> int:
    return _to_int(z) % _Fr


def _g1_xy(pt: Sequence[Union[int, str]]) -> tuple:
    if not (isinstance(pt, (list, tuple)) and len(pt) == 2):
        raise ValueError("G1 point must be [x, y]")
    x, y = _to_int(pt[0]), _to_int(pt[1])
    if x == 0 and y == 0:
        # infinity (Jacobian z=0 convention)
        return (FQ(1), FQ(1), FQ(0))
    return (FQ(x), FQ(y), FQ(1))


def _g2_xy(pt: Sequence[Sequence[Union[int, str]]]) -> tuple:
    if not (
        isinstance(pt, (list, tuple))
        and len(pt) == 2
        and len(pt[0]) == 2
        and len(pt[1]) == 2
    ):
        raise ValueError("G2 point must be [[x0,x1],[y0,y1]]")
    x = FQ2([_to_int(pt[0][0]), _to_int(pt[0][1])])
    y = FQ2([_to_int(pt[1][0]), _to_int(pt[1][1])])
    # infinity
    if int(x.coeffs[0]) == 0 and int(x.coeffs[1]) == 0 and int(y.coeffs[0]) == 0 and int(y.coeffs[1]) == 0:  # type: ignore[attr-defined]
        return (FQ2([1, 0]), FQ2([1, 0]), FQ2([0, 0]))
    return (x, y, FQ2([1, 0]))


# -----------------------------------------------------------------------------
# KZG opening (BN254) — JSON adapter
# -----------------------------------------------------------------------------


def verify_kzg_opening_bn254_json(
    vk_json: Mapping[str, Any],
    opening_json: Mapping[str, Any],
    *,
    validate_points: bool = True,
) -> bool:
    """
    Verify a *single-point KZG opening* over BN254 with JSON-shaped inputs.

    vk_json:
      { "s_g2": [[sx0,sx1],[sy0,sy1]] }

    opening_json (aliases tolerated):
      {
        "commitment": [cx,cy],            # aka "C"
        "z": "<int|0x>",                  # eval point  (aka "x")
        "value": "<int|0x>",              # evaluated y (aka "y", "v")
        "proof": [px,py]                  # aka "opening_proof", "pi"
      }

    Returns True/False.
    """
    try:
        s2 = vk_json.get("s_g2") or vk_json.get("tau_g2") or vk_json.get("sg2")
        if s2 is None:
            raise ValueError("vk_json missing 's_g2'")
        vk = _KZGVK(g1=_g1_gen(), g2=_g2_gen(), s_g2=_g2_xy(s2))  # type: ignore[arg-type]

        Cj = opening_json.get("commitment") or opening_json.get("C")
        zj = opening_json.get("z") or opening_json.get("x")
        vj = opening_json.get("value") or opening_json.get("y") or opening_json.get("v")
        pij = (
            opening_json.get("proof")
            or opening_json.get("opening_proof")
            or opening_json.get("pi")
        )
        if Cj is None or zj is None or vj is None or pij is None:
            return False

        C = _g1_xy(Cj)
        pi = _g1_xy(pij)
        z = _fr(zj)
        v = _fr(vj)

        # Optional curve checks
        if validate_points:
            if not _is_on_curve(
                C, b"\x00"
            ):  # py_ecc ignores this 'b' arg for optimized; harmless
                return False
            if not _is_on_curve(pi, b"\x00"):
                return False

        return bool(_kzg_verify(C, z, v, pi, vk, validate=True))
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Scheme-specific wrappers (stable names)
# -----------------------------------------------------------------------------


def verify_groth16_bn254(
    vk_json: Mapping[str, Any],
    proof_json: Mapping[str, Any],
    public_inputs: Sequence[Union[int, str]] = (),
) -> bool:
    """Groth16(BN254) — snarkjs-compatible JSON verifier."""
    try:
        return bool(_verify_groth16_bn254(vk_json, proof_json, public_inputs))  # type: ignore[arg-type]
    except Exception:
        return False


def verify_plonk_kzg_bn254(
    vk_json: Mapping[str, Any],
    proof_json: Mapping[str, Any],
    public_inputs: Sequence[Union[int, str]] = (),
) -> bool:
    """PLONK(KZG, BN254) — final single-opening pairing check (see module doc for scope)."""
    try:
        return bool(_verify_plonk_kzg_bn254(vk_json, proof_json, public_inputs))  # type: ignore[arg-type]
    except Exception:
        return False


def verify_stark_toy_merkle(
    proof: Mapping[str, Any], public: Mapping[str, Any], *, with_fri: bool = True
) -> bool:
    """Tiny STARK (Toy Merkle AIR + minimal FRI) — **educational** only."""
    try:
        return bool(_verify_stark_toy_merkle(proof, public, with_fri=with_fri))
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Registry + generic dispatch
# -----------------------------------------------------------------------------

VerifierFn = Callable[..., bool]

VERIFIERS: Dict[str, VerifierFn] = {
    # Pairing-based
    "groth16-bn254": verify_groth16_bn254,
    "plonk-kzg-bn254": verify_plonk_kzg_bn254,
    "kzg-opening-bn254": verify_kzg_opening_bn254_json,
    # STARK (toy)
    "stark-toy-merkle": verify_stark_toy_merkle,
}


def resolve(name: str) -> VerifierFn:
    """
    Resolve a verifier by registry key.
    Raises KeyError if unknown.
    """
    key = name.strip().lower()
    if key not in VERIFIERS:
        raise KeyError(f"unknown verifier '{name}'")
    return VERIFIERS[key]


def verify(name: str, *args: Any, **kwargs: Any) -> bool:
    """
    Generic one-liner:
        ok = verify("groth16-bn254", vk_json, proof_json, public_inputs)
    """
    return bool(resolve(name)(*args, **kwargs))


# -----------------------------------------------------------------------------
# Public exports
# -----------------------------------------------------------------------------

__all__ = [
    # Dispatch
    "verify",
    "resolve",
    "VERIFIERS",
    # Specific schemes
    "verify_groth16_bn254",
    "verify_plonk_kzg_bn254",
    "verify_kzg_opening_bn254_json",
    "verify_stark_toy_merkle",
    # Primitives
    "merkle_verify",
    "merkle_root",
    "sha3_256",
    "sha2_256",
    "blake2s_256",
    "Poseidon",
    "poseidon_hash",
    "Transcript",
]
