"""
Animica | proofs.storage

Storage v0 (heartbeat PoSt) verifier.

Goal
- Verify a provider is still storing committed data by validating a PoSt-style
  inclusion proof over a sector commitment at a challenge epoch/seed.
- Optionally evaluate retrieval tickets (latency + success) for a small bonus.
- Emit ProofMetrics suitable for PoIES scoring (consumed by policy_adapter).

Body shape (checked against proofs/schemas/storage.cddl):

{
  "provider": {
    "providerId": bstr .size 32,      # logical id for registry/aicf
    ? "attestation": bytes / object   # (reserved; not required for v0)
  },
  "commit": {                         # static commitment for the sealed sector set
    "sectorRoot": bstr .size 32,      # Merkle root of sector leaves (sealed replicas)
    "sectorSize": uint,               # bytes in one sector (e.g., 32 MiB, 512 MiB)
    "replicas": uint,                 # number of sealed replicas
    "minSamples": uint                # policy-minimum required samples for validity
  },
  "challenge": {
    "epoch": uint,                    # challenge window/height
    "seed": bstr .size 32             # randomness seed binding epoch & chain
  },
  "proof": {                          # minimal inclusion proofs for random leaves
    "samples": [
      {
        "leaf": bstr .size 32,        # leaf digest committed in sectorRoot
        "index": uint,                # leaf index (0-based)
        "path": [ bstr .size 32 ]     # Merkle branches from leaf→root (sibling hashes)
      }
    ]
  },
  ? "retrieval": {                    # optional tickets toward a small bonus
    "tickets": [
      { "blobCommitment": bstr .size 32,
        "latencyMs": uint,
        "ok": bool }
    ]
  }
}

Returned metrics (ProofMetrics subset):
- storage_bytes: int (≥0)     → nominal bytes proven live (sectorSize×replicas scaled by proof quality)
- retrieval_bonus: float [0,1]→ optional multiplier component from retrieval tickets
Other fields are None for storage proofs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .errors import ProofError, SchemaError
from .types import ProofType, ProofEnvelope
from .metrics import ProofMetrics
from .cbor import validate_body
from .utils.hash import sha3_256
from .utils.math import clamp01

# ─────────────────────────────── Domains & hashing ───────────────────────────────

_LEAF_DOMAIN = b"Animica/StorageLeaf/v1"
_NODE_DOMAIN = b"Animica/StorageNode/v1"
_CHALLENGE_DOMAIN = b"Animica/StorageChallenge/v1"

def _leaf_hash(raw_leaf: bytes) -> bytes:
    return sha3_256(_LEAF_DOMAIN + raw_leaf)

def _node_hash(left: bytes, right: bytes) -> bytes:
    return sha3_256(_NODE_DOMAIN + left + right)

def _derive_sample_indices(seed: bytes, epoch: int, count: int) -> List[int]:
    """
    Deterministic index sampler from (seed, epoch) via SHA3-256 in counter mode.
    The total number of leaves is not known here; indices are later modded by
    an upper bound derived from path height if needed by the caller.
    """
    if len(seed) != 32:
        raise SchemaError("challenge.seed must be 32 bytes")
    out: List[int] = []
    ctr = 0
    while len(out) < count:
        ctr_bytes = ctr.to_bytes(8, "big")
        h = sha3_256(_CHALLENGE_DOMAIN + seed + epoch.to_bytes(8, "big") + ctr_bytes)
        # Extract four 64-bit integers per digest for efficiency.
        for i in range(0, 32, 8):
            out.append(int.from_bytes(h[i:i+8], "big"))
            if len(out) == count:
                break
        ctr += 1
    return out

def _verify_merkle_path(leaf_raw: bytes, index: int, path: List[bytes], root: bytes) -> bool:
    """
    Verify a canonical binary Merkle path using domain-separated hashing.
    - leaf hash is H(LEAF_DOMAIN || leaf_raw)
    - internal node is H(NODE_DOMAIN || left || right)
    Index LSB corresponds to the first sibling in the path (standard convention).
    Odd node duplication is NOT used here; path height must match the tree.
    """
    node = _leaf_hash(leaf_raw)
    idx = index
    for sib in path:
        if not isinstance(sib, (bytes, bytearray)) or len(sib) != 32:
            return False
        if (idx & 1) == 0:
            node = _node_hash(node, sib)
        else:
            node = _node_hash(sib, node)
        idx >>= 1
    return node == root

# ─────────────────────────────── Retrieval bonus ───────────────────────────────

def _retrieval_bonus(tickets: List[Dict[str, Any]]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute a small bonus in [0,1] from retrieval tickets:
    - success ratio = ok / total
    - latency score maps 0ms→1, 2s→~0, per-ticket then averaged over ok tickets
    Final = 0.7*success + 0.3*latency_avg (conservative weight on success).
    """
    if not tickets:
        return 0.0, {"count": 0, "ok": 0, "lat_avg": None}

    import math

    total = len(tickets)
    oks = 0
    lat_components: List[float] = []
    for t in tickets:
        ok = bool(t.get("ok", False))
        if ok:
            oks += 1
            p95 = max(0, int(t.get("latencyMs", 0)))
            # Map 0ms→1, 500ms→~0.5, 2000ms→~0
            lat_norm = 1.0 - (math.log1p(p95 / 500.0) / math.log1p(4.0))
            lat_components.append(clamp01(lat_norm))
    success = oks / float(total)
    lat_avg = sum(lat_components) / len(lat_components) if lat_components else 0.0
    bonus = clamp01(0.7 * success + 0.3 * lat_avg)
    return bonus, {"count": total, "ok": oks, "lat_avg": lat_avg}

