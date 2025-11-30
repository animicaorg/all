"""
Animica zk.adapters.stark_loader
================================

Tolerant loader/normalizer for **STARK FRI proofs** expressed as JSON.

This module is **parsing-only**: it accepts a variety of field names used by
popular STARK toolchains (Winterfell-like exports, Plonky-ish dumps, custom
bundles) and normalizes them into a small, stable shape that our educational
verifier(s) can consume.

Scope & Philosophy
------------------
- Handle *common aliases* for FRI components:
  - Commitments (layer roots)
  - Queries (positions/indices + Merkle authentication paths per layer)
  - Per-layer values/codewords (optional; kept if provided)
  - Final (small) polynomial coefficients (aka remainder)
  - Optional params (domain size, rate/blowup, rounds)
- Never perform cryptographic verification here — *numbers in, numbers out*.
- Keep unknown fields (pass-through) but provide a canonical sub-structure.

Canonical Output Shape
----------------------
`normalize_fri_proof(..)` returns a dict with these keys (when discoverable):

{
  # FRI commitments (Merkle roots) as hex strings (0x…)
  "commitments": [ "0x…", "0x…", ... ],

  # Queries across layers.
  # Each query has a scalar position (index into the base domain),
  # and a list of merkle paths — one per layer — where each path is a
  # list of 32-byte sibling hashes (hex strings).
  "queries": [
      {
        "position": 123,
        "merkle_paths": [
           ["0x..", "0x..", ...],     # layer 0 auth path
           ["0x..", "0x..", ...],     # layer 1 auth path
           ...
        ],
        # Optional per-layer values/codewords if present in input
        "values": [
           ["0x..", "0x.."],          # layer 0 leaf value(s) if provided
           ["0x.."],                  # layer 1 ...
           ...
        ]
      },
      ...
  ],

  # Final tiny polynomial coefficients over the base field (ints)
  "final_poly": [ 1, 42, 7 ],

  # Optional parameters (ints if present)
  "fri_params": {
    "domain_size": 2**k,    # or n
    "rate_bits":  k,        # log2(blowup)
    "blowup":     1<<k,     # if provided
    "num_rounds": r
  }
}

Public I/O Normalization (Toy Merkle AIR)
-----------------------------------------
If you also pass *public* data (e.g., `{ "root": "0x..", "leaf": 5, "index": 7 }`)
into `normalize_public_io`, you get a canonical dict with exactly those keys,
coercing numbers to `int` and hashes to `0x`-prefixed hex.

Exports
-------
- load_json(source)                      # re-exported from snarkjs_loader
- normalize_numbers(obj)                 # re-exported number coercion

- is_fri_proof(obj) -> bool
- normalize_fri_proof(obj) -> dict
- normalize_public_io(obj) -> dict
- load_fri(proof_source, public_source=None) -> (proof_dict, public_dict|None)

Notes
-----
- Hex normalization is best-effort: we preserve any `0x` hashes; for bare
  hex strings we add the prefix. We do not change byte order.
- We **do not** run `normalize_numbers` over the entire proof because that
  would interpret `0x…` hashes as integers; we only coerce known numeric
  scalars (positions, poly coeffs, params).

License: MIT
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from .snarkjs_loader import (load_json,  # re-exported utilities
                             normalize_numbers)

JsonLike = Union[str, bytes, os.PathLike, Mapping[str, Any]]

# -----------------------------------------------------------------------------
# Hex & int helpers (careful not to convert hash strings to ints globally)
# -----------------------------------------------------------------------------

_HEX_RE_PLAIN = re.compile(r"^[0-9A-Fa-f]+$")
_INT_TOKEN_RE = re.compile(r"^\s*([+-]?(?:0x[0-9a-fA-F]+|\d+))n?\s*$")


def _is_hex_like(s: Any) -> bool:
    if not isinstance(s, str):
        return False
    t = s.strip()
    return t.startswith(("0x", "0X")) or bool(_HEX_RE_PLAIN.fullmatch(t))


def _int_try(x: Any) -> Optional[int]:
    """Try to parse a scalar number (decimal, hex string, JS BigInt), else None."""
    if isinstance(x, bool):
        return int(x)
    if isinstance(x, int):
        return x
    if isinstance(x, (bytes, bytearray, memoryview)):
        return int.from_bytes(bytes(x), "big")
    if isinstance(x, str):
        m = _INT_TOKEN_RE.match(x)
        if m:
            try:
                return int(m.group(1), 0)
            except Exception:
                return None
    return None


def _int_to_hex(i: int) -> str:
    if i == 0:
        return "0x0"
    width = (i.bit_length() + 7) // 8
    return "0x" + i.to_bytes(width, "big").hex()


def _as_hex(x: Any) -> str:
    """
    Normalize various representations into a canonical '0x' lowercase hex string.
    Accepts: '0x..', 'ABCDEF..', bytes, int.
    """
    if isinstance(x, (bytes, bytearray, memoryview)):
        return "0x" + bytes(x).hex()
    if isinstance(x, int) and not isinstance(x, bool):
        return _int_to_hex(x)
    if isinstance(x, str):
        s = x.strip()
        if s.startswith(("0x", "0X")):
            return "0x" + s[2:].lower()
        if _HEX_RE_PLAIN.fullmatch(s):
            return "0x" + s.lower()
    # Fallback: treat as string payload (ensure prefix to satisfy consumers)
    return f"0x{str(x).strip().lower()}"


def _as_int(x: Any, *, default: Optional[int] = None) -> Optional[int]:
    v = _int_try(x)
    return v if v is not None else default


# -----------------------------------------------------------------------------
# Commitments (layer roots)
# -----------------------------------------------------------------------------


def _extract_commitments(obj: Mapping[str, Any]) -> Optional[List[str]]:
    # Try common keys at top-level or under "fri"/"layers"
    candidates: List[Any] = []
    for k in ("commitments", "roots", "layerRoots", "commitmentRoots"):
        v = obj.get(k)
        if v is not None:
            candidates.append(v)
    fri = obj.get("fri")
    if isinstance(fri, Mapping):
        for k in ("commitments", "roots", "layerRoots"):
            v = fri.get(k)
            if v is not None:
                candidates.append(v)
    layers = obj.get("layers")
    if isinstance(layers, Mapping):
        for k in ("roots", "commitments"):
            v = layers.get(k)
            if v is not None:
                candidates.append(v)

    arr: Optional[List[Any]] = None
    for cand in candidates:
        if isinstance(cand, list) and len(cand) > 0:
            arr = cand
            break

    if arr is None:
        # Sometimes single root appears as 'root'
        single = obj.get("root") or (
            fri.get("root") if isinstance(fri, Mapping) else None
        )
        if single is not None:
            return [_as_hex(single)]
        return None

    roots: List[str] = []
    for item in arr:
        if isinstance(item, Mapping) and "root" in item:
            roots.append(_as_hex(item["root"]))
        else:
            roots.append(_as_hex(item))
    return roots


# -----------------------------------------------------------------------------
# Queries & auth paths
# -----------------------------------------------------------------------------


def _extract_layer_paths(layer_obj: Any) -> List[str]:
    """
    Given a layer entry which could be:
      - list[str|int|bytes] of siblings
      - { "siblings": [...]} or {"path": [...]} or {"auth_path":[...]}
    return list of hex strings.
    """
    if isinstance(layer_obj, list):
        return [_as_hex(x) for x in layer_obj]
    if isinstance(layer_obj, Mapping):
        for k in ("siblings", "path", "auth_path", "authentication_path"):
            if k in layer_obj and isinstance(layer_obj[k], list):
                return [_as_hex(x) for x in layer_obj[k]]
    # Fallback: treat as single element
    return [_as_hex(layer_obj)]


def _extract_layer_values(layer_obj: Any) -> List[Union[int, str]]:
    """
    Optional helper to extract leaf/value payloads per layer.
    We return ints when value looks numeric, otherwise hex strings.
    """
    values: List[Union[int, str]] = []
    src: Optional[List[Any]] = None

    if isinstance(layer_obj, Mapping):
        for k in ("values", "evals", "evaluations", "leaves", "codewords"):
            v = layer_obj.get(k)
            if isinstance(v, list):
                src = v
                break
        # Sometimes value is a scalar under 'value'/'leaf'
        if src is None:
            for k in ("value", "leaf"):
                if k in layer_obj:
                    src = [layer_obj[k]]  # box into list
                    break
    elif isinstance(layer_obj, list):
        # Could already be a list of values; accept
        src = layer_obj

    if src is None:
        return values

    for x in src:
        xi = _int_try(x)
        values.append(xi if xi is not None else _as_hex(x))
    return values


def _extract_queries(obj: Mapping[str, Any]) -> Optional[List[Dict[str, Any]]]:
    # Collect possible query arrays from several places
    qsrc: Optional[List[Any]] = None
    for k in (
        "queries",
        "queryRounds",
        "query_rounds",
        "openings",
        "decommitments",
        "query_proofs",
    ):
        v = obj.get(k)
        if isinstance(v, list):
            qsrc = v
            break
    if qsrc is None and isinstance(obj.get("proof"), Mapping):
        pv = obj["proof"]
        for k in (
            "queries",
            "queryRounds",
            "openings",
            "decommitments",
            "query_proofs",
        ):
            v = pv.get(k)
            if isinstance(v, list):
                qsrc = v
                break
    if qsrc is None and isinstance(obj.get("fri"), Mapping):
        fv = obj["fri"]
        for k in (
            "queries",
            "queryRounds",
            "openings",
            "decommitments",
            "query_proofs",
        ):
            v = fv.get(k)
            if isinstance(v, list):
                qsrc = v
                break

    if qsrc is None:
        return None

    out: List[Dict[str, Any]] = []
    for q in qsrc:
        if not isinstance(q, Mapping):
            # Allow raw index (position) arrays (rare); we cannot build paths from it
            pos = _as_int(q)
            if pos is None:
                continue
            out.append({"position": pos, "merkle_paths": []})
            continue

        # Position / index
        pos = (
            _as_int(q.get("position"))
            or _as_int(q.get("index"))
            or _as_int(q.get("idx"))
            or _as_int(q.get("i"))
            or _as_int(q.get("x"))
        )
        if pos is None:
            # Some formats nest the position per-layer; keep None if not present
            pos = None

        # Paths per layer — look for well-known keys or infer from 'layers'
        layers_src: Optional[List[Any]] = None
        for k in ("merkle_paths", "auth_paths", "authentication_paths", "paths"):
            v = q.get(k)
            if isinstance(v, list):
                layers_src = v
                break
        if layers_src is None:
            if isinstance(q.get("layers"), list):
                layers_src = q["layers"]
            elif isinstance(q.get("decommitments"), list):
                layers_src = q["decommitments"]

        merkle_paths: List[List[str]] = []
        values_layers: List[List[Union[int, str]]] = []

        if isinstance(layers_src, list):
            for layer in layers_src:
                merkle_paths.append(_extract_layer_paths(layer))
                vals = _extract_layer_values(layer)
                if vals:
                    values_layers.append(vals)

        entry: Dict[str, Any] = {"merkle_paths": merkle_paths}
        if pos is not None:
            entry["position"] = pos
        if values_layers:
            entry["values"] = values_layers

        out.append(entry)

    return out


# -----------------------------------------------------------------------------
# Final polynomial (remainder)
# -----------------------------------------------------------------------------


def _extract_final_poly(obj: Mapping[str, Any]) -> Optional[List[int]]:
    # Try common names at top-level / nested
    cands: List[Any] = []
    for k in ("final_poly", "remainder", "remainder_coeffs", "coefficients"):
        v = obj.get(k)
        if v is not None:
            cands.append(v)
    for parent in ("fri", "proof"):
        sub = obj.get(parent)
        if isinstance(sub, Mapping):
            for k in ("final_poly", "remainder", "remainder_coeffs", "coefficients"):
                v = sub.get(k)
                if v is not None:
                    cands.append(v)
    for cand in cands:
        if isinstance(cand, list):
            out: List[int] = []
            for x in cand:
                xi = _as_int(x)
                if xi is None:
                    # If someone encoded as hex string, allow decoding as int as fallback
                    if _is_hex_like(x):
                        try:
                            out.append(int(str(x), 16))
                            continue
                        except Exception:
                            pass
                    raise ValueError("final_poly/coefficients must be numeric")
                out.append(xi)
            return out
    return None


# -----------------------------------------------------------------------------
# FRI params
# -----------------------------------------------------------------------------


def _extract_params(obj: Mapping[str, Any]) -> Optional[Dict[str, int]]:
    srcs: List[Mapping[str, Any]] = []
    for k in ("fri_params", "params", "fri"):
        v = obj.get(k)
        if isinstance(v, Mapping):
            srcs.append(v)

    if not srcs and isinstance(obj.get("proof"), Mapping):
        pv = obj["proof"]
        for k in ("fri_params", "params", "fri"):
            v = pv.get(k)
            if isinstance(v, Mapping):
                srcs.append(v)

    if not srcs:
        return None

    # Merge first match (order of precedence)
    p = dict(srcs[0])
    out: Dict[str, int] = {}
    # Domain size aliases
    for k in ("domain_size", "n", "domainSize"):
        if k in p:
            v = _as_int(p[k])
            if v is not None:
                out["domain_size"] = v
                break
    # Rate bits / blowup
    rb = None
    for k in ("rate_bits", "log_blowup", "logBlowup", "rateBits"):
        if k in p:
            rb = _as_int(p[k])
            if rb is not None:
                out["rate_bits"] = rb
                break
    if "blowup" in p:
        b = _as_int(p["blowup"])
        if b is not None:
            out["blowup"] = b
    elif rb is not None:
        out["blowup"] = 1 << rb
    # Rounds
    for k in ("num_rounds", "rounds"):
        if k in p:
            r = _as_int(p[k])
            if r is not None:
                out["num_rounds"] = r
                break

    return out if out else None


# -----------------------------------------------------------------------------
# Public IO for toy Merkle AIR
# -----------------------------------------------------------------------------


def normalize_public_io(public: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Canonicalize toy public inputs for the Merkle-membership AIR:
      {
        "root":  "0x…",     # hex digest
        "leaf":  int|0x..,  # we pass int if numeric, else hex
        "index": int
      }
    Accepts common aliases: commitment/root/hash for the digest; value/leaf for leaf;
    index/idx/position for the index.
    """
    root = public.get("root") or public.get("commitment") or public.get("hash")
    if root is None:
        raise ValueError("public requires 'root' (or alias 'commitment'/'hash')")
    idx = (
        _as_int(public.get("index"))
        or _as_int(public.get("idx"))
        or _as_int(public.get("position"))
    )
    if idx is None:
        raise ValueError("public requires 'index' (or alias 'idx'/'position')")
    leaf_raw = public.get("leaf", public.get("value"))
    if leaf_raw is None:
        raise ValueError("public requires 'leaf' (or alias 'value')")

    leaf_int = _as_int(leaf_raw)
    leaf: Union[int, str] = leaf_int if leaf_int is not None else _as_hex(leaf_raw)

    return {
        "root": _as_hex(root),
        "leaf": leaf,
        "index": int(idx),
    }


