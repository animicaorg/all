"""
Animica • DA • Erasure — Decoder
Recover a blob from erasure-coded, namespaced NMT leaves — verifying inclusion
proofs against a DA root when provided — using any k out of n shards per stripe.

Overview
--------
Given erasure params RS(k, n) with fixed shard size B = share_bytes, the encoder
emits, per stripe, k data leaves (variable body length for the last data shard)
followed by p = n-k parity leaves (always length B). To decode:

  1) (Optional but recommended) Verify each provided leaf's inclusion proof
     against the DA (NMT) root, and check the namespace tag.
  2) For each stripe, when you have >= k leaf bodies (data and/or parity),
     pad data bodies to B where needed, then solve the RS system to recover
     the k data shards of that stripe.
  3) Reassemble the original blob by concatenating the k data shards per
     stripe, trimming the very last shard to its *meaningful length* (either
     supplied by the caller or inferred from any present data leaf in the
     last stripe if available).

This module does **not** resolve NMT *range* proofs; it expects standard
inclusion proofs for individual leaves. A higher layer can validate that the
set of leaves cover the namespace range if required.

API
---
- decode_blob_from_records(records, params, *, da_root=None,
                           expected_namespace=None, original_size=None,
                           stripes_hint=None, require_all_stripes=True)
      -> ErasureDecodeResult

Where `records` is a sequence of ErasureLeafRecord carrying (stripe, position,
encoded leaf bytes, and optional proof).

Notes
-----
- `position` is the *per-stripe* position 0..n-1 where 0..k-1 are data rows and
  k..n-1 are parity rows. If you cannot derive positions from proofs/wire, you
  must supply them (they are necessary for RS decoding).
- If `original_size` is unknown and no data leaf from the *last recovered*
  stripe is available, the result will be padded and flagged as `size_ambiguous`.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..errors import DAError, InvalidProof
from ..nmt.codec import decode_leaf  # returns (namespace: bytes, body: bytes)
from ..nmt.namespace import normalize_namespace
from .params import ErasureParams
from .reedsolomon import rs_decode

# `da.nmt.verify` is optional at import time for flexibility; we import lazily.
try:  # pragma: no cover - exercised in integration tests
    from ..nmt import verify as nmt_verify  # type: ignore
except Exception:  # pragma: no cover
    nmt_verify = None  # type: ignore


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ErasureLeafRecord:
    """
    One leaf (data or parity) belonging to a stripe of size n=k+p.

    Attributes:
      stripe:   0-based stripe index in the blob layout.
      position: 0..n-1 position within the stripe
                (0..k-1 = data rows, k..n-1 = parity rows).
      leaf:     encoded NMT leaf bytes (namespace || u16(len) || body).
      proof:    optional NMT inclusion proof object; if provided and `da_root`
                is passed to the decoder, it will be verified.
    """

    stripe: int
    position: int
    leaf: bytes
    proof: Optional[object] = None


@dataclass(frozen=True)
class ErasureDecodeResult:
    """
    Outcome of an erasure decode attempt.
    """

    blob: bytes
    recovered_stripes: int
    stripes_total: int
    used_records: int
    missing_stripes: List[int]
    size_ambiguous: bool
    inferred_size: Optional[int]


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _verify_leaf_inclusion(
    leaf: bytes,
    proof: object,
    da_root: bytes,
    expected_ns: Optional[bytes],
) -> None:
    """
    Verify an inclusion proof for `leaf` under `da_root`. If `expected_ns` is
    provided, also enforce the namespace tag in the leaf body matches it.

    This wrapper tolerates a few signatures of da.nmt.verify.* to ease
    integration across modules without tight coupling.
    """
    # Check namespace tag (cheap, deterministic)
    if expected_ns is not None:
        ns, _ = decode_leaf(leaf)
        if ns != expected_ns:
            raise InvalidProof("namespace tag mismatch for leaf")

    if proof is None:
        # If the caller supplies a DA root but no proof, we treat as failure.
        raise InvalidProof("missing inclusion proof while DA root was provided")

    if nmt_verify is None:
        raise InvalidProof("NMT verifier not available at runtime")

    # Try a few common call patterns
    ok = None
    # Pattern 1: verify_inclusion(leaf, proof, root[, namespace]) -> bool
    func = getattr(nmt_verify, "verify_inclusion", None)
    if callable(func):
        try:
            ok = func(leaf, proof, da_root, expected_ns)  # type: ignore[arg-type]
        except TypeError:
            ok = func(leaf, proof, da_root)  # type: ignore[misc]
    # Pattern 2: verify(leaf, proof, root[, namespace]) -> bool
    if ok is None:
        func2 = getattr(nmt_verify, "verify", None)
        if callable(func2):
            try:
                ok = func2(leaf, proof, da_root, expected_ns)  # type: ignore[arg-type]
            except TypeError:
                ok = func2(leaf, proof, da_root)  # type: ignore[misc]
    if ok is None:
        raise InvalidProof("unsupported NMT verify API")

    if not bool(ok):
        raise InvalidProof("NMT inclusion proof failed")


def _group_records_by_stripe(
    records: Sequence[ErasureLeafRecord],
    params: ErasureParams,
) -> Dict[int, Dict[int, bytes]]:
    """
    Returns mapping: stripe -> { position -> shard_payload_B }

    For data leaves the shard payload is right-padded with zeros to B; for
    parity leaves it is exactly B (as encoded).
    """
    k = params.data_shards
    n = params.total_shards
    B = params.share_bytes

    by_stripe: Dict[int, Dict[int, bytes]] = {}

    for rec in records:
        if rec.position < 0 or rec.position >= n:
            raise DAError(f"position out of range (0..{n-1}): {rec.position}")
        if rec.stripe < 0:
            raise DAError("negative stripe index not allowed")

        ns, body = decode_leaf(rec.leaf)  # body length <= B for data; == B for parity
        # Bring to shard payload length B
        if len(body) > B:
            raise DAError("leaf body exceeds share_bytes")
        payload_B = body if len(body) == B else (body + bytes(B - len(body)))

        stripe_map = by_stripe.setdefault(rec.stripe, {})
        if rec.position in stripe_map:
            raise DAError(
                f"duplicate leaf for stripe {rec.stripe}, position {rec.position}"
            )
        stripe_map[rec.position] = payload_B

    return by_stripe


def _infer_last_shard_meaningful_len(
    last_stripe_records: Sequence[ErasureLeafRecord],
    params: ErasureParams,
) -> Optional[int]:
    """
    Try to infer the *meaningful* byte length of the very last data shard in
    the blob (within the last stripe) from any present data leaf. If none of
    the provided records include that data leaf, return None.
    """
    k = params.data_shards
    B = params.share_bytes
    # The last data shard is (position = m) where m is the highest in [0..k-1]
    # that actually carried payload during encoding. We don't know which one
    # that was unless we see its data leaf; parity leaves don't carry a length.
    # Heuristic: if *any* data leaf within the last stripe has body length < B,
    # that's the last shard and the length is its body length. Otherwise we
    # cannot disambiguate and return None (assume full length).
    for rec in last_stripe_records:
        if 0 <= rec.position < k:
            _, body = decode_leaf(rec.leaf)
            if len(body) < B:
                return len(body)
    return None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def decode_blob_from_records(
    records: Sequence[ErasureLeafRecord],
    params: ErasureParams,
    *,
    da_root: Optional[bytes] = None,
    expected_namespace: Optional[bytes] = None,
    original_size: Optional[int] = None,
    stripes_hint: Optional[int] = None,
    require_all_stripes: bool = True,
) -> ErasureDecodeResult:
    """
    Recover a blob from a set of erasure-coded leaves (possibly spanning many
    stripes), verifying inclusion proofs when `da_root` is supplied.

    Args:
      records:
        Sequence of ErasureLeafRecord with (stripe, position, leaf, proof?).
        You can provide an arbitrary mix of data and parity leaves per stripe.
      params:
        Erasure RS(k, n) parameters and share_bytes.
      da_root:
        When provided, every record MUST have a proof and will be verified.
      expected_namespace:
        Optional namespace to enforce; if provided, leaf tags must match it.
      original_size:
        If known out-of-band (e.g., from the blob envelope), pass it to get an
        exact-length result even when no last data leaf is present.
      stripes_hint:
        Optional upper bound for total stripes to expect; used only for nicer
        diagnostics. Decoding does not require it.
      require_all_stripes:
        If True (default), raises DAError when any stripe without >=k shards is
        encountered between min(stripe) and max(stripe).

    Returns:
      ErasureDecodeResult with the reconstructed blob and metadata.

    Raises:
      InvalidProof on proof failures, DAError on structural/decoding issues.
    """
    # Normalize expected namespace, if any
    ns_norm = (
        normalize_namespace(expected_namespace)
        if expected_namespace is not None
        else None
    )

    # 1) Optional NMT inclusion verification for every record
    if da_root is not None:
        for rec in records:
            _verify_leaf_inclusion(rec.leaf, rec.proof, da_root, ns_norm)

    if not records:
        return ErasureDecodeResult(
            blob=b"",
            recovered_stripes=0,
            stripes_total=0 if stripes_hint is None else stripes_hint,
            used_records=0,
            missing_stripes=[],
            size_ambiguous=False if (original_size == 0) else True,
            inferred_size=0 if (original_size == 0) else None,
        )

    # 2) Group by stripe and normalize shard payloads to B bytes
    by_stripe = _group_records_by_stripe(records, params)

    # Determine stripe range for diagnostics
    stripes_present = sorted(by_stripe.keys())
    min_stripe = stripes_present[0]
    max_stripe = stripes_present[-1]
    stripes_total = stripes_hint if stripes_hint is not None else (max_stripe + 1)

    # 3) For each stripe, if >=k shards available, RS-decode to k data shards
    k = params.data_shards
    n = params.total_shards
    B = params.share_bytes

    recovered_data_by_stripe: Dict[int, List[bytes]] = {}
    missing_stripes: List[int] = []

    for s in range(min_stripe, max_stripe + 1):
        shard_map = by_stripe.get(s, {})
        if len(shard_map) < k:
            missing_stripes.append(s)
            continue
        # rs_decode expects a map {shard_index: bytes} with k entries
        # We deterministically take the *first k* positions (sorted) available
        selected_positions = sorted(shard_map.keys())[:k]
        submap: Dict[int, bytes] = {idx: shard_map[idx] for idx in selected_positions}
        try:
            data_shards = rs_decode(submap, params)  # List[k] of B-length shards
        except Exception as e:
            raise DAError(f"RS decode failed for stripe {s}: {e}") from e
        recovered_data_by_stripe[s] = data_shards

    if require_all_stripes and missing_stripes:
        raise DAError(
            f"insufficient shards to recover stripes {missing_stripes}; "
            f"need at least {k} of {n} leaves per missing stripe"
        )

    if not recovered_data_by_stripe:
        # Nothing recovered
        return ErasureDecodeResult(
            blob=b"",
            recovered_stripes=0,
            stripes_total=stripes_total,
            used_records=len(records),
            missing_stripes=missing_stripes,
            size_ambiguous=True if original_size is None else False,
            inferred_size=None if original_size is None else original_size,
        )

    # 4) Reassemble blob in stripe order; trim to exact length
    # Determine the last recovered stripe
    last_stripe = max(recovered_data_by_stripe.keys())
    # Stitch all *full* stripes below `last_stripe`
    parts: List[bytes] = []
    for s in range(min_stripe, last_stripe):
        if s not in recovered_data_by_stripe:
            # If not required to recover all, skip gaps
            continue
        parts.extend(recovered_data_by_stripe[s])  # k shards in order 0..k-1

    # Handle the last stripe: may contain the partially-filled final shard.
    last_data = recovered_data_by_stripe[last_stripe]
    parts.extend(last_data)  # tentatively as full B each

    # Now compute total length to trim to:
    if original_size is not None:
        total_len = original_size
        size_ambiguous = False
        inferred_size = original_size
    else:
        # Try to infer from any *data* leaf present in the last stripe
        last_records = [r for r in records if r.stripe == last_stripe]
        inferred = _infer_last_shard_meaningful_len(last_records, params)
        if inferred is None:
            # No hint; blob size is ambiguous (assume fully packed)
            total_len = len(b"".join(parts))
            size_ambiguous = True
            inferred_size = None
        else:
            # All prior data shards are full; only the very last shard is trimmed
            total_len = (len(parts) - 1) * B + inferred
            size_ambiguous = False
            inferred_size = total_len

    blob_full = b"".join(parts)
    if total_len > len(blob_full):
        # Should not happen; guard against inconsistent hints
        raise DAError("computed blob length exceeds reconstructed bytes")
    blob = blob_full[:total_len]

    return ErasureDecodeResult(
        blob=blob,
        recovered_stripes=len(recovered_data_by_stripe),
        stripes_total=stripes_total,
        used_records=len(records),
        missing_stripes=missing_stripes,
        size_ambiguous=size_ambiguous,
        inferred_size=inferred_size,
    )


__all__ = [
    "ErasureLeafRecord",
    "ErasureDecodeResult",
    "decode_blob_from_records",
]
