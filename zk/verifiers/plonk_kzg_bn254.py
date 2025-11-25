"""
Animica zk.verifiers.plonk_kzg_bn254
====================================

PLONK (KZG) — single-opening pairing check (demo).

What this does
--------------
This module implements the **final KZG opening check** that appears at the end
of a PLONK verifier: after the verifier derives Fiat–Shamir challenges and
(linearizes/aggregates) all polynomial commitments and evaluations into a single
commitment `C_agg` and a single value `v_agg` at point `z`, it must verify:

    e(C_agg - v_agg * G1, G2) == e(π, s*G2 - z * G2)

We expose a compact, production-usable helper that:
- Consumes a KZG verifying key (needs only `s*G2`).
- Accepts either:
  (A) an already-aggregated commitment/value in the proof JSON, or
  (B) a list of per-polynomial commitments/evaluations and **derives a random
      linear combination using a Fiat–Shamir transcript** (challenge ρ) so the
      verifier only performs one pairing check.

⚠️ Scope note
-------------
This is a **demo** verifier: it *only* performs the single KZG opening check.
It does **not** reconstruct the PLONK linearization polynomial or constraint
identities from gates/selector commitments. Use this as the final step once
you already have (C_agg, v_agg), or as a template to wire into your full
PLONK verifier.

JSON I/O (flexible)
-------------------
Verifying key JSON must contain the G2 element `s_g2`:

    {
      "s_g2": [[sx0, sx1], [sy0, sy1]]   // decimals or 0x hex strings
    }

Proof JSON can be one of:

(A) Aggregated form:
    {
      "agg_commitment": [cx, cy],        // G1
      "agg_value": "<int>",              // Fr
      "z": "<int>",                      // eval point
      "opening_proof": [px, py]          // G1
    }

(B) Expand-and-aggregate form (we derive ρ and aggregate in verifier):
    {
      "commitments": { "A": [ax, ay], "Z": [zx, zy], ... },  // name -> G1
      "evaluations": { "A": "<int>", "Z": "<int>", ... },    // name -> Fr
      "z": "<int>",
      "opening_proof": [px, py]
    }

Public API
----------
- verify_plonk_kzg(vk_json: dict, proof_json: dict, public_inputs: list[int|str] = []) -> bool

It returns True/False and never raises for ordinary verification failures.

Dependencies
------------
- `py_ecc` for BN254 arithmetic (optimized backend preferred).
- Our local helpers:
    - zk.verifiers.kzg_bn254 (pairing check)
    - zk.verifiers.transcript_fs (Fiat–Shamir, Poseidon)
    - zk.verifiers.pairing_bn254 (curve utils & generators)

License: MIT
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple, Union

# KZG verify + VK type
from .kzg_bn254 import VerifyingKey as KZGVK, kzg_verify
# Fiat–Shamir transcript (Poseidon-based)
from .transcript_fs import Transcript
# Curve helpers (generators, curve order, on-curve)
from .pairing_bn254 import (
    g1_generator,
    g2_generator,
    is_on_curve_g1,
    is_on_curve_g2,
    curve_order,
)

# py_ecc (optimized preferred)
try:
    from py_ecc.optimized_bn128 import (  # type: ignore
        FQ, FQ2, add as _add, neg as _neg, multiply as _mul, is_on_curve as _is_on_curve, b as _B, b2 as _B2
    )
    _BACKEND = "py_ecc.optimized_bn128"
except Exception:  # pragma: no cover
    from py_ecc.bn128 import (  # type: ignore
        FQ, FQ2, add as _add, neg as _neg, multiply as _mul, is_on_curve as _is_on_curve, b as _B, b2 as _B2
    )
    _BACKEND = "py_ecc.bn128"

# Types
G1Point = Any
G2Point = Any

# Field
_FR = int(curve_order())


# ---------------------------
# Parsing helpers
# ---------------------------

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
    if xi == 0 and yi == 0:
        return (FQ(1), FQ(1), FQ(0))  # infinity (z=0)
    return (FQ(xi), FQ(yi), FQ(1))

def _g2(xx: Sequence[Union[int, str]], yy: Sequence[Union[int, str]]) -> G2Point:
    x0, x1 = _to_int(xx[0]), _to_int(xx[1])
    y0, y1 = _to_int(yy[0]), _to_int(yy[1])
    if x0 == 0 and x1 == 0 and y0 == 0 and y1 == 0:
        return (FQ2([1, 0]), FQ2([1, 0]), FQ2([0, 0]))  # infinity
    return (FQ2([x0, x1]), FQ2([y0, y1]), FQ2([1, 0]))


# ---------------------------
# VK / Proof containers
# ---------------------------

@dataclass(frozen=True)
class VerifyingKey:
    g1: G1Point
    g2: G2Point
    s_g2: G2Point

def load_vk(vk_json: Mapping[str, object]) -> VerifyingKey:
    """
    Parse verifying key JSON for the KZG opening check.
    Expects at least 's_g2' as a G2 point: [[x0,x1],[y0,y1]].
    """
    g1 = g1_generator()
    g2 = g2_generator()

    s2 = vk_json.get("s_g2") or vk_json.get("tau_g2") or vk_json.get("sg2")
    if not isinstance(s2, (list, tuple)) or len(s2) != 2:
        raise ValueError("VK JSON must contain 's_g2' as [[x0,x1],[y0,y1]]")
    s_g2 = _g2(s2[0], s2[1])

    if not is_on_curve_g2(s_g2):
        raise ValueError("s_g2 not on G2 curve")

    return VerifyingKey(g1=g1, g2=g2, s_g2=s_g2)


@dataclass
class ProofDemo:
    # Either pre-aggregated:
    agg_commitment: Optional[G1Point]
    agg_value: Optional[int]
    # Or components to be aggregated in-verifier:
    commitments: Dict[str, G1Point]
    evaluations: Dict[str, int]
    # Common:
    z: int
    opening_proof: G1Point

def load_proof(proof_json: Mapping[str, object]) -> ProofDemo:
    """
    Parse proof JSON in either aggregated or expand-and-aggregate form.
    """
    # Required: z, opening_proof
    z_raw = proof_json.get("z") or proof_json.get("x") or proof_json.get("eval_point")
    if z_raw is None:
        raise ValueError("proof JSON missing 'z' (evaluation point)")
    z = _fr(z_raw)

    op = (proof_json.get("opening_proof")
          or proof_json.get("proof")
          or proof_json.get("w")
          or proof_json.get("W")
          or proof_json.get("Wz"))
    if not isinstance(op, (list, tuple)) or len(op) != 2:
        raise ValueError("proof JSON missing 'opening_proof' as [px, py]")
    opening_proof = _g1(op[0], op[1])
    if not is_on_curve_g1(opening_proof):
        raise ValueError("opening_proof not on G1 curve")

    # Optional pre-aggregated fields
    aggC_json = proof_json.get("agg_commitment") or proof_json.get("C_agg")
    aggV_json = proof_json.get("agg_value") or proof_json.get("v_agg")

    agg_commitment: Optional[G1Point] = None
    agg_value: Optional[int] = None
    if aggC_json is not None and aggV_json is not None:
        if not isinstance(aggC_json, (list, tuple)) or len(aggC_json) != 2:
            raise ValueError("agg_commitment must be [x, y]")
        agg_commitment = _g1(aggC_json[0], aggC_json[1])
        if not is_on_curve_g1(agg_commitment):
            raise ValueError("agg_commitment not on G1 curve")
        agg_value = _fr(aggV_json)

    # Expand-and-aggregate form
    commitments: Dict[str, G1Point] = {}
    evaluations: Dict[str, int] = {}

    comms_json = proof_json.get("commitments") or {}
    evals_json = proof_json.get("evaluations") or {}

    if isinstance(comms_json, Mapping):
        for name, pt in comms_json.items():
            if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                raise ValueError(f"commitment '{name}' must be [x, y]")
            P = _g1(pt[0], pt[1])
            if not is_on_curve_g1(P):
                raise ValueError(f"commitment '{name}' not on G1 curve")
            commitments[str(name)] = P

    if isinstance(evals_json, Mapping):
        for name, val in evals_json.items():
            evaluations[str(name)] = _fr(val)

    # If we have one of (commitments/evaluations), ensure the other matches.
    if commitments and not evaluations:
        raise ValueError("have commitments but missing evaluations")
    if evaluations and not commitments:
        raise ValueError("have evaluations but missing commitments")
    if commitments and (set(commitments.keys()) != set(evaluations.keys())):
        raise ValueError("commitments/evaluations key sets mismatch")

    return ProofDemo(
        agg_commitment=agg_commitment,
        agg_value=agg_value,
        commitments=commitments,
        evaluations=evaluations,
        z=z,
        opening_proof=opening_proof,
    )


# ---------------------------
# Aggregation (ρ-powers)
# ---------------------------

def _powers(base: int, n: int) -> List[int]:
    out = [1]
    for _ in range(1, n):
        out.append((out[-1] * base) % _FR)
    return out


def _aggregate_with_rho(commitments: Mapping[str, G1Point],
                        evaluations: Mapping[str, int],
                        rho: int) -> Tuple[G1Point, int]:
    """
    Deterministic aggregation: order by name (ASCII), coefficients = [1, ρ, ρ^2, ...].
    Returns (C_agg, v_agg).
    """
    if not commitments:
        raise ValueError("no commitments to aggregate")

    names = sorted(commitments.keys())
    k = len(names)
    coeffs = _powers(rho % _FR, k)

    # Start from infinity (z=0 representation)
    C_agg = (FQ(1), FQ(1), FQ(0))
    v_agg = 0

    for i, nm in enumerate(names):
        c = coeffs[i]
        C_agg = _add(C_agg, _mul(commitments[nm], c))
        v_agg = (v_agg + (c * int(evaluations[nm])) % _FR) % _FR

    return C_agg, v_agg


# ---------------------------
# Verify (single-opening)
# ---------------------------

def verify_plonk_kzg(
    vk_json: Mapping[str, object],
    proof_json: Mapping[str, object],
    public_inputs: Sequence[Union[int, str]] = (),
    *,
    fs_label: str = "animica:plonk-kzg:demo",
    poseidon_params: str = "bn254_t3",
) -> bool:
    """
    Perform the **single KZG opening check** used at the end of PLONK.

    It will:
      - Parse the VK & proof.
      - If needed, derive a random linear combination via Fiat–Shamir (ρ) using:
            - s_g2
            - the sorted list of commitment points,
            - the evaluation point z,
            - the list of public inputs (to bind them into the check),
        and aggregate (commitments, evaluations) → (C_agg, v_agg).
      - Run KZG verify:   e(C_agg - v_agg*G1, G2) == e(π, s*G2 - z*G2).

    Returns:
      True on success, False otherwise.
    """
    try:
        # VK & proof
        vk_parsed = load_vk(vk_json)
        pf = load_proof(proof_json)

        # Prepare a KZG VK (reuse the generators from pairing module)
        kzg_vk = KZGVK(g1=vk_parsed.g1, g2=vk_parsed.g2, s_g2=vk_parsed.s_g2)

        # Compute (C_agg, v_agg)
        if pf.agg_commitment is not None and pf.agg_value is not None:
            C_agg = pf.agg_commitment
            v_agg = pf.agg_value
        else:
            # Derive rho with FS transcript bound to inputs/commitments/z
            t = Transcript(fs_label, params_name=poseidon_params)
            t.append_g2("s_g2", vk_parsed.s_g2)
            for nm in sorted(pf.commitments.keys()):
                t.append_g1(f"comm:{nm}", pf.commitments[nm])
                t.append_scalar(f"eval:{nm}", pf.evaluations[nm])
            t.append_scalar("z", pf.z)
            for i, pi in enumerate(public_inputs):
                t.append_scalar(f"pub[{i}]", _fr(pi))
            rho = t.challenge_scalar("rho")

            C_agg, v_agg = _aggregate_with_rho(pf.commitments, pf.evaluations, rho)

        # Final KZG pairing check
        ok = kzg_verify(C_agg, pf.z, v_agg, pf.opening_proof, kzg_vk, validate=True)
        return bool(ok)
    except Exception:
        # Avoid leaking parse/arith details to callers unless you log internally.
        return False


# ---------------------------
# Self-test (manual)
# ---------------------------

if __name__ == "__main__":  # pragma: no cover
    # Small deterministic smoke that exercises the code path by crafting a
    # linear polynomial f(t)=a0+a1*t and "pretending" it is already aggregated.
    from random import Random
    rnd = Random(7)
    q = _FR

    # Build a local KZG VK using toxic s (dev only)
    from .kzg_bn254 import make_verifying_key
    s = rnd.randrange(2, q-1)
    kzg_vk = make_verifying_key(s)
    vk_json = {
        "s_g2": [
            [int(kzg_vk.s_g2[0].coeffs[0]), int(kzg_vk.s_g2[0].coeffs[1])],  # type: ignore[attr-defined]
            [int(kzg_vk.s_g2[1].coeffs[0]), int(kzg_vk.s_g2[1].coeffs[1])],  # type: ignore[attr-defined]
        ]
    }

    # We'll need s*G1 locally to craft a commitment (for this self-test only)
    g1 = kzg_vk.g1
    s_g1 = _mul(g1, s % q)

    a0, a1 = rnd.randrange(1, q), rnd.randrange(1, q)
    z = rnd.randrange(1, q)
    y = (a0 + a1 * z) % q

    # C = a0*G1 + a1*(s*G1)
    C = _add(_mul(g1, a0), _mul(s_g1, a1))
    # π = a1*G1  (quotient of linear poly)
    pi = _mul(g1, a1)

    proof_json = {
        "agg_commitment": [int(C[0]), int(C[1])],  # type: ignore[index]
        "agg_value": str(y),
        "z": str(z),
        "opening_proof": [int(pi[0]), int(pi[1])],  # type: ignore[index]
    }

    ok = verify_plonk_kzg(vk_json, proof_json, public_inputs=[])
    print(f"[plonk_kzg_bn254] backend={_BACKEND}  verify -> {ok}")
