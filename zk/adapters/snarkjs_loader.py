"""
Animica zk.adapters.snarkjs_loader
==================================

Helpers to **load and normalize** SnarkJS JSON artifacts for verifiers:

- Groth16 on BN254 (aka bn128 in SnarkJS)
- (Light) PLONK/KZG BN254 pass-through number normalization

This module does **not** verify proofs; it only parses files/JSON, coerces
bigint-like strings into Python `int`s, and normalizes common shapes so they
can be passed into `zk.adapters.verify_*` helpers.

Typical Groth16 SnarkJS shapes
------------------------------
Verifying key (vk.json):
{
  "protocol": "groth16",
  "curve": "bn128",
  "vk_alpha_1": [ "0x..", "0x.." ],
  "vk_beta_2":  [[ "0x..","0x.." ], [ "0x..","0x.." ]],
  "vk_gamma_2": [[ "0x..","0x.." ], [ "0x..","0x.." ]],
  "vk_delta_2": [[ "0x..","0x.." ], [ "0x..","0x.." ]],
  "IC": [ [ "0x..", "0x.." ], ... ]
}

Proof and publics (proof.json):
{
  "protocol": "groth16",
  "curve": "bn128",
  "pi_a": [ "0x..", "0x.." ],
  "pi_b": [[ "0x..","0x.." ], [ "0x..","0x.." ]],
  "pi_c": [ "0x..", "0x.." ],
  "publicSignals": [ "123", "0x45" ]
}

Some tools wrap as { "proof": {...}, "publicSignals": [...] } — we handle both.

Exports
-------
- load_json(source) -> dict
- normalize_numbers(obj) -> obj_with_ints
- is_groth16_vk(obj) / is_groth16_proof(obj) / is_plonk_vk(obj) / is_plonk_proof(obj)
- normalize_groth16_vk(vk) -> dict (same keys, ints)
- normalize_groth16_proof(proof_or_bundle) -> (proof_dict, public_inputs_list)
- load_groth16(vk_source, proof_source) -> (vk_json, proof_json, public_inputs_list)

- normalize_plonk_vk(vk) / normalize_plonk_proof(bundle)  # light numeric coercion
- load_plonk(vk_source, proof_source) -> (vk_json, proof_json, public_inputs_list)

License: MIT
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Tuple, Union


JsonLike = Union[str, bytes, os.PathLike, Mapping[str, Any]]


# -----------------------------------------------------------------------------
# I/O helpers
# -----------------------------------------------------------------------------

def load_json(source: JsonLike) -> Dict[str, Any]:
    """
    Load JSON from:
      - dict-like: shallow-copied into a new dict
      - path-like or string path
      - string containing JSON text

    Raises ValueError on failure.
    """
    if isinstance(source, Mapping):
        return dict(source)  # copy
    if isinstance(source, (bytes, bytearray, memoryview)):
        return json.loads(bytes(source).decode("utf-8"))
    s = str(source)

    # Path detection: if it exists on disk, read it
    if os.path.exists(s) and os.path.isfile(s):
        with open(s, "r", encoding="utf-8") as f:
            return json.load(f)

    # Otherwise, treat as JSON text
    try:
        return json.loads(s)
    except Exception as e:
        raise ValueError(f"Could not load JSON from provided source: {e}") from e


# -----------------------------------------------------------------------------
# Number coercion (dec/hex/JS BigInt strings → Python int)
# -----------------------------------------------------------------------------

_INT_RE = re.compile(r"^\s*([+-]?(?:0x[0-9a-fA-F]+|\d+))n?\s*$")

def _maybe_to_int(x: Any) -> Any:
    if isinstance(x, int):
        return x
    if isinstance(x, bool):  # bool is int subclass; keep boolean as-is
        return x
    if isinstance(x, (bytes, bytearray, memoryview)):
        # Interpret as big-endian integer
        return int.from_bytes(bytes(x), "big")
    if isinstance(x, str):
        m = _INT_RE.match(x)
        if m:
            val = m.group(1)
            try:
                return int(val, 0)
            except Exception:
                return x
    return x

def normalize_numbers(obj: Any) -> Any:
    """
    Recursively traverse obj and convert **numeric-like strings** (e.g. "123",
    "0xabc", "123n") into Python ints. Bytes are interpreted as big-endian ints.
    Other types are preserved.
    """
    if isinstance(obj, Mapping):
        return {k: normalize_numbers(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_numbers(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(normalize_numbers(v) for v in obj)
    return _maybe_to_int(obj)


# -----------------------------------------------------------------------------
# Shape detection
# -----------------------------------------------------------------------------

def is_groth16_vk(obj: Mapping[str, Any]) -> bool:
    keys = set(obj.keys())
    return (
        ("vk_alpha_1" in keys) and
        ("vk_beta_2" in keys) and
        ("vk_gamma_2" in keys) and
        ("vk_delta_2" in keys) and
        ("IC" in keys)
    )

def is_groth16_proof(obj: Mapping[str, Any]) -> bool:
    # Either proof bundle {proof:{pi_a,..}, publicSignals:[..]} or flat with pi_a
    if "proof" in obj and isinstance(obj.get("proof"), Mapping):
        p = obj["proof"]
        return all(k in p for k in ("pi_a", "pi_b", "pi_c"))
    return all(k in obj for k in ("pi_a", "pi_b", "pi_c"))

def is_plonk_vk(obj: Mapping[str, Any]) -> bool:
    # SnarkJS PLONK VK shapes vary; accept "protocol":"plonk" as a hint
    return obj.get("protocol", "").lower() == "plonk"

def is_plonk_proof(obj: Mapping[str, Any]) -> bool:
    # SnarkJS usually emits { proof: {...}, publicSignals: [...] } with "protocol":"plonk"
    if obj.get("protocol", "").lower() == "plonk" and ("proof" in obj or "pi_a" not in obj):
        return True
    if "proof" in obj and isinstance(obj["proof"], Mapping):
        return obj.get("protocol", "").lower() in ("plonk", "")
    # Pass-through: we just number-normalize
    return False


# -----------------------------------------------------------------------------
# Groth16 normalization
# -----------------------------------------------------------------------------

def _norm_g1(pt: Iterable[Any]) -> List[int]:
    arr = list(pt)
    if len(arr) != 2:
        raise ValueError("G1 point must have 2 coordinates")
    return [int(_maybe_to_int(arr[0])), int(_maybe_to_int(arr[1]))]

def _norm_g2(pt: Iterable[Iterable[Any]]) -> List[List[int]]:
    arr = [list(a) for a in pt]
    if len(arr) != 2 or len(arr[0]) != 2 or len(arr[1]) != 2:
        raise ValueError("G2 point must be [[x0,x1],[y0,y1]]")
    return [
        [int(_maybe_to_int(arr[0][0])), int(_maybe_to_int(arr[0][1]))],
        [int(_maybe_to_int(arr[1][0])), int(_maybe_to_int(arr[1][1]))],
    ]

def normalize_groth16_vk(vk: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Normalize SnarkJS Groth16 verifying key into a stable dict with the
    **same key names** SnarkJS uses, but with Python ints everywhere.
    """
    if not is_groth16_vk(vk):
        raise ValueError("Provided object does not look like a Groth16 verifying key")
    out: Dict[str, Any] = {}

    # Preserve metadata if present
    for meta_key in ("protocol", "curve"):
        if meta_key in vk:
            out[meta_key] = str(vk[meta_key])

    out["vk_alpha_1"] = _norm_g1(vk["vk_alpha_1"])
    out["vk_beta_2"]  = _norm_g2(vk["vk_beta_2"])
    out["vk_gamma_2"] = _norm_g2(vk["vk_gamma_2"])
    out["vk_delta_2"] = _norm_g2(vk["vk_delta_2"])

    # Optional vk_alphabeta_12 (unused by verifier but keep normalized if present)
    if "vk_alphabeta_12" in vk:
        out["vk_alphabeta_12"] = normalize_numbers(vk["vk_alphabeta_12"])

    # IC array of G1 points
    IC = vk.get("IC")
    if not isinstance(IC, list) or len(IC) == 0:
        raise ValueError("vk.IC must be a non-empty list of G1 points")
    out["IC"] = [_norm_g1(pt) for pt in IC]

    return out

