"""
omni_sdk.light_client.verify
===========================

Light verification helpers for:
  * Header linkage (parent hash / height / chainId checks)
  * Data-Availability (DA) light proofs against a header's DA root
  * (Optional) minimal beacon/light-proof checks (hash-chain only)

This module is intentionally dependency-light and relies on the same hashing
conventions used across the SDK (SHA3-256). It does **not** attempt to re-encode
headers into consensus CBOR formats; instead it expects a header dict to carry
a `hash` (or `headerHash`) field, or a `raw` CBOR blob (hex) from which the hash
can be derived (sha3_256(raw)).

DA proofs are validated as standard binary Merkle proofs. If a Namespaced
Merkle Tree (NMT) branch is provided, the proof entry objects must carry a
`hash` field; namespace-range enforcement is out of scope for this lightweight
verifier (full-node rules still apply at acceptance).

Compatible with the node components described in:
- spec/header_format.cddl (hashing outside of scope here)
- da/schemas/availability_proof.cddl (shape mirrored loosely)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple, Union

# --- Utilities ---------------------------------------------------------------

# Bytes & hash helpers (fall back to stdlib if omni_sdk.utils isn't available at import time)
try:
    from omni_sdk.utils.bytes import from_hex as _from_hex, to_hex as _to_hex  # type: ignore
except Exception:  # pragma: no cover
    def _from_hex(s: str) -> bytes:
        s = s[2:] if isinstance(s, str) and s.startswith("0x") else s
        return bytes.fromhex(s)
    def _to_hex(b: bytes) -> str:
        return "0x" + bytes(b).hex()

try:
    from omni_sdk.utils.hash import sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib as _hashlib
    def sha3_256(data: bytes) -> bytes:
        return _hashlib.sha3_256(data).digest()


Json = Dict[str, Any]


class LightVerifyError(Exception):
    """Raised on verification failure."""


def _norm_hex_bytes(x: Union[str, bytes, bytearray, memoryview]) -> bytes:
    if isinstance(x, str):
        return _from_hex(x)
    return bytes(x)


def _extract_header_hash(header: Mapping[str, Any]) -> bytes:
    """
    Extract the consensus header hash from a header-like mapping.

    Accepts:
      - header["hash"] or header["headerHash"] as hex string
      - header["raw"] as hex (CBOR bytes), hashed via sha3_256(raw)
    """
    for k in ("hash", "headerHash"):
        v = header.get(k)
        if isinstance(v, str) and v.startswith("0x"):
            return _from_hex(v)
    raw = header.get("raw")
    if isinstance(raw, str) and raw.startswith("0x"):
        return sha3_256(_from_hex(raw))
    raise LightVerifyError("Header missing 'hash'/'headerHash' and 'raw' CBOR; cannot verify linkage")


def _get(header: Mapping[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in header:
            return header[k]
    return None


def _require_hex_str(v: Any, field: str) -> str:
    if not (isinstance(v, str) and v.startswith("0x")):
        raise LightVerifyError(f"Expected hex string for '{field}'")
    return v


def _merkle_combine(left: bytes, right: bytes) -> bytes:
    """
    Combine two child nodes into a parent hash.

    We prefix with a domain byte to avoid ambiguity:
      H = sha3_256(0x01 || left || right)
    """
    return sha3_256(b"\x01" + left + right)


def _merkle_from_branch(leaf_hash: bytes, branch: Sequence[Any], index: int) -> bytes:
    """
    Compute the Merkle root from a leaf hash, a proof branch, and the leaf index.

    Branch entries can be:
      * hex strings (siblings), orientation inferred from index bits
      * dicts with {"hash": "0x…", "pos": "L"|"R"} for explicit orientation
      * dicts with {"hash": "0x…"} orientation inferred as above
    """
    acc = leaf_hash
    idx = int(index)
    # Iterate over siblings at successive depths
    for depth, node in enumerate(branch):
        sib_hex: Optional[str] = None
        pos: Optional[str] = None

        if isinstance(node, str):
            sib_hex = _require_hex_str(node, f"branch[{depth}]")
        elif isinstance(node, Mapping):
            h = node.get("hash", node.get("h"))
            if h is None:
                # Sometimes NMT branches pack hash directly as '0x…'
                # Allow 'node' to be that value by mistake
                if len(node) == 1:
                    only = next(iter(node.values()))
                    if isinstance(only, str) and only.startswith("0x"):
                        sib_hex = only
                if sib_hex is None:
                    raise LightVerifyError(f"branch[{depth}] missing 'hash'")
            else:
                sib_hex = _require_hex_str(h, f"branch[{depth}].hash")
            pos_val = node.get("pos") or node.get("side")
            if isinstance(pos_val, str) and pos_val:
                pos = pos_val.upper()[0]  # 'L' or 'R'
        else:
            raise LightVerifyError(f"branch[{depth}] has unsupported type: {type(node).__name__}")

        sibling = _from_hex(sib_hex)  # type: ignore[arg-type]

        # Determine orientation
        if pos == "L":
            acc = _merkle_combine(sibling, acc)
        elif pos == "R":
            acc = _merkle_combine(acc, sibling)
        else:
            # Infer from index bit at this depth (LSB-first)
            if (idx >> depth) & 1:
                # current acc is a right child -> sibling is left
                acc = _merkle_combine(sibling, acc)
            else:
                acc = _merkle_combine(acc, sibling)

    return acc


# --- Public API --------------------------------------------------------------

@dataclass
class VerifyOptions:
    """Optional knobs for verification."""
    enforce_chain_id: bool = True
    enforce_height_step: bool = True  # require new.height == prev.height + 1
    header_hash_required: bool = False  # if True, 'raw' fallback is disallowed


class LightClient:
    """
    Minimal light verifier for headers (linkage) and DA proofs.

    Example
    -------
        lc = LightClient(trust_anchor=genesis_header)
        lc.verify_header(header_1)
        lc.verify_header(header_2)
        ok = lc.verify_da_proof(header_2, proof_obj)
    """

    def __init__(
        self,
        trust_anchor: Mapping[str, Any],
        *,
        options: Optional[VerifyOptions] = None,
    ) -> None:
        self._opts = options or VerifyOptions()
        self._last = dict(trust_anchor)
        self._chain_id = _get(self._last, "chainId", "chain_id", "chainID")

        # Cache the known hash of the last header (trust anchor)
        self._last_hash = _extract_header_hash(self._last)

    @property
    def head(self) -> Mapping[str, Any]:
        """Return the last verified header (including the trust anchor at start)."""
        return self._last

    @property
    def chain_id(self) -> Any:
        """Chain ID derived from the trust anchor, if present."""
        return self._chain_id

    def verify_header(self, header: Mapping[str, Any]) -> bool:
        """
        Verify a new header against the current head (parent hash, height, chainId).

        On success, advances the light client's head to `header`.
        """
        # Chain ID consistency
        cid = _get(header, "chainId", "chain_id", "chainID")
        if self._opts.enforce_chain_id and self._chain_id is not None and cid is not None:
            if cid != self._chain_id:
                raise LightVerifyError(f"chainId mismatch: expected {self._chain_id}, got {cid}")

        # Heights
        prev_h = _get(self._last, "number", "height")
        curr_h = _get(header, "number", "height")
        if self._opts.enforce_height_step and isinstance(prev_h, int) and isinstance(curr_h, int):
            if curr_h != prev_h + 1:
                raise LightVerifyError(f"height step mismatch: prev={prev_h}, got={curr_h}")

        # Parent hash linkage
        parent_hex = _get(header, "parentHash", "parent_hash", "prevHash")
        parent_hex = _require_hex_str(parent_hex, "parentHash")
        if _from_hex(parent_hex) != self._last_hash:
            raise LightVerifyError("parentHash does not link to last verified header")

        # Current hash presence (or raw→hash)
        if self._opts.header_hash_required and not isinstance(_get(header, "hash", "headerHash"), str):
            raise LightVerifyError("header hash required but not present")

        current_hash = _extract_header_hash(header)  # also validates presence
        # Accept the header – we don't recompute consensus roots here.
        self._last = dict(header)
        self._last_hash = current_hash
        # Fix chain id if it was previously None but present now
        if self._chain_id is None and cid is not None:
            self._chain_id = cid
        return True

    # --- DA proof verification ------------------------------------------------

    def verify_da_proof(self, header: Mapping[str, Any], proof: Mapping[str, Any]) -> bool:
        """
        Verify a DA (Data Availability) light proof against a header.

        Expected inputs
        ---------------
        header:
            Must carry 'daRoot' (or 'da_root') hex string.
        proof:
            {
              "root": "0x…",             # optional echo of expected root
              "samples": [
                  {
                    "leaf": "0x…",       # serialized leaf or commitment bytes
                    "leafHash": "0x…",   # optional; if absent we hash leaf bytes
                    "index": 42,         # leaf index in the tree
                    "branch": ["0x…", {"hash":"0x…","pos":"L"}, ...]  # sibling list
                  },
                  ...
              ]
            }

        Returns
        -------
        True on success; raises LightVerifyError on failure.
        """
        root_hex = _get(header, "daRoot", "da_root", "da")
        root_hex = _require_hex_str(root_hex, "header.daRoot")
        expected_root = _from_hex(root_hex)

        # If proof echoes a root, it must match.
        pr = proof.get("root")
        if pr is not None:
            pr = _require_hex_str(pr, "proof.root")
            if _from_hex(pr) != expected_root:
                raise LightVerifyError("proof.root does not match header.daRoot")

        samples = proof.get("samples")
        if not isinstance(samples, Sequence) or not samples:
            raise LightVerifyError("proof.samples missing or empty")

        for i, s in enumerate(samples):
            if not isinstance(s, Mapping):
                raise LightVerifyError(f"samples[{i}] not an object")
            # Leaf hash – accept prehashed ('leafHash') or hash('leaf')
            leaf_hash_b: bytes
            if "leafHash" in s:
                leaf_hash_b = _from_hex(_require_hex_str(s["leafHash"], f"samples[{i}].leafHash"))
            else:
                leaf_hex = _require_hex_str(s.get("leaf"), f"samples[{i}].leaf")
                leaf_hash_b = sha3_256(_from_hex(leaf_hex))

            index = s.get("index")
            if not isinstance(index, int):
                raise LightVerifyError(f"samples[{i}].index must be int")

            branch = s.get("branch")
            if not isinstance(branch, Sequence):
                raise LightVerifyError(f"samples[{i}].branch must be a list")

            root_calc = _merkle_from_branch(leaf_hash_b, branch, index)
            if root_calc != expected_root:
                raise LightVerifyError(f"samples[{i}] branch does not lead to header.daRoot")

        return True

    # --- Beacon / light-proof (minimal) --------------------------------------

    @staticmethod
    def verify_beacon_light_proof(light_proof: Mapping[str, Any]) -> bool:
        """
        Minimal verification for a randomness beacon light-proof object.

        This checks only the hash-chain linkage portion if present:

            seed_0 --H--> seed_1 --H--> ... --H--> output

        If the object includes a VDF proof, this method does **not** verify it,
        since a secure Wesolowski verifier is out of scope for this lightweight SDK.
        Full nodes remain the source of truth for beacon finalization.

        Returns True if the hash-chain is consistent (or if not provided),
        otherwise raises LightVerifyError.
        """
        chain = light_proof.get("hashChain") or light_proof.get("chain")
        output_hex = light_proof.get("output")
        if chain is None and output_hex is None:
            # Nothing to verify; treat as a no-op success.
            return True
        if output_hex is None:
            raise LightVerifyError("beacon light-proof missing 'output'")
        output = _from_hex(_require_hex_str(output_hex, "output"))

        if chain:
            if not isinstance(chain, Sequence) or len(chain) == 0:
                raise LightVerifyError("hashChain must be a non-empty list")
            cursor = _from_hex(_require_hex_str(chain[0], "hashChain[0]"))
            for idx in range(1, len(chain)):
                cursor = sha3_256(cursor)
                expected = _from_hex(_require_hex_str(chain[idx], f"hashChain[{idx}]"))
                if cursor != expected:
                    raise LightVerifyError(f"hashChain mismatch at step {idx}")
            if cursor != output:
                raise LightVerifyError("hashChain tail does not match stated 'output'")

        # If no chain is provided, we can't validate output further here.
        return True


# Backwards-compatible function alias (common ergonomic import)
def verify_light_proof(header: Mapping[str, Any], proof: Mapping[str, Any]) -> bool:
    """
    Convenience function to verify a DA light proof against a header.

    Equivalent to:
        LightClient(trust_anchor=header).verify_da_proof(header, proof)

    (No head advancement is kept with this helper.)
    """
    lc = LightClient(trust_anchor=header)
    return lc.verify_da_proof(header, proof)


__all__ = ["LightClient", "verify_light_proof", "VerifyOptions", "LightVerifyError"]
