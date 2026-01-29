"""
Animica • DA • Sample Verifier

Verifies a batch of Data Availability Sampling (DAS) proofs against an NMT/DA
commitment root. Designed to be flexible with payload shapes returned by a DA
retrieval service.

Primary entrypoint
------------------
verify_samples(root: bytes, payload: Mapping[str, Any]) -> dict

Returns a dict:
{
  "ok_indices":  [int, ...],   # samples that verified
  "bad_indices": [int, ...],   # samples that failed verification
}

Accepted payload shapes
-----------------------
We accept several common, JSON-friendly shapes. At minimum we need:
  - indices:           list[int] (sample positions)
  - proofs/branches:   list[...] (one per index, see below)
  - Either:
      * leaves/raw:    list[hexstr|bytes] (raw leaf data to encode), AND "namespace"
    or
      * leaf_hashes:   list[hexstr|bytes] (pre-encoded leaf node hash)

Examples:

1) With raw leaves (preferred):
{
  "namespace": 24,
  "indices": [10, 50, 4095],
  "leaves":  ["0x...", "0x...", "0x..."],    # raw shard bytes (not hashed/encoded)
  "proofs":  [
     {"branch": ["0x..", "0x..", ...]},      # sibling nodes from leaf→root
     {"branch": ["0x..", "0x..", ...]},
     {"branch": ["0x..", "0x..", ...]}
  ],
  "total_leaves": 65536                       # optional; used by some proof encodings
}

2) With pre-hashed leaf nodes:
{
  "indices": [10, 50, 4095],
  "leaf_hashes": ["0x...", "0x...", "0x..."],
  "proofs":  [{"branch":[...]} , ...]
}

Notes
-----
- We do *not* perform network I/O.
- We allow hex strings with/without "0x".
- Namespace can be int or hex string; if omitted and only leaf_hashes are
  provided, we skip encoding and trust the provided leaf hashes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple


# Lazy imports to keep this module lightweight on import
def _lazy(module: str, attr: str):
    import importlib

    m = importlib.import_module(module)
    try:
        return getattr(m, attr)
    except AttributeError as e:  # pragma: no cover
        raise RuntimeError(f"Expected attribute '{attr}' in module '{module}'") from e


# ------------------------------ Public API ---------------------------------


def verify_samples(root: bytes, payload: Mapping[str, Any]) -> Mapping[str, List[int]]:
    """
    Verify a batch of samples against `root`.

    Parameters
    ----------
    root : bytes
        DA/NMT commitment root (already raw bytes).
    payload : Mapping
        JSON-like structure, see module docstring for accepted shapes.

    Returns
    -------
    dict with keys "ok_indices" and "bad_indices".
    """
    samples = _normalize_payload(payload)
    ok: List[int] = []
    bad: List[int] = []

    verify_incl = _lazy("da.nmt.verify", "verify_inclusion")
    codec_encode = _lazy("da.nmt.codec", "encode_leaf")

    for s in samples:
        try:
            if s.leaf_hash is not None:
                # Pre-hashed leaf node provided; call inclusion with precomputed node.
                # Prefer a dedicated function if available; otherwise use verify_inclusion
                # and let it accept `prehashed=True` via kwargs when supported.
                verified = _verify_with_possible_prehashed(
                    verify_incl, root, s, prehashed=True
                )
            else:
                if s.namespace is None:
                    raise ValueError(
                        "namespace required when raw leaf data is provided"
                    )
                # Encode leaf as per NMT codec, then verify inclusion.
                leaf_node = codec_encode(int(s.namespace), s.leaf_data or b"")
                verified = _verify_with_possible_prehashed(
                    verify_incl, root, s._replace_leaf_hash(leaf_node), prehashed=True
                )
        except Exception:
            verified = False

        (ok if verified else bad).append(int(s.index))

    return {"ok_indices": ok, "bad_indices": bad}


# ------------------------------ Internals ----------------------------------


@dataclass(frozen=True)
class _Sample:
    index: int
    branch: Sequence[bytes]  # sibling nodes from leaf→root
    leaf_data: Optional[bytes] = None  # raw shard data (pre-encoding)
    leaf_hash: Optional[bytes] = None  # pre-encoded leaf node
    namespace: Optional[int] = None  # required if leaf_data is used

    def _replace_leaf_hash(self, new_hash: bytes) -> "_Sample":
        return _Sample(
            index=self.index,
            branch=self.branch,
            leaf_data=self.leaf_data,
            leaf_hash=new_hash,
            namespace=self.namespace,
        )


def _verify_with_possible_prehashed(
    verify_incl_fn, root: bytes, sample: _Sample, prehashed: bool
) -> bool:
    """
    Call da.nmt.verify.verify_inclusion with forward-compatible kwargs.
    Some versions may accept (root, ns, leaf_bytes, index, branch)
    while others might accept (root, leaf_hash, index, branch, prehashed=True).

    We try the common variants gracefully.
    """
    # Variant A: verify_inclusion(root, namespace, leaf_bytes, index, branch)
    try:
        if (
            sample.namespace is not None
            and not prehashed
            and sample.leaf_data is not None
        ):
            return bool(
                verify_incl_fn(
                    root,
                    int(sample.namespace),
                    sample.leaf_data,
                    int(sample.index),
                    list(sample.branch),
                )
            )
    except TypeError:
        pass

    # Variant B: verify_inclusion(root, leaf_node, index, branch, prehashed=True/False)
    try:
        return bool(
            verify_incl_fn(
                root,
                sample.leaf_hash or b"",
                int(sample.index),
                list(sample.branch),
                prehashed=True,
            )
        )
    except TypeError:
        pass

    # Variant C: verify_inclusion(root=root, leaf=..., index=..., branch=..., prehashed=True)
    try:
        return bool(
            verify_incl_fn(
                root=root,
                leaf=sample.leaf_hash or b"",
                index=int(sample.index),
                branch=list(sample.branch),
                prehashed=True,
            )
        )
    except TypeError:
        pass

    # If none of the call styles work, raise a descriptive error for the caller.
    raise RuntimeError("da.nmt.verify.verify_inclusion has an unsupported signature")


def _normalize_payload(payload: Mapping[str, Any]) -> List[_Sample]:
    """
    Accept several payload shapes and normalize to a list of _Sample.
    """
    indices = _as_int_list(
        payload.get("indices") or payload.get("sample_indices") or []
    )
    if not indices:
        raise ValueError("payload must include a non-empty 'indices' array")

    namespace = _maybe_namespace(payload.get("namespace") or payload.get("ns"))
    leaves_raw = payload.get("leaves") or payload.get("raw") or payload.get("data")
    leaf_hashes = payload.get("leaf_hashes") or payload.get("leaves_hashed")
    proofs = payload.get("proofs") or payload.get("branches") or []

    # Alternate shape: a flat "samples" array of objects:
    if not proofs and isinstance(payload.get("samples"), list):
        samples = []
        for item in payload["samples"]:
            idx = _as_int(item.get("index"))
            br = _normalize_branch(item)
            ns = _maybe_namespace(item.get("namespace") or namespace)
            lh = _as_bytes_maybe(item.get("leaf_hash") or item.get("leafNode"))
            ld = _as_bytes_maybe(item.get("leaf") or item.get("data"))
            samples.append(
                _Sample(index=idx, branch=br, leaf_data=ld, leaf_hash=lh, namespace=ns)
            )
        return samples

    # Common parallel-array shape:
    if not isinstance(proofs, list) or len(proofs) != len(indices):
        raise ValueError(
            "payload.proofs/branches must be a list of the same length as indices"
        )

    samples: List[_Sample] = []
    for i, idx in enumerate(indices):
        branch = _normalize_branch(proofs[i])
        ld = None
        lh = None
        if leaves_raw is not None:
            ld = _as_bytes(leaves_raw[i])
        if leaf_hashes is not None:
            lh = _as_bytes(leaf_hashes[i])
        samples.append(
            _Sample(
                index=idx,
                branch=branch,
                leaf_data=ld,
                leaf_hash=lh,
                namespace=namespace,
            )
        )
    return samples


def _normalize_branch(obj: Any) -> List[bytes]:
    """
    Normalize a branch description into a simple list of sibling node bytes.
    Supports:
      - {"branch": [...]} or {"siblings": [...]}
      - list[...] directly
      - Each node as hex string or bytes
      - Optional nested dict nodes like {"hash": "0x.."} (we pick 'hash')
    """
    if isinstance(obj, list):
        items = obj
    elif isinstance(obj, dict):
        items = obj.get("branch") or obj.get("siblings") or obj.get("nodes") or []
    else:
        raise ValueError(
            "proof branch must be a list or dict with a 'branch'/'siblings' field"
        )

    out: List[bytes] = []
    for n in items:
        if isinstance(n, (bytes, bytearray)):
            out.append(bytes(n))
        elif isinstance(n, str):
            out.append(_as_bytes(n))
        elif isinstance(n, dict) and ("hash" in n or "node" in n):
            out.append(_as_bytes(n.get("hash") or n.get("node")))
        else:
            raise ValueError(
                "branch element must be bytes/hex or an object with 'hash'"
            )
    return out


# ------------------------------ Small utils --------------------------------


def _as_int(x: Any) -> int:
    if isinstance(x, bool):
        raise ValueError("boolean is not a valid index")
    if isinstance(x, int):
        return x
    if isinstance(x, str):
        s = x.strip().lower()
        if s.startswith("0x"):
            return int(s, 16)
        return int(s)
    raise ValueError(f"cannot parse int from {type(x).__name__}")


def _as_int_list(xs: Any) -> List[int]:
    if not isinstance(xs, list):
        return []
    return [_as_int(x) for x in xs]


def _as_bytes(x: Any) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, bytearray):
        return bytes(x)
    if isinstance(x, str):
        s = x.strip().lower()
        if s.startswith("0x"):
            s = s[2:]
        import binascii

        return binascii.unhexlify(s) if s else b""
    raise ValueError(f"cannot parse bytes from {type(x).__name__}")


def _as_bytes_maybe(x: Any) -> Optional[bytes]:
    if x is None:
        return None
    return _as_bytes(x)


def _maybe_namespace(ns: Any) -> Optional[int]:
    if ns is None:
        return None
    return _as_int(ns)


__all__ = ["verify_samples"]
