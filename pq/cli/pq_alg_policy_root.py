#!/usr/bin/env python3
from __future__ import annotations

"""
Build the Animica PQ alg-policy Merkle root from a JSON policy document.

Inputs
  • Policy JSON file (see spec/alg_policy.schema.json).
  • Optional JSON-Schema path for validation.
  • Optional proof query to produce a Merkle proof for a specific algorithm.

Hashing & Merkle (v1)
  • Leaf hash   = SHA3-512("animica|alg-policy|leaf|v1|" || canonical_json(entry))
  • Node hash   = SHA3-512("animica|alg-policy|node|v1|" || L || R)
  • Empty root  = SHA3-512("animica|alg-policy|empty|v1|")
  • Canonical JSON is utf-8, sorted keys, no whitespace, deterministic floats/ints.
  • Leaf order  = sort by (entry["id"], entry["name"]) ascending before building the tree.
  • If odd number of leaves at any level, the last node is duplicated (“BLAKE-style” padding).

Outputs
  • Hex root to stdout by default.
  • --json prints a structured JSON blob (root, leaves, tree stats).
  • --print-tree dumps a text tree with hashes (debug).
  • --proof <id-or-name> prints a JSON Merkle proof for the selected leaf.
  • --out <path> writes the hex root to a file (and still prints stdout unless --quiet).

Usage
  python -m pq.cli.pq_alg_policy_root --in pq/alg_policy/example_policy.json
  python -m pq.cli.pq_alg_policy_root --in - --json < policy.json
  python -m pq.cli.pq_alg_policy_root --in policy.json --proof dilithium3 --json
  python -m pq.cli.pq_alg_policy_root --in policy.json --schema spec/alg_policy.schema.json

Notes
  • This tool does NOT fetch domain constants; it hardcodes v1 strings above to avoid
    cross-package import cycles. Keep in sync with spec/alg_policy.schema.json if you rev.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from pq.py.utils.hash import sha3_512
except Exception as e:  # pragma: no cover
    raise SystemExit(
        f"FATAL: pq hashing utils not available: {e}\n"
        "Hint: run from repo root or set PYTHONPATH=~/animica"
    )

# ---------- Canonical JSON ----------------------------------------------------


def _canon_dumps(obj: Any) -> bytes:
    """
    Deterministic JSON (RFC-8785-ish spirit, but minimal):
      - sort keys
      - no spaces
      - ensure_ascii=False
      - integers/floats pass through; no NaN/Inf allowed
    """

    def _default(o):
        raise TypeError(f"Non-serializable type in policy: {type(o)}")

    s = json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_default,
        allow_nan=False,
    )
    return s.encode("utf-8")


# ---------- Merkle (v1) ------------------------------------------------------

DOM_LEAF = b"animica|alg-policy|leaf|v1|"
DOM_NODE = b"animica|alg-policy|node|v1|"
DOM_EMPTY = b"animica|alg-policy|empty|v1|"


@dataclass
class Leaf:
    idx: int  # position in sorted sequence
    key: Tuple[Any, Any]  # (id, name)
    entry: Dict[str, Any]
    h: bytes  # leaf hash


def _leaf_hash(entry: Dict[str, Any]) -> bytes:
    return sha3_512(DOM_LEAF + _canon_dumps(entry))


def _node_hash(left: bytes, right: bytes) -> bytes:
    return sha3_512(DOM_NODE + left + right)


def _empty_root() -> bytes:
    return sha3_512(DOM_EMPTY)


def _build_leaves(entries: List[Dict[str, Any]]) -> List[Leaf]:
    # Validate required fields for sorting and reproducibility
    prepared: List[Tuple[Tuple[Any, Any], Dict[str, Any]]] = []
    for e in entries:
        if "id" not in e or "name" not in e:
            raise SystemExit("Each entry must contain 'id' and 'name'")
        prepared.append(((e["id"], e["name"]), e))

    prepared.sort(key=lambda kv: kv[0])

    leaves: List[Leaf] = []
    for i, (key, entry) in enumerate(prepared):
        leaves.append(Leaf(idx=i, key=key, entry=entry, h=_leaf_hash(entry)))
    return leaves


def _build_tree_level(nodes: List[bytes]) -> List[bytes]:
    if not nodes:
        return []
    out: List[bytes] = []
    n = len(nodes)
    i = 0
    while i < n:
        if i + 1 < n:
            out.append(_node_hash(nodes[i], nodes[i + 1]))
            i += 2
        else:
            # duplicate last
            out.append(_node_hash(nodes[i], nodes[i]))
            i += 1
    return out


def _build_merkle_root(leaves: List[Leaf]) -> Tuple[bytes, List[List[bytes]]]:
    if not leaves:
        return _empty_root(), []

    level: List[bytes] = [lf.h for lf in leaves]
    tree: List[List[bytes]] = [level]
    while len(level) > 1:
        level = _build_tree_level(level)
        tree.append(level)
    return level[0], tree  # root, full tree by levels (0 = leaves)


# ---------- Proofs ------------------------------------------------------------


@dataclass
class ProofItem:
    sibling: str  # hex
    side: str  # "L" or "R"


def _merkle_proof(
    leaves: List[Leaf], tree: List[List[bytes]], leaf_idx: int
) -> List[ProofItem]:
    """
    Build a simple audit path from leaf index to root. For an odd number of nodes at a level,
    the last node is duplicated; in that case if the target is the last node, sibling==self.
    """
    if not tree:
        return []
    proof: List[ProofItem] = []
    idx = leaf_idx
    for level in range(len(tree) - 1):  # stop before root level
        nodes = tree[level]
        # compute sibling index
        if idx % 2 == 0:
            sib_idx = idx + 1
            side = "R"
        else:
            sib_idx = idx - 1
            side = "L"
        if sib_idx >= len(nodes):
            # duplicate case (sibling is self)
            sib_idx = idx
        sib = nodes[sib_idx]
        proof.append(ProofItem(sibling=sib.hex(), side=side))
        # move up
        idx //= 2
    return proof


def _find_leaf_index(leaves: List[Leaf], q: str) -> int:
    """
    Find a leaf by 'id' or 'name'. We try:
      1) exact match on id (string compare)
      2) exact match on name (string compare)
    """
    # try id first
    for lf in leaves:
        if str(lf.entry.get("id")) == q:
            return lf.idx
    # then name
    for lf in leaves:
        if str(lf.entry.get("name")) == q:
            return lf.idx
    raise SystemExit(f"No entry matches id/name '{q}'")


# ---------- Schema validation (optional) --------------------------------------


def _maybe_validate_schema(doc: Dict[str, Any], schema_path: Optional[Path]) -> None:
    if not schema_path:
        return
    try:
        import jsonschema  # type: ignore
    except Exception as e:
        raise SystemExit(f"--schema was provided but jsonschema is not installed: {e}")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=doc, schema=schema)


# ---------- I/O & CLI ---------------------------------------------------------


def _load_policy(path: str) -> Dict[str, Any]:
    if path == "-":
        data = sys.stdin.read()
    else:
        data = Path(path).read_text(encoding="utf-8")
    try:
        doc = json.loads(data)
    except json.JSONDecodeError as e:
        raise SystemExit(f"Invalid JSON: {e}")
    if (
        not isinstance(doc, dict)
        or "entries" not in doc
        or not isinstance(doc["entries"], list)
    ):
        raise SystemExit("Policy JSON must be an object with an 'entries' array")
    return doc


def _print_tree(leaves: List[Leaf], tree: List[List[bytes]]) -> None:
    print("# Merkle Tree (level 0 = leaves)")
    for lvl, nodes in enumerate(tree):
        if lvl == 0:
            print(f"level {lvl} (leaves, n={len(nodes)}):")
            for lf, h in zip(leaves, nodes):
                print(
                    f"  [{lf.idx:02d}] id={lf.entry['id']!r} name={lf.entry['name']!r}  h={h.hex()}"
                )
        else:
            print(f"level {lvl} (n={len(nodes)}):")
            for i, h in enumerate(nodes):
                print(f"  [{i:02d}] {h.hex()}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build PQ alg-policy Merkle root (v1).")
    ap.add_argument(
        "--in", dest="inp", required=True, help="Policy JSON path or '-' for stdin"
    )
    ap.add_argument(
        "--schema", type=Path, help="Optional JSON-Schema path to validate input"
    )
    ap.add_argument(
        "--print-tree", action="store_true", help="Print the Merkle tree (debug)"
    )
    ap.add_argument(
        "--proof", help="Emit a Merkle proof for entry with this id or name"
    )
    ap.add_argument(
        "--json", action="store_true", help="Emit JSON instead of plain hex"
    )
    ap.add_argument("--out", type=Path, help="Write hex root to file")
    ap.add_argument("--quiet", action="store_true", help="Don’t print root to stdout")
    args = ap.parse_args(argv)

    doc = _load_policy(args.inp)
    _maybe_validate_schema(doc, args.schema)

    leaves = _build_leaves(doc["entries"])
    root, tree = _build_merkle_root(leaves)

    proof_json = None
    if args.proof is not None:
        idx = _find_leaf_index(leaves, args.proof)
        proof = _merkle_proof(leaves, tree, idx)
        proof_json = {
            "query": args.proof,
            "index": idx,
            "leaf": leaves[idx].h.hex(),
            "path": [p.__dict__ for p in proof],
            "root": root.hex(),
            "domain": {
                "leaf": DOM_LEAF.decode("utf-8"),
                "node": DOM_NODE.decode("utf-8"),
                "empty": DOM_EMPTY.decode("utf-8"),
            },
        }

    if args.print_tree:
        _print_tree(leaves, tree)

    if args.json:
        out = {
            "root": root.hex(),
            "count": len(leaves),
            "entries": [
                {
                    "idx": lf.idx,
                    "id": lf.entry["id"],
                    "name": lf.entry["name"],
                    "hash": lf.h.hex(),
                }
                for lf in leaves
            ],
            "proof": proof_json,
        }
        print(json.dumps(out, indent=2))
    else:
        if not args.quiet:
            print(root.hex())

    if args.out:
        args.out.write_text(root.hex() + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