# -----------------------------------------------------------------------------
# Top-level API
# -----------------------------------------------------------------------------


def is_fri_proof(obj: Mapping[str, Any]) -> bool:
    """Heuristic: presence of FRI commitments/queries/final_poly in common layouts."""
    keys = set(obj.keys())
    if keys & {
        "commitments",
        "roots",
        "layerRoots",
        "final_poly",
        "remainder",
        "coefficients",
    }:
        return True
    if (
        "queries" in keys
        or "queryRounds" in keys
        or "openings" in keys
        or "decommitments" in keys
    ):
        return True
    # Nested under "fri" or "proof"
    for k in ("fri", "proof"):
        v = obj.get(k)
        if isinstance(v, Mapping) and is_fri_proof(v):
            return True
    return False


def normalize_fri_proof(obj: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Normalize a JSON-ish FRI proof bundle into a stable, minimal shape suitable
    for our tiny verifier(s). Unknown fields are not kept; this returns only
    the canonical substructure.
    """
    out: Dict[str, Any] = {}

    commits = _extract_commitments(obj)
    if commits:
        out["commitments"] = commits

    queries = _extract_queries(obj)
    if queries:
        # Ensure all positions are integers if present
        for q in queries:
            if "position" in q:
                q["position"] = int(q["position"])
        out["queries"] = queries

    final_poly = _extract_final_poly(obj)
    if final_poly:
        out["final_poly"] = [int(x) for x in final_poly]

    params = _extract_params(obj)
    if params:
        out["fri_params"] = params

    if not out:
        raise ValueError("Object did not resemble a FRI proof")
    return out


def load_fri(
    proof_source: JsonLike, public_source: Optional[JsonLike] = None
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    Convenience loader:
        proof, pub = load_fri("fri_proof.json", "public.json")

    We **do not** run `normalize_numbers` on the whole proof to avoid
    converting hex digests to integers. We only coerce expected numeric scalars.
    """
    raw = load_json(proof_source)
    proof = normalize_fri_proof(raw)

    pub_norm: Optional[Dict[str, Any]] = None
    if public_source is not None:
        raw_pub = load_json(public_source)
        pub_norm = normalize_public_io(raw_pub)

    return proof, pub_norm


# -----------------------------------------------------------------------------
# Public exports
# -----------------------------------------------------------------------------

__all__ = [
    "is_fri_proof",
    "normalize_fri_proof",
    "normalize_public_io",
    "load_fri",
    # Re-exports
    "load_json",
    "normalize_numbers",
]
