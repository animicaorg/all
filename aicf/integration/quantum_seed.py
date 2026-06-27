"""
aicf.integration.quantum_seed
=============================

Make quantum entropy *meaningfully drive AI*, not just the randomness beacon.

A QUW node contributes hardware-attested quantum entropy that gets mixed into the
chain randomness beacon (see ``randomness.qrng``). This module turns that beacon
into the RNG seed for ENA AI work and into an unbiasable audit selector:

  1. ``quantum_seed_for(...)`` derives a DETERMINISTIC, VERIFIABLE seed for a
     specific training/inference job from (beacon_seed, round, job_id). Because
     the beacon is public and on-chain, anyone can recompute the seed — a worker
     cannot cherry-pick a favorable seed (anti-cheat for the AICF training/eval
     receipts), and runs are reproducible.

  2. ``seed_everything(...)`` applies the derived seed to Python/NumPy/PyTorch RNG
     (best-effort; torch/numpy optional) so weight init, data shuffling, dropout,
     and sampling actually come from the quantum-seeded stream.

  3. ``audit_select(...)`` picks which receipts/outputs to audit using the same
     beacon, so anti-fraud trap selection is unpredictable and unbiasable.

  4. ``verify_quantum_seed(...)`` re-derives and checks a claimed seed/provenance.

This reuses ``aicf.integration.randomness_bridge`` (epoch seed derivation +
beacon-driven shuffling) and is pure/deterministic so it is fully testable here.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Any, Callable, Dict, List, Optional, Sequence

try:
    from aicf.integration.randomness_bridge import (derive_epoch_seed,
                                                    epoch_from_height)
    _HAVE_BRIDGE = True
except Exception:  # pragma: no cover
    _HAVE_BRIDGE = False

_DS = b"animica/aicf/quantum-seed/v1"


@dataclasses.dataclass(frozen=True)
class QuantumSeed:
    """A verifiable, quantum-beacon-derived seed for an AI job."""

    seed_hex: str
    beacon_round: int
    job_id: str
    attested: bool          # True iff the beacon round mixed attested QRNG entropy
    beacon_seed_hex: str    # the beacon value the seed was derived from
    domain: str = _DS.decode("ascii")

    def as_int(self, bits: int = 64) -> int:
        """Seed as an int for torch/numpy/random (default 64-bit)."""
        return int.from_bytes(bytes.fromhex(self.seed_hex), "big") % (1 << bits)

    def as_bytes(self) -> bytes:
        return bytes.fromhex(self.seed_hex)

    def as_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def _derive(beacon_seed: bytes, beacon_round: int, job_id: str) -> bytes:
    """seed = H(DS || round_be || H_epoch(beacon, round) || job_id)."""
    if _HAVE_BRIDGE:
        epoch_seed = derive_epoch_seed(beacon_seed, beacon_round)
    else:  # pragma: no cover - bridge normally present
        h = hashlib.sha3_256()
        h.update(b"animica/aicf/assign/v1:epoch:")
        h.update(int(beacon_round).to_bytes(8, "big"))
        h.update(beacon_seed)
        epoch_seed = h.digest()
    h = hashlib.sha3_256()
    h.update(_DS)
    h.update(int(beacon_round).to_bytes(8, "big"))
    h.update(epoch_seed)
    h.update(job_id.encode("utf-8"))
    return h.digest()


def quantum_seed_for(
    *,
    beacon_seed: bytes,
    beacon_round: int,
    job_id: str,
    attested: bool = False,
) -> QuantumSeed:
    """Derive the verifiable quantum seed for an AI job from the beacon."""
    seed = _derive(beacon_seed, beacon_round, job_id)
    return QuantumSeed(
        seed_hex=seed.hex(),
        beacon_round=int(beacon_round),
        job_id=str(job_id),
        attested=bool(attested),
        beacon_seed_hex=bytes(beacon_seed).hex(),
    )


def verify_quantum_seed(qseed: QuantumSeed, *, beacon_seed: bytes) -> bool:
    """Recompute the seed from the public beacon and confirm it matches."""
    expect = _derive(beacon_seed, qseed.beacon_round, qseed.job_id)
    return (
        expect.hex() == qseed.seed_hex
        and bytes(beacon_seed).hex() == qseed.beacon_seed_hex
    )


def seed_everything(qseed: QuantumSeed) -> Dict[str, Any]:
    """
    Apply the quantum seed to Python/NumPy/PyTorch RNG. Returns a report of which
    backends were seeded. Safe when numpy/torch are absent.
    """
    s = qseed.as_int(32)
    report: Dict[str, Any] = {"seed": s, "backends": []}
    import random as _random
    _random.seed(s)
    report["backends"].append("random")
    try:
        import numpy as _np  # type: ignore
        _np.random.seed(s)
        report["backends"].append("numpy")
    except Exception:
        pass
    try:
        import torch as _torch  # type: ignore
        _torch.manual_seed(s)
        if _torch.cuda.is_available():  # pragma: no cover - no GPU here
            _torch.cuda.manual_seed_all(s)
        report["backends"].append("torch")
    except Exception:
        pass
    return report


def _score(seed: bytes, salt: bytes, key: bytes) -> int:
    h = hashlib.sha3_256()
    h.update(_DS)
    h.update(b":audit:")
    h.update(seed)
    h.update(salt)
    h.update(key)
    return int.from_bytes(h.digest(), "big")


def _key_of(x: Any) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, str):
        return x.encode("utf-8")
    for attr in ("receipt_hash", "task_id", "id", "provider_id", "address"):
        v = getattr(x, attr, None)
        if isinstance(v, bytes):
            return v
        if isinstance(v, str):
            return v.encode("utf-8")
    return repr(x).encode("utf-8")


def audit_select(
    items: Sequence[Any],
    *,
    beacon_seed: bytes,
    beacon_round: int,
    k: int,
    salt: bytes = b"audit",
) -> List[Any]:
    """
    Deterministically and unbiasably select up to ``k`` items to audit, seeded by
    the quantum beacon. Reproducible by every node; unpredictable before the
    beacon is revealed, so workers cannot dodge audits.
    """
    if k <= 0 or not items:
        return []
    seed = _derive(beacon_seed, beacon_round, "audit")
    ordered = sorted(items, key=lambda it: (_score(seed, salt, _key_of(it)), _key_of(it)))
    return ordered[: min(k, len(ordered))]
