# Copyright (c) Animica.
# SPDX-License-Identifier: MIT
"""
Aggregators for commit-reveal randomness.

We assume each participant submitted a prior *commitment* C = H(tag || addr || salt || payload)
and later revealed (addr, salt, payload). After individual reveals are validated against their
commitments, we combine the *reveal materials* into a single round output.

Provided combiners (32-byte outputs):
- `hash_xor_fold` : XOR of per-reveal SHA3-256 digests (bias-resistant if ≥1 input has entropy).
- `hash_chain`    : Sorted, chained hash over per-reveal digests (order-independent).

Both combiners bind the round-id and the revealer's address/salt/payload into a per-reveal digest
before folding, using explicit domain-separation tags.

Notes on bias resistance
------------------------
Commit–reveal prevents *adaptive* grinding at reveal time, but participants can still *withhold*
their reveal. XOR-of-hashes is a standard, simple strong extractor: if at least one revealed input
is unknown to the adversary and has sufficient min-entropy, the output is (close to) uniform.
To mitigate withholding bias in practice, pair these aggregators with incentives/penalties (e.g.,
bonding or slashing for non-reveal) at the protocol layer.

APIs
----
- hash_xor_fold(reveals, round_id=None, domain_tag=...): bytes
- hash_chain(reveals, round_id=None, domain_tag=...): bytes

Inputs
------
`reveals` is an iterable of `randomness.commit_reveal.verify.Reveal`. Only verified reveals should
be passed in. `round_id` can be an int or bytes to bind the epoch/height/round.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Union

try:
    # Preferred: use project-wide tagged hash if available.
    from randomness.utils.hash import sha3_256  # type: ignore
except Exception:  # pragma: no cover - fallback if module layout changes in early dev
    import hashlib

    def sha3_256(data: bytes) -> bytes:  # type: ignore
        return hashlib.sha3_256(data).digest()

from randomness.commit_reveal.verify import Reveal


BytesLike = Union[bytes, bytearray, memoryview]


# -------- helpers --------


def _as_bytes(x: BytesLike) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    raise TypeError("expected bytes-like object")


def _round_id_bytes(round_id: Optional[Union[int, BytesLike]]) -> bytes:
    if round_id is None:
        return b""
    if isinstance(round_id, int):
        # 8-byte big-endian encoding (enough for heights/round counters)
        return round_id.to_bytes(8, "big", signed=False)
    return _as_bytes(round_id)


def _per_reveal_digest(
    r: Reveal,
    *,
    round_id: Optional[Union[int, BytesLike]],
    domain_tag: bytes,
) -> bytes:
    """
    H(tag || "V1" || round_id || addr || salt || payload)
    """
    rid = _round_id_bytes(round_id)
    return sha3_256(domain_tag + b"\x01" + rid + r.addr + r.salt + r.payload)


def _xor32(a: bytes, b: bytes) -> bytes:
    if len(a) != 32 or len(b) != 32:
        raise ValueError("xor operands must be 32 bytes")
    # Fast path with int conversion
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(32, "big")


def _finalize(tag: bytes, folded: bytes) -> bytes:
    # Optional final hash to hide structure and avalanche
    return sha3_256(tag + b"\xFF" + folded)


# Compatibility helpers used by legacy tests expecting simple digest folds.
def combine_pair(a: bytes, b: bytes) -> bytes:  # pragma: no cover - thin wrapper
    return _xor32(_as_bytes(a), _as_bytes(b))


def aggregate_digests(digests: list[bytes]) -> bytes:
    """Aggregate a list of 32-byte digests using XOR folding with a final hash."""

    if not digests:
        return b"\x00" * 32
    acc = _as_bytes(digests[0])
    for d in digests[1:]:
        acc = combine_pair(acc, d)
    return _finalize(b"ANIMICA/RAND/AGG/DIGESTS", acc)


# -------- combiners --------


def hash_xor_fold(
    reveals: Iterable[Reveal],
    *,
    round_id: Optional[Union[int, BytesLike]] = None,
    domain_tag: bytes = b"ANIMICA/RAND/AGG/XOR-V1",
    finalize: bool = True,
) -> bytes:
    """
    Combine reveals by XOR-folding per-reveal digests.

    For each reveal r:
        d_i = H(tag || 0x01 || round_id || r.addr || r.salt || r.payload)
    Output:
        X = d_1 XOR d_2 XOR ... XOR d_n
        return H(tag || 0xFF || X) if `finalize` else X

    Raises
    ------
    ValueError if no reveals provided.
    """
    it = iter(reveals)
    try:
        first = next(it)
    except StopIteration:
        raise ValueError("hash_xor_fold requires at least one reveal")

    acc = _per_reveal_digest(first, round_id=round_id, domain_tag=domain_tag)
    for r in it:
        di = _per_reveal_digest(r, round_id=round_id, domain_tag=domain_tag)
        acc = _xor32(acc, di)

    return _finalize(domain_tag, acc) if finalize else acc


def hash_chain(
    reveals: Iterable[Reveal],
    *,
    round_id: Optional[Union[int, BytesLike]] = None,
    domain_tag: bytes = b"ANIMICA/RAND/AGG/CHAIN-V1",
) -> bytes:
    """
    Order-independent chained hash.

    1. Compute d_i as in `hash_xor_fold`.
    2. Sort by revealer address bytes to get a canonical order.
    3. seed = H(tag || 0x00 || round_id)
    4. For each d_i in order: seed = H(tag || 0x02 || seed || d_i)
    5. Return H(tag || 0xFF || seed)

    Raises
    ------
    ValueError if no reveals provided.
    """
    ds: list[tuple[bytes, bytes]] = []
    count = 0
    for r in reveals:
        d = _per_reveal_digest(r, round_id=round_id, domain_tag=domain_tag)
        ds.append((r.addr, d))
        count += 1
    if count == 0:
        raise ValueError("hash_chain requires at least one reveal")

    ds.sort(key=lambda t: t[0])  # canonicalize by address

    seed = sha3_256(domain_tag + b"\x00" + _round_id_bytes(round_id))
    for _, di in ds:
        seed = sha3_256(domain_tag + b"\x02" + seed + di)

    return _finalize(domain_tag, seed)


# -------- convenience API --------


@dataclass(frozen=True, slots=True)
class CombinedOutput:
    """Holds both strategies for diagnostics/experiments."""
    xor_fold: bytes
    chain: bytes


def combine_both(
    reveals: Iterable[Reveal],
    *,
    round_id: Optional[Union[int, BytesLike]] = None,
) -> CombinedOutput:
    """
    Compute both combiners with their default domain tags.
    """
    revs = list(reveals)  # materialize once to traverse twice
    return CombinedOutput(
        xor_fold=hash_xor_fold(revs, round_id=round_id),
        chain=hash_chain(revs, round_id=round_id),
    )


__all__ = [
    "hash_xor_fold",
    "hash_chain",
    "combine_both",
    "CombinedOutput",
]
