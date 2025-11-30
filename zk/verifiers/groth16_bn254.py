"""
Animica zk.verifiers.groth16_bn254
==================================

Groth16 verifier for BN254 (altbn128), compatible with the common
`snarkjs` JSON layout.

Verification equation (standard form)
-------------------------------------
    e(A, B) == e(alpha1, beta2) * e(VK_x, gamma2) * e(C, delta2)

We implement this as a product check in GT:
    e(A, B) * e(-alpha1, beta2) * e(-VK_x, gamma2) * e(-C, delta2) == 1

JSON compatibility (snarkjs)
----------------------------
- Verifying key:
  {
    "vk_alpha_1": [ax, ay],
    "vk_beta_2": [[bx0, bx1], [by0, by1]],
    "vk_gamma_2": [[gx0, gx1], [gy0, gy1]],
    "vk_delta_2": [[dx0, dx1], [dy0, dy1]],
    "IC": [[ic0x, ic0y], [ic1x, ic1y], ...]   # length = 1 + #public_inputs
  }

- Proof:
  {
    "pi_a": [ax, ay],
    "pi_b": [[bx0, bx1], [by0, by1]],
    "pi_c": [cx, cy]
  }

All coordinates are decimal strings (or numbers). For G2, elements are Fq2
with the convention: c0 + c1 * i  is encoded as [c0, c1].

Public API
----------
- verify_groth16(vk_json: dict, proof_json: dict, inputs: list[int|str]) -> bool
- load_vk(vk_json: dict) -> VerifyingKey
- load_proof(proof_json: dict) -> Proof

Notes
-----
- We rely on `py_ecc` for curve arithmetic and our pairing wrapper for the
  product-of-pairings check.
- Inputs are reduced modulo the BN254 scalar field order.
- Points are validated to be on the curve before use.

License: MIT
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Sequence, Tuple, Union

# Pairing/product helpers and curve info
from .pairing_bn254 import (check_pairing_product, curve_order, is_on_curve_g1,
                            is_on_curve_g2)

# py_ecc backends (optimized preferred)
try:  # optimized
    from py_ecc.optimized_bn128 import FQ, FQ2
    from py_ecc.optimized_bn128 import add as _add  # type: ignore
    from py_ecc.optimized_bn128 import b as _B
    from py_ecc.optimized_bn128 import b2 as _B2
    from py_ecc.optimized_bn128 import is_on_curve as _is_on_curve
    from py_ecc.optimized_bn128 import multiply as _mul
    from py_ecc.optimized_bn128 import neg as _neg

    _BACKEND = "py_ecc.optimized_bn128"
except Exception:  # pragma: no cover
    from py_ecc.bn128 import FQ, FQ2
    from py_ecc.bn128 import add as _add  # type: ignore
    from py_ecc.bn128 import b as _B
    from py_ecc.bn128 import b2 as _B2
    from py_ecc.bn128 import is_on_curve as _is_on_curve
    from py_ecc.bn128 import multiply as _mul
    from py_ecc.bn128 import neg as _neg

    _BACKEND = "py_ecc.bn128"

# Types the backend understands (opaque tuples)
G1Point = Any
G2Point = Any


# ---------------------------
# Utilities
# ---------------------------

_FR = int(curve_order())


def _to_int(z: Union[int, str]) -> int:
    if isinstance(z, int):
        return z
    s = str(z).strip().lower()
    if s.startswith("0x"):
        return int(s, 16)
    return int(s)


def _fr(z: Union[int, str]) -> int:
    return _to_int(z) % _FR


def _g1(x: Union[int, str], y: Union[int, str]) -> G1Point:
    xi, yi = _to_int(x), _to_int(y)
    # Infinity convention sometimes appears as [0,0]
    if xi == 0 and yi == 0:
        return (FQ(1), FQ(1), FQ(0))  # projective infinity (z=0)
    return (FQ(xi), FQ(yi), FQ(1))  # Jacobian/projective


def _g2(xx: Sequence[Union[int, str]], yy: Sequence[Union[int, str]]) -> G2Point:
    # Expect xx = [x_c0, x_c1], yy = [y_c0, y_c1]
    x0, x1 = _to_int(xx[0]), _to_int(xx[1])
    y0, y1 = _to_int(yy[0]), _to_int(yy[1])
    if x0 == 0 and x1 == 0 and y0 == 0 and y1 == 0:
        return (FQ2([1, 0]), FQ2([1, 0]), FQ2([0, 0]))  # infinity (z=0)
    return (FQ2([x0, x1]), FQ2([y0, y1]), FQ2([1, 0]))


def _on_curve_g1(P: G1Point) -> bool:
    try:
        return bool(_is_on_curve(P, _B))
    except TypeError:
        return bool(_is_on_curve(P))


def _on_curve_g2(Q: G2Point) -> bool:
    try:
        return bool(_is_on_curve(Q, _B2))
    except TypeError:
        return bool(_is_on_curve(Q))


# ---------------------------
# Data classes
# ---------------------------


@dataclass(frozen=True)
class VerifyingKey:
    alpha1: G1Point
    beta2: G2Point
    gamma2: G2Point
    delta2: G2Point
    IC: List[G1Point]  # [IC0, IC1, ..., ICn]


@dataclass(frozen=True)
class Proof:
    A: G1Point
    B: G2Point
    C: G1Point


# ---------------------------
# Loaders (snarkjs JSON)
# ---------------------------


def load_vk(vk_json: dict) -> VerifyingKey:
    """
    Parse a snarkjs-style verifying key JSON object into a VerifyingKey.
    """
    # Accept both common keys and relaxed names
    a1 = vk_json.get("vk_alpha_1") or vk_json.get("alpha_1") or vk_json["alpha1"]
    b2 = vk_json.get("vk_beta_2") or vk_json.get("beta_2") or vk_json["beta2"]
    g2 = vk_json.get("vk_gamma_2") or vk_json.get("gamma_2") or vk_json["gamma2"]
    d2 = vk_json.get("vk_delta_2") or vk_json.get("delta_2") or vk_json["delta2"]
    IC = vk_json.get("IC") or vk_json.get("vk_ic") or vk_json["ic"]

    alpha1 = _g1(a1[0], a1[1])
    beta2 = _g2(b2[0], b2[1])
    gamma2 = _g2(g2[0], g2[1])
    delta2 = _g2(d2[0], d2[1])
    ic_pts = [_g1(x, y) for (x, y) in IC]

    # Basic sanity
    if not (
        _on_curve_g1(alpha1)
        and _on_curve_g2(beta2)
        and _on_curve_g2(gamma2)
        and _on_curve_g2(delta2)
    ):
        raise ValueError("VK points are not on curve")
    for P in ic_pts:
        if not _on_curve_g1(P):
            raise ValueError("IC point not on G1 curve")

    return VerifyingKey(
        alpha1=alpha1, beta2=beta2, gamma2=gamma2, delta2=delta2, IC=ic_pts
    )


def load_proof(proof_json: dict) -> Proof:
    """
    Parse a snarkjs-style proof JSON object into a Proof.
    """
    A = proof_json.get("pi_a") or proof_json.get("A")
    B = proof_json.get("pi_b") or proof_json.get("B")
    C = proof_json.get("pi_c") or proof_json.get("C")

    A1 = _g1(A[0], A[1])
    B2 = _g2(B[0], B[1])
    C1 = _g1(C[0], C[1])

    # Sanity
    if not (_on_curve_g1(A1) and _on_curve_g2(B2) and _on_curve_g1(C1)):
        raise ValueError("Proof points are not on curve")

    return Proof(A=A1, B=B2, C=C1)


# ---------------------------
# Core verification
# ---------------------------


def _vk_x(IC: Sequence[G1Point], inputs: Sequence[Union[int, str]]) -> G1Point:
    """
    Compute VK_x = IC[0] + sum_i inputs[i] * IC[i+1]  in G1.
    """
    if len(IC) != len(inputs) + 1:
        raise ValueError(f"IC length {len(IC)} != 1 + len(inputs) {len(inputs)}")
    acc = IC[0]
    for i, v in enumerate(inputs):
        s = _fr(v)
        if s != 0:
            acc = _add(acc, _mul(IC[i + 1], s))
    return acc


def verify_groth16(
    vk_json: dict,
    proof_json: dict,
    public_inputs: Sequence[Union[int, str]],
) -> bool:
    """
    Verify a Groth16 proof given snarkjs-style VK/Proof JSON and public inputs.

    Returns True on success, False otherwise (no exceptions for routine failures).
    """
    try:
        vk = load_vk(vk_json)
        pf = load_proof(proof_json)
        vkx = _vk_x(vk.IC, public_inputs)

        # Product check:
        # e(A, B) * e(-alpha1, beta2) * e(-VK_x, gamma2) * e(-C, delta2) == 1
        pairs = [
            (pf.A, pf.B),
            (_neg(vk.alpha1), vk.beta2),
            (_neg(vkx), vk.gamma2),
            (_neg(pf.C), vk.delta2),
        ]
        return check_pairing_product(pairs)
    except Exception:
        # For security-sensitive contexts, consider logging the specific reason.
        return False


# ---------------------------
# Self-test (optional smoke)
# ---------------------------

if __name__ == "__main__":  # pragma: no cover
    import json
    import sys

    print(f"[groth16_bn254] backend={_BACKEND}")
    if len(sys.argv) == 3:
        # Quick CLI: python groth16_bn254.py vk.json proof.json  (reads public inputs from proof.json if present)
        with open(sys.argv[1], "r") as f:
            vk = json.load(f)
        with open(sys.argv[2], "r") as f:
            proof_obj = json.load(f)
        inputs = proof_obj.get("publicSignals") or proof_obj.get("inputs") or []
        ok = verify_groth16(
            vk, proof_obj["proof"] if "proof" in proof_obj else proof_obj, inputs
        )
        print("verify ->", ok)
    else:
        print(
            "Run with: python zk/verifiers/groth16_bn254.py path/to/vk.json path/to/proof.json"
        )