# ─────────────────────────────── Main verification ───────────────────────────────

def verify_storage_body(body: Dict[str, Any]) -> Tuple[ProofMetrics, Dict[str, Any]]:
    """
    Verify Storage v0 heartbeat proof and optional retrieval tickets.
    Returns (ProofMetrics, details).
    """
    # 1) Structural validation
    validate_body(ProofType.STORAGE, body)

    # 2) Extract fields
    provider = body["provider"]
    commit = body["commit"]
    challenge = body["challenge"]
    proof = body["proof"]

    sector_root = bytes(commit["sectorRoot"])
    if len(sector_root) != 32:
        raise SchemaError("commit.sectorRoot must be 32 bytes")

    sector_size = int(commit["sectorSize"])
    replicas = int(commit["replicas"])
    min_samples = int(commit["minSamples"])
    if sector_size <= 0 or replicas <= 0 or min_samples <= 0:
        raise SchemaError("sectorSize, replicas, minSamples must be positive")

    epoch = int(challenge["epoch"])
    seed = bytes(challenge["seed"])
    # 3) Verify inclusion samples
    samples = proof.get("samples", [])
    if not isinstance(samples, list) or len(samples) < min_samples:
        raise ProofError(f"insufficient samples: got {len(samples)}, need >= {min_samples}")

    # Deterministically derive the expected *positions*; we tolerate any superset order,
    # but require at least the first min_samples derived indices to appear.
    # Because we don't know the tree leaf count here, we check exact provided indices and
    # verify uniqueness.
    provided_indices = {int(s["index"]) for s in samples}
    if len(provided_indices) != len(samples):
        raise ProofError("duplicate sample indices provided")

    # Verify each sample Merkle path to the declared sectorRoot.
    valid_count = 0
    for i, s in enumerate(samples):
        try:
            leaf = bytes(s["leaf"])
            idx = int(s["index"])
            path = [bytes(x) for x in s["path"]]
        except Exception as e:  # noqa: BLE001
            raise SchemaError(f"invalid proof.samples[{i}]: {e}") from e
        ok = _verify_merkle_path(leaf, idx, path, sector_root)
        if not ok:
            raise ProofError(f"invalid Merkle path for sample index {idx}")
        valid_count += 1

    if valid_count < min_samples:
        raise ProofError("not enough valid samples")

    # Bind the proof to the challenge by re-deriving indices and checking coverage.
    # We only *require* that the first min_samples derived indices are covered by provided_indices.
    derived = _derive_sample_indices(seed, epoch, count=min_samples)
    # Normalize derived indices into a reasonable range using the maximum observed index bit-length.
    max_idx = max(provided_indices)
    # Avoid zero; choose the next power-of-two above max index to model tree size.
    import math
    tree_size = 1 << (max(1, math.ceil(math.log2(max(1, max_idx + 1)))))
    derived_mod = {d % tree_size for d in derived}
    if not derived_mod.issubset(provided_indices):
        raise ProofError("derived challenge indices are not fully covered by provided samples")

    # 4) Compute nominal storage proven
    # Scale by sample coverage ratio as a conservative quality factor in [0.5, 1.0].
    coverage = valid_count / float(max(valid_count, min_samples))
    quality = clamp01(0.5 + 0.5 * coverage)  # at min_samples → 0.5, grows to 1.0 with more samples
    storage_bytes = int(sector_size * replicas * quality)

    # 5) Optional retrieval bonus
    retrieval = body.get("retrieval")
    if retrieval and isinstance(retrieval, dict):
        bonus, bonus_details = _retrieval_bonus(list(retrieval.get("tickets", [])))
    else:
        bonus, bonus_details = 0.0, {"count": 0, "ok": 0, "lat_avg": None}

    # 6) Build metrics & details
    metrics = ProofMetrics(
        storage_bytes=storage_bytes,
        retrieval_bonus=bonus,
    )

    details = {
        "providerId": bytes(provider["providerId"]).hex(),
        "sectorRoot": sector_root.hex(),
        "sectorSize": sector_size,
        "replicas": replicas,
        "minSamples": min_samples,
        "challenge": {"epoch": epoch, "seed": seed.hex()},
        "samples": {"provided": len(samples), "valid": valid_count, "coverage": coverage, "tree_size_guess": tree_size},
        "quality": quality,
        "storage_bytes": storage_bytes,
        "retrieval": bonus_details,
    }
    return metrics, details


def verify_envelope(env: ProofEnvelope) -> Tuple[ProofMetrics, Dict[str, Any]]:
    """
    Envelope-aware variant; env.type_id must be STORAGE.
    """
    if env.type_id != ProofType.STORAGE:
        raise SchemaError(f"wrong proof type for Storage verifier: {int(env.type_id)}")
    return verify_storage_body(env.body)


__all__ = [
    "verify_storage_body",
    "verify_envelope",
]
