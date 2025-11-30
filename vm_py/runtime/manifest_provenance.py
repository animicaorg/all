from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

_DEFAULT_ALGO = "sha3_256"


def _canonical_json_bytes(obj: Any) -> bytes:
    """
    Serialize a manifest (or any JSON-serializable object) into a canonical
    byte representation:

    - UTF-8
    - keys sorted
    - no extra whitespace
    - stable separators
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def compute_manifest_hash_for_provenance(
    manifest: Mapping[str, Any],
    *,
    algo: str = _DEFAULT_ALGO,
) -> str:
    """
    Compute a provenance hash for a manifest.

    The hash is taken over the manifest *without* any top-level "provenance"
    block, so that provenance/signatures can wrap the manifest without
    creating a recursion.

    Returns a 0x-prefixed hex string.
    """
    # Drop the provenance block if present.
    base = {k: v for k, v in manifest.items() if k != "provenance"}

    try:
        h_fn = getattr(hashlib, algo)
    except AttributeError as exc:  # pragma: no cover - defensive
        raise ValueError(f"unsupported manifest hash algorithm: {algo}") from exc

    digest = h_fn(_canonical_json_bytes(base)).hexdigest()
    return "0x" + digest


def is_provenance_hash_valid(manifest: Mapping[str, Any]) -> bool:
    """
    Validate that the manifest's provenance.hash matches the recomputed hash
    of the normalized manifest (excluding the provenance block itself).

    The provenance block is expected to look like:

        "provenance": {
            "hashAlgo": "sha3_256",
            "hash": "0x...",
            "signatures": [...]
        }

    Returns:
        True if the hash field is present and matches, False otherwise.
    """
    prov = manifest.get("provenance")
    if not isinstance(prov, Mapping):
        return False

    algo = prov.get("hashAlgo") or prov.get("algo") or _DEFAULT_ALGO
    expected = prov.get("hash")
    if not isinstance(expected, str):
        return False

    recomputed = compute_manifest_hash_for_provenance(manifest, algo=algo)
    return recomputed == expected