def normalize_groth16_proof(bundle_or_proof: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[int]]:
    """
    Accept either:
      - flat proof dict {pi_a, pi_b, pi_c, protocol?, curve?, publicSignals?}
      - bundle {proof: {...}, publicSignals: [...]}

    Returns: (proof_dict, public_inputs_list_of_ints)
    """
    public_signals: List[int] = []
    if "proof" in bundle_or_proof and isinstance(bundle_or_proof["proof"], Mapping):
        proof = bundle_or_proof["proof"]
        publics = bundle_or_proof.get("publicSignals", [])
    else:
        proof = bundle_or_proof
        publics = bundle_or_proof.get("publicSignals", [])

    if not is_groth16_proof({"proof": proof} if "proof" not in proof else proof):  # tolerant check
        # Simplify: require pi_a/b/c after the extraction above
        for k in ("pi_a", "pi_b", "pi_c"):
            if k not in proof:
                raise ValueError(f"Groth16 proof missing '{k}'")

    out: Dict[str, Any] = {}
    for meta_key in ("protocol", "curve"):
        if meta_key in proof:
            out[meta_key] = str(proof[meta_key])

    out["pi_a"] = _norm_g1(proof["pi_a"])
    out["pi_b"] = _norm_g2(proof["pi_b"])
    out["pi_c"] = _norm_g1(proof["pi_c"])

    # Publics
    if publics is None:
        public_signals = []
    elif isinstance(publics, list):
        public_signals = [int(_maybe_to_int(v)) for v in publics]
    else:
        raise ValueError("publicSignals must be a list when present")

    return out, public_signals


