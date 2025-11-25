"""
Animica zk.adapters.plonkjs_loader
==================================

Helpers to **load and normalize PlonkJS-style** JSON artifacts (BN254/KZG).

Why this exists
---------------
Different toolchains (PlonkJS, SnarkJS, custom exporters) serialize PLONK/KZG
objects with slightly different field names and number encodings (decimal
strings, `0x` hex, or JS BigInt strings with a trailing `n`). This module
accepts those variations, coerces all numeric-like values into Python `int`,
and returns a *stable, minimally-opinionated* shape that downstream verifiers
can consume.

Scope
-----
- Pure parsing/normalization. **No verification here.**
- BN254/KZG oriented (but tolerant of extra fields).
- Proof bundles like `{ "proof": {...}, "publicSignals": [...] }` or flat
  objects are both accepted.

Exports
-------
- load_json(source)                            # from snarkjs_loader
- normalize_numbers(obj)                       # from snarkjs_loader

- is_plonkjs_vk(obj) -> bool
- is_plonkjs_proof(obj) -> bool

- normalize_plonkjs_vk(vk_obj) -> dict
- normalize_plonkjs_proof(bundle_or_proof) -> (proof_dict, public_inputs_list)

- load_plonkjs(vk_source, proof_source) -> (vk_json, proof_json, public_inputs)

- extract_kzg_vk(vk_json) -> {"s_g2": [[sx0,sx1],[sy0,sy1]]}
- extract_kzg_opening(proof_json) -> {
      "commitment": [cx,cy],
      "z": int,
      "value": int,
      "proof": [px,py]
  }
  (Only returned if those aliases are present; otherwise KeyError.)

Notes on tolerated field names
------------------------------
KZG verifying key:
    - "s_g2", "tau_g2", "g2_s", or nested in:
      { "commitmentKey": { "s_g2": ... } } or { "kzg": { "g2_s": ... } }

KZG single-opening (often present in minimal PLONK/KZG verifiers):
    commitment: "commitment", "C"
    eval point : "z", "x"
    value      : "y", "value", "v"
    opening    : "proof", "opening_proof", "pi"

We **preserve original keys** but also return helpers to extract the above
canonical forms.

License: MIT
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple, List, Union, Iterable, Optional
from .snarkjs_loader import load_json, normalize_numbers

JsonLike = Union[str, bytes, "os.PathLike[str]", Mapping[str, Any]]


# -----------------------------------------------------------------------------
# Shape detection
# -----------------------------------------------------------------------------

def is_plonkjs_vk(obj: Mapping[str, Any]) -> bool:
    """
    Heuristic check for a PlonkJS verifying key:
      - explicit protocol 'plonk' OR
      - presence of KZG params (any of: s_g2 / tau_g2 / commitmentKey / kzg)
    """
    proto = str(obj.get("protocol", "")).lower()
    if proto == "plonk":
        return True
    kzg = obj.get("s_g2") or obj.get("tau_g2") or obj.get("g2_s")
    if kzg is not None:
        return True
    ck = obj.get("commitmentKey") or obj.get("kzg")
    if isinstance(ck, Mapping):
        return any(k in ck for k in ("s_g2", "g2_s", "tau_g2"))
    return False

def is_plonkjs_proof(obj: Mapping[str, Any]) -> bool:
    """
    Heuristic for a PlonkJS proof bundle:
      - object with 'proof' (dict) and optional 'publicSignals'
      - OR a flat object that *looks like* a PLONK/KZG proof (has common fields)
    """
    if "proof" in obj and isinstance(obj["proof"], Mapping):
        return True
    # Flat: look for common fields (very tolerant)
    keys = set(obj.keys())
    candidates = {"publicSignals", "commitments", "W", "opening", "pi", "proof", "z", "x"}
    return bool(keys & candidates)


# -----------------------------------------------------------------------------
# Normalizers
# -----------------------------------------------------------------------------

def _norm_g1(pt: Iterable[Any]) -> List[int]:
    arr = list(pt)
    if len(arr) != 2:
        raise ValueError("G1 point must be [x, y]")
    return [int(arr[0]), int(arr[1])]

def _norm_g2(pt: Iterable[Iterable[Any]]) -> List[List[int]]:
    arr = [list(a) for a in pt]
    if len(arr) != 2 or len(arr[0]) != 2 or len(arr[1]) != 2:
        raise ValueError("G2 point must be [[x0,x1],[y0,y1]]")
    return [[int(arr[0][0]), int(arr[0][1])], [int(arr[1][0]), int(arr[1][1])]]

def _maybe_get(mapping: Mapping[str, Any], *names: str) -> Any:
    for n in names:
        if n in mapping:
            return mapping[n]
    return None

def _pluck_s_g2(vk: Mapping[str, Any]) -> Optional[List[List[int]]]:
    # direct
    for key in ("s_g2", "tau_g2", "g2_s"):
        v = vk.get(key)
        if isinstance(v, Iterable):
            try:
                return _norm_g2(v)  # type: ignore[arg-type]
            except Exception:
                pass
    # nested
    for parent in ("commitmentKey", "kzg"):
        ck = vk.get(parent)
        if isinstance(ck, Mapping):
            for key in ("s_g2", "g2_s", "tau_g2"):
                v = ck.get(key)
                if v is not None:
                    return _norm_g2(v)  # type: ignore[arg-type]
    return None

def normalize_plonkjs_vk(vk: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Return a dict with **numbers coerced to ints** and common KZG params normalized.
    We keep original keys intact and, if discoverable, add a normalized mirror:

        out["kzg_vk"] = { "s_g2": [[sx0,sx1],[sy0,sy1]] }

    Other fields (selectors, domains, etc.) are passed through number-normalized.
    """
    vk_n = normalize_numbers(vk)
    out: Dict[str, Any] = dict(vk_n)
    sg2 = _pluck_s_g2(vk_n)
    if sg2 is not None:
        out.setdefault("kzg_vk", {"s_g2": sg2})
        # prefer canonical "s_g2" at top-level if not present
        out.setdefault("s_g2", sg2)
    return out

