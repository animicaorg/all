#!/usr/bin/env python3
"""
Compute the Animica PQ alg-policy Merkle root (SHA3-512).

Design (v1):
- Input: a JSON file that follows spec/alg_policy.schema.json.
- We normalize into three leaf kinds:
  * META:    {"version": <int>}
  * THRESH:  {"kind": "sig"|"kem", "minAlgs": <int>, "minWeight": <float>}
  * ENTRY:   for each algorithm entry in .entries[]
             Only consensus-affecting fields are included in the leaf:
             { "id", "kind", "enabled", "sunsetAfter", "keySizes", "sigSizes",
               "kemSizes", "weight" }
             Non-consensus fields like "name", "notes", "minVersion" are ignored.
- Canonicalization: UTF-8 JSON with sort_keys=True and separators=(',', ':').
- Leaf hash:    H_leaf = SHA3-512(0x00 || canonical_json_bytes)
- Internal hash: H_node = SHA3-512(0x01 || left_hash || right_hash)
- Pairing rule: left-to-right; if odd, duplicate the last hash at that level.
- Leaf ordering (stable & deterministic):
    order_key = (type_rank, kind_or_empty, id_or_label)
    where type_rank: META=0, THRESH=1, ENTRY=2
- Output: hex-encoded SHA3-512 Merkle root. Optionally dump leaf hashes.

This file is intentionally dependency-light (no jsonschema). It performs basic
shape checks and will refuse obviously malformed inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional

# ---------- utils

def sha3_512(data: bytes) -> bytes:
    return hashlib.sha3_512(data).digest()

def to_hex(b: bytes) -> str:
    return "0x" + b.hex()

def canon_json(obj: Any) -> bytes:
    """Deterministic JSON encoding used for hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

# ---------- model

@dataclass(frozen=True)
class Leaf:
    kind: str        # "META" | "THRESH" | "ENTRY"
    sort_kind: str   # "", "sig"/"kem" for THRESH/ENTRY; "" for META
    label: str       # "version" for META, kind for THRESH, alg id for ENTRY
    payload: Dict[str, Any]

    def hash(self) -> bytes:
        body = canon_json(self.payload)
        return sha3_512(b"\x00" + body)

def type_rank(t: str) -> int:
    return {"META": 0, "THRESH": 1, "ENTRY": 2}[t]

def leaf_sort_key(l: Leaf) -> Tuple[int, str, str]:
    return (type_rank(l.kind), l.sort_kind, l.label)

# ---------- normalization (consensus-affecting projection)

CONSENSUS_ENTRY_KEYS = (
    "id", "kind", "enabled", "sunsetAfter", "keySizes", "sigSizes", "kemSizes", "weight"
)

