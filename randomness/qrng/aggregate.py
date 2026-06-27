"""
randomness.qrng.aggregate
=========================

Combine many accepted quantum-entropy contributions for a round into one
aggregated value. Aggregating ALL accepted (attested-preferred) contributions —
rather than trusting a single "best" one — is what makes the resulting beacon
decentralized and manipulation-resistant: no single contributor controls the
output as long as one honest attested contributor participates.

Aggregation is an order-independent, domain-separated XOR-fold of per-contribution
digests, then a final hash. Deterministic and reproducible by every node.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import List, Sequence, Tuple

_AGG_DOMAIN = b"animica/qrng/aggregate/v1"


def _sha3(*parts: bytes) -> bytes:
    h = hashlib.sha3_256()
    for p in parts:
        h.update(p)
    return h.digest()


@dataclasses.dataclass(frozen=True)
class AggregateResult:
    value: bytes              # 32-byte aggregated quantum entropy
    n_total: int
    n_attested: int
    contributors: Tuple[str, ...]
    attested: bool            # True iff >=1 attested contribution was folded in
    commitment: str           # hex of value (public commitment)

    def as_dict(self) -> dict:
        return {
            "value_hex": self.value.hex(),
            "n_total": self.n_total,
            "n_attested": self.n_attested,
            "contributors": list(self.contributors),
            "attested": self.attested,
            "commitment": self.commitment,
        }


def aggregate_entropy(
    items: Sequence[Tuple[bytes, bool, str]],
    *,
    require_attested: bool = False,
) -> AggregateResult:
    """
    Aggregate ``items`` = sequence of (entropy_bytes, is_attested, contributor).

    If ``require_attested`` and any attested contributions exist, only the
    attested ones are folded (so an unattested submitter cannot dilute/bias an
    otherwise-attested round). Order-independent: each contribution is reduced to
    a domain+contributor-bound digest, XOR-folded, then finalized with the count.
    """
    chosen: List[Tuple[bytes, bool, str]] = list(items)
    attested_items = [x for x in chosen if x[1]]
    if require_attested and attested_items:
        chosen = attested_items

    acc = bytearray(32)
    contributors: List[str] = []
    n_attested = 0
    for entropy, is_attested, who in chosen:
        # Bind each contribution to its contributor so identical bytes from two
        # addresses don't cancel under XOR.
        d = _sha3(_AGG_DOMAIN, b"|c:", who.encode("utf-8"), b"|e:", bytes(entropy))
        for i in range(32):
            acc[i] ^= d[i]
        contributors.append(who)
        if is_attested:
            n_attested += 1

    value = _sha3(_AGG_DOMAIN, b"|final|", len(chosen).to_bytes(4, "big"), bytes(acc))
    return AggregateResult(
        value=value,
        n_total=len(chosen),
        n_attested=n_attested,
        contributors=tuple(contributors),
        attested=n_attested > 0,
        commitment=value.hex(),
    )