def normalize_plonkjs_proof(bundle_or_proof: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[int]]:
    """
    Accept either:
      - bundle: { proof: {...}, publicSignals: [...] }
      - flat:   { ...fields..., publicSignals?: [...] }

    Returns: (proof_dict_with_ints, public_inputs_int_list)
    """
    if "proof" in bundle_or_proof and isinstance(bundle_or_proof["proof"], Mapping):
        proof_obj = bundle_or_proof["proof"]
        publics = bundle_or_proof.get("publicSignals", [])
        # hoist meta if present
        for mk in ("protocol", "curve"):
            if mk in bundle_or_proof and mk not in proof_obj:
                proof_obj = {mk: bundle_or_proof[mk], **proof_obj}
    else:
        proof_obj = bundle_or_proof
        publics = bundle_or_proof.get("publicSignals", [])

    proof_n = normalize_numbers(proof_obj)
    if publics is None:
        publics_n: List[int] = []
    elif isinstance(publics, list):
        publics_n = [int(x if isinstance(x, int) else normalize_numbers(x)) for x in publics]
    else:
        raise ValueError("publicSignals must be a list when present")

    return proof_n, publics_n


# -----------------------------------------------------------------------------
# KZG convenience extractors (optional)
# -----------------------------------------------------------------------------

def extract_kzg_vk(vk_json: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Extract the **KZG verifying key** piece used by our KZG/PLONK verifiers.

    Returns: {"s_g2": [[sx0,sx1],[sy0,sy1]]}
    Raises KeyError if not found.
    """
    vk_n = normalize_plonkjs_vk(vk_json)
    # Prefer normalized mirror
    kzg = vk_n.get("kzg_vk")
    if isinstance(kzg, Mapping) and "s_g2" in kzg:
        return {"s_g2": _norm_g2(kzg["s_g2"])}  # type: ignore[arg-type]
    # Try top-level canonical
    sg2 = _pluck_s_g2(vk_n)
    if sg2 is not None:
        return {"s_g2": sg2}
    raise KeyError("Could not locate KZG s_g2 in verifying key")

def extract_kzg_opening(proof_json: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Extract a **single-point KZG opening** (if present) from a PLONK proof object.

    Returns a dict:
      {
        "commitment": [cx, cy],
        "z": int,
        "value": int,
        "proof": [px, py]
      }

    We look for flexible aliases:
      commitment: "commitment", "C"
      z         : "z", "x"
      value     : "value", "y", "v"
      proof     : "proof", "opening_proof", "pi"
    """
    p = normalize_numbers(proof_json)

    C  = _maybe_get(p, "commitment", "C")
    z  = _maybe_get(p, "z", "x")
    v  = _maybe_get(p, "value", "y", "v")
    pi = _maybe_get(p, "proof", "opening_proof", "pi")

    if C is None or z is None or v is None or pi is None:
        raise KeyError("KZG opening fields not found in proof object")

    return {
        "commitment": _norm_g1(C),          # type: ignore[arg-type]
        "z": int(z),
        "value": int(v),
        "proof": _norm_g1(pi),              # type: ignore[arg-type]
    }


# -----------------------------------------------------------------------------
# Top-level loaders
# -----------------------------------------------------------------------------

def load_plonkjs(vk_source: JsonLike, proof_source: JsonLike) -> Tuple[Dict[str, Any], Dict[str, Any], List[int]]:
    """
    Convenience loader:
        vk_json, proof_json, public_inputs = load_plonkjs("vk.json", "proof.json")

    This performs tolerant number coercion and preserves unknown keys.
    """
    raw_vk = load_json(vk_source)
    raw_pf = load_json(proof_source)
    vk = normalize_plonkjs_vk(raw_vk)
    proof, publics = normalize_plonkjs_proof(raw_pf)
    return vk, proof, publics


# -----------------------------------------------------------------------------
# Public exports
# -----------------------------------------------------------------------------

__all__ = [
    "is_plonkjs_vk",
    "is_plonkjs_proof",
    "normalize_plonkjs_vk",
    "normalize_plonkjs_proof",
    "extract_kzg_vk",
    "extract_kzg_opening",
    "load_plonkjs",
    # Re-exported utilities
    "load_json",
    "normalize_numbers",
]