def normalize_policy(policy: Dict[str, Any]) -> List[Leaf]:
    # Basic sanity
    if not isinstance(policy, dict):
        raise ValueError("policy: expected object")
    version = policy.get("version")
    if not isinstance(version, int):
        raise ValueError("policy.version: expected integer")
    entries = policy.get("entries")
    if not isinstance(entries, list):
        raise ValueError("policy.entries: expected array")

    thresholds = policy.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("policy.thresholds: expected object")
    sig_thr = thresholds.get("sig")
    kem_thr = thresholds.get("kem")
    for k, obj in [("sig", sig_thr), ("kem", kem_thr)]:
        if not isinstance(obj, dict):
            raise ValueError(f"policy.thresholds.{k}: expected object")
        if not isinstance(obj.get("minAlgs"), int):
            raise ValueError(f"policy.thresholds.{k}.minAlgs: expected integer")
        if not (isinstance(obj.get("minWeight"), (int, float))):
            raise ValueError(f"policy.thresholds.{k}.minWeight: expected number")

    leaves: List[Leaf] = []
    # META
    leaves.append(Leaf("META", "", "version", {"version": version}))
    # THRESH
    leaves.append(Leaf("THRESH", "sig", "sig", {
        "kind": "sig",
        "minAlgs": int(sig_thr["minAlgs"]),
        "minWeight": float(sig_thr["minWeight"]),
    }))
    leaves.append(Leaf("THRESH", "kem", "kem", {
        "kind": "kem",
        "minAlgs": int(kem_thr["minAlgs"]),
        "minWeight": float(kem_thr["minWeight"]),
    }))

    # ENTRY
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            raise ValueError(f"entries[{i}]: expected object")
        kind = e.get("kind")
        if kind not in ("sig", "kem"):
            raise ValueError(f"entries[{i}].kind: expected 'sig' or 'kem'")
        alg_id = e.get("id")
        if not isinstance(alg_id, str) or not alg_id:
            raise ValueError(f"entries[{i}].id: expected non-empty string")

        # Project to consensus-affecting fields, ignore "name", "notes", "minVersion", etc.
        proj: Dict[str, Any] = {}
        for k in CONSENSUS_ENTRY_KEYS:
            v = e.get(k, None)
            # enforce types for critical fields
            if k == "enabled":
                if not isinstance(v, bool):
                    raise ValueError(f"entries[{i}].enabled: expected boolean")
            if k == "weight" and v is not None:
                if not (isinstance(v, (int, float))):
                    raise ValueError(f"entries[{i}].weight: expected number")
                v = float(v)
            if k == "sunsetAfter" and v is not None and not (isinstance(v, int) or v is None):
                raise ValueError(f"entries[{i}].sunsetAfter: expected integer or null")
            if k in ("keySizes", "sigSizes", "kemSizes") and v is not None and not isinstance(v, dict):
                raise ValueError(f"entries[{i}].{k}: expected object or null")
            proj[k] = v if v is not None else None

        # required minimal set
        for req in ("id", "kind", "enabled"):
            if proj.get(req) in (None, ""):
                raise ValueError(f"entries[{i}].{req}: required")

        leaves.append(Leaf("ENTRY", kind, alg_id, proj))

    # deterministic order
    leaves.sort(key=leaf_sort_key)
    if not leaves:
        raise ValueError("no leaves produced")
    return leaves

# ---------- merkle

def merkle_root(leaves: List[Leaf]) -> Tuple[bytes, List[str]]:
    """Return (root_bytes, hex_hashes_of_leaves_in_order)."""
    leaf_hashes = [lf.hash() for lf in leaves]
    if len(leaf_hashes) == 0:
        raise ValueError("cannot build merkle root of empty leaf set")

    level = leaf_hashes
    while len(level) > 1:
        nxt: List[bytes] = []
        for i in range(0, len(level), 2):
            L = level[i]
            R = level[i+1] if i+1 < len(level) else level[i]  # duplicate last if odd
            nxt.append(sha3_512(b"\x01" + L + R))
        level = nxt
    return level[0], [to_hex(h) for h in leaf_hashes]

# ---------- CLI

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Compute SHA3-512 Merkle root for Animica PQ alg-policy JSON.")
    p.add_argument("policy", help="Path to alg-policy JSON (e.g., pq/alg_policy/example_policy.json)")
    p.add_argument("--dump-leaves", action="store_true", help="Print ordered leaf hashes and labels.")
    p.add_argument("--dump-json", action="store_true", help="Print the normalized leaf JSON payloads.")
    args = p.parse_args(argv)

    with open(args.policy, "rb") as f:
        policy = json.load(f)

    leaves = normalize_policy(policy)
    root, leaf_hex = merkle_root(leaves)

    print(to_hex(root))
    if args.dump_leaves:
        print("\n# Ordered leaves")
        for lf, hx in zip(leaves, leaf_hex):
            print(f"{hx}  {lf.kind}:{lf.sort_kind}:{lf.label}")
    if args.dump_json:
        print("\n# Normalized leaf payloads (in order)")
        for lf in leaves:
            print(json.dumps({
                "kind": lf.kind,
                "sortKind": lf.sort_kind,
                "label": lf.label,
                "payload": lf.payload
            }, sort_keys=True, indent=2))

    return 0

if __name__ == "__main__":
    sys.exit(main())
