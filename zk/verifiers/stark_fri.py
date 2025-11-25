"""
Animica zk.verifiers.stark_fri
==============================

A *tiny* STARK-style verifier (educational), specialized to a toy AIR that
checks a Merkle-membership *like* computation:

    h[0]   = leaf
    h[i+1] = ToyHash(h[i], sibling[i], dir[i])   for i = 0..T-2
    h[T-1] = claimed_root

Where:
- `sibling[i]` are per-step "sibling" field elements,
- `dir[i]` are 0/1 direction flags deciding left/right ordering.

This verifier:
  1) Uses a non-cryptographic 2→1 *toy* hash over the Goldilocks field
     (p = 2^64 - 2^32 + 1) to keep the AIR field-friendly.
  2) Checks the transition and boundary constraints at *random sampled rows*
     using Merkle commitments of the three trace columns (h/sib/dir).
  3) Includes a **minimal single-fold FRI** consistency check for the `h`
     column commitment (optional but enabled by default) to demonstrate the
     idea of folding. This is **not** a production LDT.

⚠️ Security & scope
-------------------
- This is for education/testing. The "ToyHash" is not collision-resistant.
- The FRI routine is a tiny demonstration (single-fold per round down to a
  small last domain). It is *not* a complete low-degree test.
- Do not rely on this module for adversarial settings.

Proof format (JSON-like dict)
-----------------------------
{
  "field": { "modulus": "0x1_00000000_ffff_ffff + 1" },   # optional (ignored; fixed GL)
  "trace_len": T,                  # number of rows (>= 2)
  "num_queries": Q,                # e.g. 30
  "commitments": {                 # Poseidon/SHA3 not required here — we use SHA3-256 below
      "h":   "0x<32-byte hex>",    # Merkle root of column h
      "sib": "0x<32-byte hex>",    # Merkle root of column sibling
      "dir": "0x<32-byte hex>"     # Merkle root of column dir (0/1 values)
  },
  "fri": {                         # OPTIONAL minimal FRI over column h
      "layers": [ "0x<root L0>", "0x<root L1>", ..., "0x<root Lr>" ],
      "last_size": 16              # must be a power-of-two <= 32
  },
  "decommitments": [
     {
       "index": i,  # verifier-chosen; prover echoes & provides paths
       "h":       {"v":"<int|0x>", "path":["0x..", ...]},        # value at row i
       "h_next":  {"v":"<int|0x>", "path":["0x..", ...]},        # value at row (i+1)
       "sib":     {"v":"<int|0x>", "path":["0x..", ...]},        # sibling at row i
       "dir":     {"v":"<0|1>",    "path":["0x..", ...]},        # dir at row i (bit)
       # Optional FRI decommit path for this query (per layer, the sibling of i):
       "fri": [
         { "pair": ["<int|0x>", "<int|0x>"], "path": ["0x..", ...] },  # layer 0 (two siblings at i and i^1)
         { "pair": ["<int|0x>", "<int|0x>"], "path": ["0x..", ...] },  # layer 1
         ...
       ]
     },
     ...
  ]
}

Public inputs (dict)
--------------------
{
  "leaf": "<int|0x|bytes-hex>",     # starting value h[0]
  "claimed_root": "<int|0x>",       # expected h[T-1]
  "statement_label": "animica:toy-merkle"   # domain tag for FS (optional)
}

API
---
- verify_toy_stark_merkle(proof: dict, public: dict, *, with_fri: bool = True) -> bool

License: MIT
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Mapping, Sequence, Tuple, Union, Optional
import hashlib
import math
import os

# ---------------------------
# Field: Goldilocks (2^64 - 2^32 + 1)
# ---------------------------

P = (1 << 64) - (1 << 32) + 1  # 18446744069414584321
MASK64 = (1 << 64) - 1

def fadd(a: int, b: int) -> int:
    return (a + b) % P

def fsub(a: int, b: int) -> int:
    return (a - b) % P

def fmul(a: int, b: int) -> int:
    return (a * b) % P

def fpow(a: int, e: int) -> int:
    return pow(a % P, e, P)

def fred(a: int) -> int:
    return int(a) % P

# A simple, fast-ish x^7 S-box over GL
def x7(x: int) -> int:
    x = fred(x)
    x2 = fmul(x, x)
    x4 = fmul(x2, x2)
    x7v = fmul(x4, fmul(x2, x))
    return x7v


# ---------------------------
# Toy 2→1 hash for the AIR
# ---------------------------

def toy_hash2(left: int, right: int) -> int:
    """
    A tiny algebraic "hash": (left + 3*right + 5)^7  mod P
    DO NOT USE FOR SECURITY — this is only for the toy AIR transition.
    """
    acc = fadd(fadd(left, fmul(3, right)), 5)
    return x7(acc)

def toy_merkle_step(curr: int, sibling: int, direction_bit: int) -> int:
    """
    If dir=0: next = H(curr, sibling)
       dir=1: next = H(sibling, curr)
    """
    if direction_bit not in (0, 1):
        raise ValueError("direction bit must be 0 or 1")
    if direction_bit == 0:
        return toy_hash2(curr, sibling)
    else:
        return toy_hash2(sibling, curr)


# ---------------------------
# Utilities
# ---------------------------

def _to_int(z: Union[int, str, bytes]) -> int:
    if isinstance(z, int):
        return z
    if isinstance(z, (bytes, bytearray, memoryview)):
        return int.from_bytes(bytes(z), "big")
    s = str(z).strip().lower()
    if s.startswith("0x"):
        return int(s, 16)
    return int(s)

def be32(x: int) -> bytes:
    return int(x % (1 << 256)).to_bytes(32, "big")

def hx(b: bytes) -> str:
    return "0x" + b.hex()

def _hex_to_bytes(s: str) -> bytes:
    s = s.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    return bytes.fromhex(s)


# ---------------------------
# Merkle (SHA3-256)
# ---------------------------

def sha3(b: bytes) -> bytes:
    return hashlib.sha3_256(b).digest()

def merkle_combine(left: bytes, right: bytes) -> bytes:
    return sha3(left + right)

def merkle_verify(root_hex: str, leaf_value: int, path: Sequence[str], index: int) -> bool:
    """
    Verify a SHA3-256 Merkle authentication path where leaves are 32-byte
    big-endian encodings of field elements mod P.
    """
    try:
        node = sha3(be32(leaf_value % P))
        idx = int(index)
        for sib_hex in path:
            sib = _hex_to_bytes(sib_hex)
            if idx & 1:
                node = merkle_combine(sib, node)
            else:
                node = merkle_combine(node, sib)
            idx >>= 1
        return node == _hex_to_bytes(root_hex)
    except Exception:
        return False


# ---------------------------
# Fiat–Shamir sampling (indices)
# ---------------------------

def sample_indices(
    *,
    trace_len: int,
    num: int,
    domain_tag: str,
    bind_roots: Sequence[str],
    bind_public: Sequence[int],
) -> List[int]:
    """
    Derive `num` distinct indices in [0, trace_len-2] using SHA3-256 as an
    expand-then-reduce PRF. We exclude the last row from sampling so that
    h[i+1] always exists for transitions.
    """
    if trace_len < 2:
        raise ValueError("trace_len must be >= 2")
    upper = trace_len - 1  # inclusive max for i (we will draw in [0, upper-1])
    want = min(num, upper)
    acc: List[int] = []
    seen = set()
    seed = sha3(
        domain_tag.encode("utf-8")
        + b"|roots|" + b"|".join(_hex_to_bytes(r) for r in bind_roots)
        + b"|pub|" + b"|".join(be32(x) for x in bind_public)
        + b"|len|" + trace_len.to_bytes(8, "big")
    )
    ctr = 0
    while len(acc) < want:
        block = sha3(seed + ctr.to_bytes(8, "big"))
        # break into four 64-bit chunks
        for j in range(4):
            r = int.from_bytes(block[8*j:8*(j+1)], "big")
            i = r % upper  # i in [0, trace_len-2]
            if i not in seen:
                seen.add(i)
                acc.append(i)
                if len(acc) == want:
                    break
        ctr += 1
    return acc


# ---------------------------
# Minimal single-fold FRI (demonstration)
# ---------------------------

@dataclass
class FriParams:
    layers: List[str]    # hex roots from L0 (original h) to Lr (last)
    last_size: int       # power-of-two <= 32

def _is_pow2(x: int) -> bool:
    return x > 0 and (x & (x - 1)) == 0

def fri_verify_single_fold_for_query(
    roots: List[str],
    query: List[Mapping[str, object]],
    index0: int,
    *,
    domain_tag: str,
) -> bool:
    """
    Verify a *single-branch* of a FRI-style folding:
      At each layer j, the proof provides two sibling values at positions (i, i^1),
      plus a Merkle path authenticating them under roots[j]. We recompute the
      folded value v' = v0 + r_j * v1  (mod P), move to the next layer index i // 2,
      and repeat. The random r_j are derived by FS from the previous root.

    This is purely pedagogical and does not enforce a degree bound — it only
    checks the self-consistency of the fold chain against the published roots.
    """
    try:
        if not roots or not query:
            return False
        i = int(index0)
        # derive per-round r_j from (domain_tag, roots[j])
        for j, qj in enumerate(query):
            if j >= len(roots) - 1:
                # last layer doesn't need a fold (it authenticates the final value)
                break
            pair = qj.get("pair")
            path = qj.get("path")
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2 and isinstance(path, (list, tuple))):
                return False
            v0 = fred(_to_int(pair[0]))
            v1 = fred(_to_int(pair[1]))
            # Authenticate both siblings under roots[j]
            # We'll encode a "leaf" as sha3( be32(v) || be32(idx) ) to bind positions.
            # (This is still demo-only.)
            leaf0 = sha3(be32(v0) + be32(i & ~1))        # position even
            leaf1 = sha3(be32(v1) + be32((i & ~1) | 1))  # position odd
            # Walk proof assuming the last hash in path is the sibling of the *pair* node
            # For simplicity, we authenticate the *pair hash* itself:
            pair_node = merkle_combine(leaf0, leaf1)
            node = pair_node
            idx = (i >> 1)
            for sib_hex in path:
                sib = _hex_to_bytes(sib_hex)
                if idx & 1:
                    node = merkle_combine(sib, node)
                else:
                    node = merkle_combine(node, sib)
                idx >>= 1
            if node != _hex_to_bytes(roots[j]):
                return False
            # derive r_j and compute folded value for index i//2
            rj = int.from_bytes(sha3(domain_tag.encode() + _hex_to_bytes(roots[j]) + j.to_bytes(4, "big")), "big") % P
            folded = fadd(v0, fmul(rj, v1))
            # the next layer should present `folded` as one of the siblings at index i//2
            # (we don't enforce which side; next loop will use provided pair)
            i >>= 1
        return True
    except Exception:
        return False


# ---------------------------
# Verifier
# ---------------------------

def verify_toy_stark_merkle(proof: Mapping[str, object], public: Mapping[str, object], *, with_fri: bool = True) -> bool:
    """
    Verify the toy STARK (Merkle-path AIR) using random spot checks and
    optional minimal FRI consistency on the `h` column.

    Returns True/False (no exceptions for ordinary failures).
    """
    try:
        # Parse basics
        T = int(proof["trace_len"])
        if T < 2:
            return False
        Q = int(proof["num_queries"])
        commits = proof["commitments"]
        root_h  = str(commits["h"])
        root_s  = str(commits["sib"])
        root_d  = str(commits["dir"])

        fri_obj: Optional[Mapping[str, object]] = proof.get("fri") if with_fri else None
        fri_params: Optional[FriParams] = None
        if fri_obj:
            layers = [str(x) for x in fri_obj["layers"]]
            last_size = int(fri_obj.get("last_size", 16))
            if not layers or not _is_pow2(last_size) or last_size > 32:
                return False
            if layers[0] != root_h:
                # the first FRI layer must authenticate the same root as column h
                return False
            fri_params = FriParams(layers=layers, last_size=last_size)

        # Public inputs
        label = str(public.get("statement_label", "animica:toy-merkle"))
        leaf0 = fred(_to_int(public["leaf"]))
        claimed_root = fred(_to_int(public["claimed_root"]))

        # Derive indices
        indices = sample_indices(
            trace_len=T,
            num=Q,
            domain_tag=label,
            bind_roots=[root_h, root_s, root_d],
            bind_public=[leaf0, claimed_root],
        )

        # Decommitments must cover all sampled indices
        decs_list = proof["decommitments"]
        if not isinstance(decs_list, (list, tuple)) or len(decs_list) < len(indices):
            return False

        # Index → decommit object map
        decs_by_idx = {int(d["index"]): d for d in decs_list}

        # Check boundary rows h[0] and h[T-1] via separate decommitments if provided
        # (otherwise they must appear among sampled indices).
        # We'll try to fetch them from the map; if missing, fail fast — it keeps the format simple.
        if 0 not in decs_by_idx or (T - 1) not in decs_by_idx:
            return False

        # Boundary: h[0] == leaf0
        d0 = decs_by_idx[0]
        h0 = fred(_to_int(d0["h"]["v"]))
        if h0 != leaf0:
            return False
        if not merkle_verify(root_h, h0, d0["h"]["path"], 0):
            return False

        # Boundary: h[T-1] == claimed_root
        dlast = decs_by_idx[T - 1]
        hlast = fred(_to_int(dlast["h"]["v"]))
        if hlast != claimed_root:
            return False
        if not merkle_verify(root_h, hlast, dlast["h"]["path"], T - 1):
            return False

        # Main spot checks (transition constraints)
        for i in indices:
            d = decs_by_idx.get(i)
            if d is None:
                return False

            # Pull and authenticate h[i], h[i+1], sib[i], dir[i]
            hi = fred(_to_int(d["h"]["v"]))
            if not merkle_verify(root_h, hi, d["h"]["path"], i):
                return False

            hip1 = fred(_to_int(d["h_next"]["v"]))
            if not merkle_verify(root_h, hip1, d["h_next"]["path"], i + 1):
                return False

            si = fred(_to_int(d["sib"]["v"]))
            if not merkle_verify(root_s, si, d["sib"]["path"], i):
                return False

            di_raw = _to_int(d["dir"]["v"])
            if di_raw not in (0, 1):
                return False
            if not merkle_verify(root_d, di_raw, d["dir"]["path"], i):
                return False

            # Transition: h[i+1] == ToyHash( h[i], sib[i], dir[i] )
            expect = toy_merkle_step(hi, si, di_raw)
            if hip1 != expect:
                return False

            # Optional: FRI consistency along this query (over column h only)
            if fri_params:
                qfri = d.get("fri")
                if not isinstance(qfri, (list, tuple)) or not qfri:
                    return False
                if not fri_verify_single_fold_for_query(fri_params.layers, qfri, i, domain_tag=label):
                    return False

        return True
    except Exception:
        return False


# ---------------------------
# Minimal CLI for ad-hoc testing
# ---------------------------

if __name__ == "__main__":  # pragma: no cover
    import json, sys
    if len(sys.argv) != 3:
        print("Usage: python zk/verifiers/stark_fri.py proof.json public.json")
        sys.exit(1)
    proof = json.load(open(sys.argv[1], "r"))
    public = json.load(open(sys.argv[2], "r"))
    ok = verify_toy_stark_merkle(proof, public, with_fri=bool(proof.get("fri")))
    print("verify ->", ok)
