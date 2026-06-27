"""
randomness.qrng.service
=======================

In-process Quantum Useful Work service: issues per-round challenges, verifies and
credits attested entropy contributions, selects the entropy to mix into the
beacon, and exposes status/credits. Pure-Python and deterministic given its
inputs so the node can drive consensus with it and tests can exercise the whole
lane without hardware.

State is in-memory by default (a ``store`` hook can persist it later). The on-
chain reward is realised by feeding ``contribution_to_quantum_metrics`` into
``consensus.scorer.score_quantum`` when a round is proposed; this service is the
authoritative off-chain ledger/coordinator view and the source of the entropy a
proposer mixes via ``finalize_round(qrng_bytes=...)``.
"""

from __future__ import annotations

import dataclasses
import os
import time
from typing import Any, Dict, List, Optional

from . import contribution as quw
from . import health as _health


@dataclasses.dataclass
class _RoundState:
    round_id: int
    nonce: bytes
    issued_s: float
    contributions: List[quw.QuantumContribution] = dataclasses.field(default_factory=list)
    results: List[quw.VerifyResult] = dataclasses.field(default_factory=list)
    per_address_count: Dict[str, int] = dataclasses.field(default_factory=dict)


class QuantumWorkService:
    def __init__(
        self,
        *,
        min_entropy_per_byte: float = _health.DEFAULT_MIN_ENTROPY_PER_BYTE,
        max_age_s: float = 120.0,
        challenge_ttl_s: float = 90.0,
        per_round_per_address_cap: int = 1,
        now_fn=None,
    ) -> None:
        self.min_h = float(min_entropy_per_byte)
        self.max_age_s = float(max_age_s)
        self.challenge_ttl_s = float(challenge_ttl_s)
        self.cap = int(per_round_per_address_cap)
        self._now = now_fn or time.time
        self._rounds: Dict[int, _RoundState] = {}
        self._seen_nullifiers: set = set()
        self._credits_units: Dict[str, float] = {}
        self._credits_rounds: Dict[str, Dict[int, float]] = {}
        self._beacons: Dict[int, Any] = {}

    # --- challenge ---
    def get_challenge(self, round_id: int) -> Dict[str, Any]:
        rs = self._rounds.get(round_id)
        now = self._now()
        if rs is None or (now - rs.issued_s) > self.challenge_ttl_s:
            rs = _RoundState(round_id=round_id, nonce=os.urandom(32), issued_s=now)
            self._rounds[round_id] = rs
        return {
            "round_id": round_id,
            "nonce_hex": rs.nonce.hex(),
            "expires_s": int(rs.issued_s + self.challenge_ttl_s),
        }

    # --- contribute ---
    def contribute(self, contribution: Dict[str, Any]) -> Dict[str, Any]:
        try:
            c = quw.QuantumContribution.from_dict(contribution)
        except Exception as e:
            return {"accepted": False, "reason": f"malformed contribution: {e}"}

        rs = self._rounds.get(c.round_id)
        if rs is None:
            return {"accepted": False, "reason": "no challenge issued for round; call getChallenge first"}

        if rs.per_address_count.get(c.address, 0) >= self.cap:
            return {"accepted": False, "reason": "per-address per-round cap reached"}

        vr = quw.verify_contribution(
            c,
            expected_nonce=rs.nonce,
            now_s=self._now(),
            max_age_s=self.max_age_s,
            min_entropy_per_byte=self.min_h,
            seen_nullifiers=self._seen_nullifiers,
        )
        if not vr.verified:
            return {"accepted": False, "attested": vr.attested, "reason": vr.reason,
                    "nullifier": vr.nullifier}

        metrics = quw.contribution_to_quantum_metrics(c, vr)
        units = float(metrics["quantum_units"])

        rs.contributions.append(c)
        rs.results.append(vr)
        rs.per_address_count[c.address] = rs.per_address_count.get(c.address, 0) + 1
        self._credits_units[c.address] = self._credits_units.get(c.address, 0.0) + units
        self._credits_rounds.setdefault(c.address, {})[c.round_id] = (
            self._credits_rounds.get(c.address, {}).get(c.round_id, 0.0) + units
        )

        # 'mixed' = whether THIS contribution is currently the one that would be
        # mixed into the beacon for the round (attested-first, then highest min-entropy).
        best = self._best_index(rs)
        will_mix = (best is not None and rs.contributions[best] is c)

        return {
            "accepted": True,
            "attested": vr.attested,
            "mixed": bool(will_mix),
            "credited_units": round(units, 6),
            "metrics": metrics,
            "min_entropy_per_byte": round(vr.min_entropy_per_byte, 4),
            "nullifier": vr.nullifier,
            "reason": "ok",
        }

    # --- beacon tie-in ---
    def _best_index(self, rs: _RoundState) -> Optional[int]:
        """Pick the contribution to mix: attested first, then highest min-entropy."""
        best = None
        best_key = None
        for i, (c, vr) in enumerate(zip(rs.contributions, rs.results)):
            key = (1 if vr.attested else 0, vr.min_entropy_per_byte)
            if best_key is None or key > best_key:
                best_key = key
                best = i
        return best

    def best_entropy_for_round(self, round_id: int) -> Optional[Dict[str, Any]]:
        rs = self._rounds.get(round_id)
        if rs is None or not rs.contributions:
            return None
        i = self._best_index(rs)
        if i is None:
            return None
        c, vr = rs.contributions[i], rs.results[i]
        tag = ("mixed_with_attested_qrng" if vr.attested
               else "mixed_with_unattested_qrng")
        return {
            "round_id": round_id,
            "entropy_hex": c.entropy_hex,
            "attested": vr.attested,
            "tag": tag,
            "contributor": c.address,
            "min_entropy_per_byte": vr.min_entropy_per_byte,
        }

    # --- aggregation + consumable beacon ---
    def aggregate_for_round(self, round_id: int, *, require_attested: bool = True):
        """Aggregate ALL accepted contributions for a round (attested-preferred)."""
        from .aggregate import aggregate_entropy
        rs = self._rounds.get(round_id)
        if rs is None or not rs.contributions:
            return None
        items = [(c.entropy(), vr.attested, c.address)
                 for c, vr in zip(rs.contributions, rs.results)]
        return aggregate_entropy(items, require_attested=require_attested)

    def get_quantum_beacon(self, round_id: int):
        """
        Build (and cache) the consumable, verifiable quantum beacon for a round
        from the aggregated entropy, chaining the previous round's beacon value.
        """
        from .public import build_quantum_beacon
        if round_id in self._beacons:
            return self._beacons[round_id]
        agg = self.aggregate_for_round(round_id)
        if agg is None:
            return None
        prev = b""
        if (round_id - 1) in self._beacons:
            prev = self._beacons[round_id - 1].value()
        beacon = build_quantum_beacon(round_id, prev, agg)
        self._beacons[round_id] = beacon
        return beacon

    # --- views ---
    def get_credits(self, address: str) -> Dict[str, Any]:
        return {
            "address": address,
            "total_units": round(self._credits_units.get(address, 0.0), 6),
            "rounds": {str(k): round(v, 6) for k, v in
                       self._credits_rounds.get(address, {}).items()},
        }

    def status(self) -> Dict[str, Any]:
        contributors = set()
        attested = 0
        total = 0
        source_kinds: Dict[str, int] = {}
        for rs in self._rounds.values():
            for c, vr in zip(rs.contributions, rs.results):
                contributors.add(c.address)
                total += 1
                if vr.attested:
                    attested += 1
                k = str(c.source.get("name", "?"))
                source_kinds[k] = source_kinds.get(k, 0) + 1
        return {
            "available": True,
            "rounds_tracked": len(self._rounds),
            "contributions_total": total,
            "active_contributors": len(contributors),
            "attested_share": round(attested / total, 4) if total else 0.0,
            "source_kinds": source_kinds,
            "min_entropy_threshold": self.min_h,
        }


# Process-wide singleton (the node RPC layer and CLI share one instance).
_SERVICE: Optional[QuantumWorkService] = None


def get_service() -> QuantumWorkService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = QuantumWorkService()
    return _SERVICE
