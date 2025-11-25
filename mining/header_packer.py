from __future__ import annotations

"""
Animica mining.header_packer
---------------------------

Packs a candidate block from a header *template* plus a chosen set of txs and proofs.
This is the last step before the scanning loop updates `nonce` and `mixSeed`.

Design goals:
- Deterministic, side-effect free.
- Minimal dependencies; gracefully degrade if optional modules are missing.
- Strict about byte encodings (txs as CBOR bytes; proofs as canonical CBOR or canonical JSON).

Inputs
------
`template`: a mapping produced by mining.templates.TemplateBuilder (or equivalent) with at least:
    {
      "header_base": {           # prefilled consensus/header fields except roots/nonce
        "parentHash": "0x..",
        "number": <int>,         # height
        "chainId": <int>,
        "theta": <int>,          # Θ micro-nats or similar fixed-point
        "policyRoots": { ... },  # policy/alg roots if applicable
        "timestamp": <int>,      # optional
        # optional fields allowed by spec/header_format.cddl (extra roots or metadata)
      },
      "mixSeed": "0x..",         # domain seed included in header (separately from nonce)
      "coinbase": "anim1...",    # optional, may be needed by execution later
    }

`txs`:    list[bytes]  — CBOR-encoded transactions.
`proofs`: list[dict]   — proof envelopes (already schema-validated upstream).

Outputs
-------
A dictionary shaped like core/types/block.Block (header + txs + proofs).
Header roots are computed as follows:
- txsRoot    = merkle_root(hash(tx_bytes) for tx in txs)
- proofsRoot = merkle_root(hash(proof_receipt(proof)) for proof in proofs)
- receiptsRoot is omitted here (filled by execution after apply), or set to zero32 if required by spec.

Notes
-----
- We try to use core.utils.merkle.merkle_root and proofs.receipts helpers if available.
- Hashing uses SHA3-256 (spec'd in spec/domains.yaml). If blake3 is available in core.utils.hash we ignore it here.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ────────────────────────────────────────────────────────────────────────
# Best-effort imports from core/ & proofs/
# ────────────────────────────────────────────────────────────────────────

_KECCAK = None
_SHA3_256 = None

try:
    from core.utils.hash import sha3_256  # type: ignore
    _SHA3_256 = sha3_256
except Exception:
    try:
        import hashlib

        def _sha3_256_fallback(b: bytes) -> bytes:
            return hashlib.sha3_256(b).digest()

        _SHA3_256 = _sha3_256_fallback
    except Exception:  # pragma: no cover
        pass

try:
    from core.utils.merkle import merkle_root as merkle_root_bytes  # type: ignore
except Exception:
    merkle_root_bytes = None  # type: ignore

try:
    # Canonical CBOR encoder/decoder (deterministic map order)
    from core.encoding.cbor import encode as cbor_encode  # type: ignore
except Exception:
    cbor_encode = None  # type: ignore

try:
    # If available, build compact receipts from proof envelopes
    from proofs.receipts import receipt_leaf_bytes  # type: ignore
except Exception:
    receipt_leaf_bytes = None  # type: ignore

ZERO32 = b"\x00" * 32
log = logging.getLogger("mining.header_packer")


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────

def _b32(hex_or_bytes: Any) -> bytes:
    if isinstance(hex_or_bytes, (bytes, bytearray)):
        b = bytes(hex_or_bytes)
        return b if len(b) == 32 else b.rjust(32, b"\x00")
    if isinstance(hex_or_bytes, str) and hex_or_bytes.startswith("0x"):
        h = hex_or_bytes[2:]
        if len(h) % 2:
            h = "0" + h
        b = bytes.fromhex(h)
        return b if len(b) == 32 else b.rjust(32, b"\x00")
    raise TypeError("expected 32-byte value or 0x… hex string")


def _to_hex(b: bytes) -> str:
    return "0x" + b.hex()


def _hash_bytes(b: bytes) -> bytes:
    if _SHA3_256 is None:  # pragma: no cover
        raise RuntimeError("sha3_256 not available")
    return _SHA3_256(b)


def _hash_struct_as_cbor(obj: Any) -> bytes:
    if cbor_encode is not None:
        return _hash_bytes(cbor_encode(obj))
    # Canonicalize JSON as a fallback (sorted keys, UTF-8, no spaces)
    js = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _hash_bytes(js)


def _merkle_root(hashes: List[bytes]) -> bytes:
    if not hashes:
        return ZERO32
    if merkle_root_bytes is not None:
        return merkle_root_bytes(hashes)
    # Simple canonical binary Merkle (rehash concatenation, left-pad singletons)
    level = hashes[:]
    while len(level) > 1:
        nxt: List[bytes] = []
        it = iter(level)
        for a in it:
            try:
                b = next(it)
            except StopIteration:
                b = a
            nxt.append(_hash_bytes(a + b))
        level = nxt
    return level[0]


# ────────────────────────────────────────────────────────────────────────
# Receipts & roots
# ────────────────────────────────────────────────────────────────────────

def txs_root_from_bytes(txs: Iterable[bytes]) -> bytes:
    leaves = [_hash_bytes(tx) for tx in txs]
    return _merkle_root(leaves)


def proofs_root_from_envelopes(proofs: Iterable[Dict[str, Any]]) -> bytes:
    leaves: List[bytes] = []
    for p in proofs:
        if receipt_leaf_bytes is not None:
            try:
                leaves.append(receipt_leaf_bytes(p))
                continue
            except Exception as e:
                log.debug("receipt_leaf_bytes failed; falling back to hashing envelope: %s", e)
        # Fallback: hash the whole envelope deterministically
        leaves.append(_hash_struct_as_cbor(p))
    return _merkle_root(leaves)


# ────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────

@dataclass
class PackResult:
    header: Dict[str, Any]
    txs: List[bytes]
    proofs: List[Dict[str, Any]]


def pack_candidate_block(
    template: Dict[str, Any],
    txs: List[bytes],
    proofs: List[Dict[str, Any]],
    *,
    include_receipts_root: bool = False,
) -> PackResult:
    """
    Build the candidate block dict:
      {
        "header": { ... computed roots, mixSeed, nonce=0x00.. },
        "txs": [... CBOR bytes ...],
        "proofs": [... envelopes ...],
      }

    - Does **not** set a final nonce. Scanning loop writes/updates it.
    - mixSeed is passed through from template (domain-bound randomness for nonce).
    - receiptsRoot is omitted by default (filled after execution). If include_receipts_root=True,
      it will be set to ZERO32 as a placeholder.
    """
    if "header_base" not in template:
        raise ValueError("template missing 'header_base'")
    base = dict(template["header_base"])
    mix_seed = template.get("mixSeed") or template.get("mix_seed")
    if mix_seed is None:
        raise ValueError("template missing 'mixSeed'")

    # Compute roots
    tx_root = txs_root_from_bytes(txs)
    pr_root = proofs_root_from_envelopes(proofs)

    # Assemble header
    header: Dict[str, Any] = {}
    header.update(base)
    header["txsRoot"] = _to_hex(tx_root)
    header["proofsRoot"] = _to_hex(pr_root)
    if include_receipts_root:
        header["receiptsRoot"] = _to_hex(ZERO32)
    # mixSeed included in header (separate from nonce)
    header["mixSeed"] = mix_seed
    # Nonce is left empty/zero; the scanning loop will fill it before submit.
    header.setdefault("nonce", "0x" + ("00" * 32))

    return PackResult(header=header, txs=txs, proofs=proofs)


# ────────────────────────────────────────────────────────────────────────
# Debug/CLI
# ────────────────────────────────────────────────────────────────────────

def _load_bytes_list(files: List[str]) -> List[bytes]:
    out: List[bytes] = []
    for f in files:
        with open(f, "rb") as fh:
            out.append(fh.read())
    return out


def _load_json_list(files: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for f in files:
        with open(f, "rb") as fh:
            out.append(json.loads(fh.read()))
    return out


if __name__ == "__main__":  # pragma: no cover
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Pack a candidate block from a header template + txs + proofs.")
    ap.add_argument("--template", required=True, help="JSON file containing the header template dict")
    ap.add_argument("--tx", action="append", default=[], help="CBOR tx file (repeatable)")
    ap.add_argument("--tx-list", help="Text file with newline-separated paths to CBOR tx files")
    ap.add_argument("--proof", action="append", default=[], help="JSON proof envelope file (repeatable)")
    ap.add_argument("--proof-list", help="Text file with newline-separated paths to proof JSON files")
    ap.add_argument("--out", required=True, help="Write candidate block JSON here")
    ap.add_argument("--receipts-zero", action="store_true", help="Include receiptsRoot=0x00.. placeholder")
    args = ap.parse_args()

    try:
        with open(args.template, "rb") as fh:
            template = json.loads(fh.read())
    except Exception as e:
        print(f"Failed to read template: {e}", file=sys.stderr)
        sys.exit(2)

    tx_files = list(args.tx)
    if args.tx_list:
        with open(args.tx_list, "r", encoding="utf-8") as fh:
            tx_files += [ln.strip() for ln in fh if ln.strip()]
    proof_files = list(args.proof)
    if args.proof_list:
        with open(args.proof_list, "r", encoding="utf-8") as fh:
            proof_files += [ln.strip() for ln in fh if ln.strip()]

    txs = _load_bytes_list(tx_files)
    proofs = _load_json_list(proof_files)

    packed = pack_candidate_block(template, txs, proofs, include_receipts_root=args.receipts_zero)
    out_obj = {
        "header": packed.header,
        "txs": ["0x" + b.hex() for b in packed.txs],
        "proofs": packed.proofs,
    }
    with open(args.out, "wb") as fh:
        fh.write(json.dumps(out_obj, sort_keys=True, indent=2).encode("utf-8"))
    print(f"Wrote candidate block → {args.out}")