def load_groth16(vk_source: JsonLike, proof_source: JsonLike) -> Tuple[Dict[str, Any], Dict[str, Any], List[int]]:
    """
    Convenience loader:
      (vk_json, proof_json, public_inputs) = load_groth16("verification_key.json", "proof.json")
    """
    raw_vk = load_json(vk_source)
    raw_pf = load_json(proof_source)
    vk = normalize_groth16_vk(normalize_numbers(raw_vk))
    proof, publics = normalize_groth16_proof(normalize_numbers(raw_pf))
    return vk, proof, publics


# -----------------------------------------------------------------------------
# PLONK normalization (light-weight; shapes differ across versions)
# -----------------------------------------------------------------------------

def normalize_plonk_vk(vk: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Perform **number normalization only**, preserving keys as-is.
    SnarkJS PLONK VK layouts differ; our verifiers/adapters should consume the
    exact fields they need. This function just ensures all numeric strings are ints.
    """
    if not is_plonk_vk(vk):
        # Still helpful to coerce numbers for other tools; don't error hard.
        pass
    return normalize_numbers(vk)

def normalize_plonk_proof(bundle: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[int]]:
    """
    Similar to Groth16 handler: accept either a flat object with fields
    the downstream expects, or a SnarkJS bundle with
      { "proof": {...}, "publicSignals": [...] }
    We **only** coerce numeric strings to ints and return (proof_dict, publics).
    """
    if "proof" in bundle and isinstance(bundle["proof"], Mapping):
        proof = bundle["proof"]
        publics = bundle.get("publicSignals", [])
        meta = {k: bundle.get(k) for k in ("protocol", "curve") if k in bundle}
        if meta:
            proof = {**meta, **proof}
    else:
        proof = bundle
        publics = bundle.get("publicSignals", [])

    proof_n = normalize_numbers(proof)
    if publics is None:
        publics_n: List[int] = []
    elif isinstance(publics, list):
        publics_n = [int(_maybe_to_int(v)) for v in publics]
    else:
        raise ValueError("publicSignals must be a list when present")

    return proof_n, publics_n

def load_plonk(vk_source: JsonLike, proof_source: JsonLike) -> Tuple[Dict[str, Any], Dict[str, Any], List[int]]:
    """
    Convenience loader for PLONK:
      (vk_json, proof_json, public_inputs) = load_plonk("vk_plonk.json", "proof_plonk.json")

    This does **not** change field names — it only coerces numeric strings.
    """
    raw_vk = load_json(vk_source)
    raw_pf = load_json(proof_source)
    vk = normalize_plonk_vk(raw_vk)
    proof, publics = normalize_plonk_proof(raw_pf)
    return vk, proof, publics


# -----------------------------------------------------------------------------
# Module export control
# -----------------------------------------------------------------------------

__all__ = [
    "load_json",
    "normalize_numbers",
    # Groth16
    "is_groth16_vk",
    "is_groth16_proof",
    "normalize_groth16_vk",
    "normalize_groth16_proof",
    "load_groth16",
    # PLONK
    "is_plonk_vk",
    "is_plonk_proof",
    "normalize_plonk_vk",
    "normalize_plonk_proof",
    "load_plonk",
]
