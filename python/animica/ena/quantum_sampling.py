"""
animica.ena.quantum_sampling — quantum-seeded generation for 7.1.1 (VIE / P2).

Derives a per-request RNG seed whose provenance traces to the node's quantum
randomness beacon (``rand.getQuantumBeacon``), falling back — honestly labelled —
to the node's CSPRNG (``quantum.quw.randomBytes``) and finally to a local
``os.urandom`` seed when no node is reachable.

The seed is *advisory and non-consensus*: it makes deterministic / seed-honoring
local backends reproducible and records the seed's origin in the Proof-of-Inference
receipt. ``attested`` is True only when the beacon itself was hardware-attested;
otherwise the receipt says so plainly (``attestation_backend`` = ``software`` /
``local``). Nothing here blocks a completion — every failure degrades softly.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Optional

_BEACON_TTL = 5.0            # seconds to cache a fetched beacon (per-round reuse)
_SEED_MASK = (1 << 63) - 1   # keep seeds inside a portable signed-64 range
_beacon_cache: Optional[dict[str, Any]] = None
_beacon_cache_at: float = 0.0


def _rpc_url() -> str:
    return os.environ.get("ANIMICA_RPC_URL", "http://127.0.0.1:8545/rpc")


def _rpc(method: str, params: Any, *, timeout: float) -> Optional[Any]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode("utf-8")
    req = urllib.request.Request(_rpc_url(), data=body, method="POST",
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("error"):
        return None
    return (data or {}).get("result")


def _monotonic() -> float:
    return time.monotonic()


def _fetch_beacon(*, timeout: float) -> Optional[dict[str, Any]]:
    """Return {seed: bytes, round: int, attested: bool, backend: str} or None.

    Cached for ``_BEACON_TTL`` so a burst of requests reuses one beacon.
    """
    global _beacon_cache, _beacon_cache_at
    if _beacon_cache is not None and (_monotonic() - _beacon_cache_at) < _BEACON_TTL:
        return _beacon_cache

    # 1) a real attested/aggregated beacon round, when one is available.
    b = _rpc("rand.getQuantumBeacon", [], timeout=timeout)
    if isinstance(b, dict) and b.get("available"):
        value_hex = b.get("value_hex") or b.get("value") or b.get("beacon_hex")
        if value_hex:
            out = {"seed": bytes.fromhex(value_hex), "round": int(b.get("round_id", 0)),
                   "attested": bool(b.get("attested", False)), "backend": "beacon"}
            _beacon_cache, _beacon_cache_at = out, _monotonic()
            return out

    # 2) the node's randomness endpoint (hardware QRNG when present, else CSPRNG).
    r = _rpc("quantum.quw.randomBytes", {"n": 32, "attested": True}, timeout=timeout)
    if isinstance(r, dict) and r.get("bytes_hex"):
        src = r.get("source") or {}
        att = r.get("attestation") or {}
        attested = bool(src.get("attested") or att.get("attested"))
        backend = ("hardware" if src.get("is_hardware")
                   else att.get("backend") or src.get("name") or "software")
        out = {"seed": bytes.fromhex(r["bytes_hex"]), "round": 0,
               "attested": attested, "backend": backend}
        _beacon_cache, _beacon_cache_at = out, _monotonic()
        return out
    return None


def quantum_seed_for_request(job_id: str, *, attested: bool = True,
                             timeout: float = 2.0) -> Optional[Any]:
    """A ``QuantumSeed`` bound to ``job_id``, or a local-fallback seed.

    ``attested`` here is the *request preference*; the returned seed's own
    ``attested`` flag reflects reality (only True if the beacon was attested).
    Returns None only if the quantum_seed machinery is unavailable entirely.
    """
    try:
        from aicf.integration.quantum_seed import quantum_seed_for
    except Exception:  # noqa: BLE001 — aicf optional in some installs
        return None

    beacon = _fetch_beacon(timeout=timeout)
    if beacon is None:
        beacon = {"seed": os.urandom(32), "round": 0, "attested": False, "backend": "local"}

    try:
        qseed = quantum_seed_for(beacon_seed=beacon["seed"], beacon_round=beacon["round"],
                                 job_id=job_id, attested=bool(beacon["attested"]) and attested)
    except Exception:  # noqa: BLE001
        return None
    # Stash the backend label for the receipt (QuantumSeed is a frozen dataclass
    # with no such field, so bypass the freeze via object.__setattr__).
    try:
        object.__setattr__(qseed, "_attestation_backend", beacon["backend"])
    except Exception:  # noqa: BLE001 — receipt then defaults to 'software'
        pass
    return qseed


def seed_int(qseed: Any) -> Optional[int]:
    """A portable integer seed to thread into an adapter's ``generate(seed=…)``."""
    if qseed is None:
        return None
    try:
        return int(qseed.as_int()) & _SEED_MASK
    except Exception:  # noqa: BLE001
        try:
            return int(qseed.seed_hex[:16], 16) & _SEED_MASK
        except Exception:  # noqa: BLE001
            return None


def seed_receipt_dict(qseed: Any, *, seed_applied: bool) -> Optional[dict[str, Any]]:
    """The seed provenance dict embedded into an InferenceReceipt."""
    if qseed is None:
        return None
    try:
        base = qseed.as_dict()
    except Exception:  # noqa: BLE001
        base = {"seed_hex": getattr(qseed, "seed_hex", None),
                "beacon_round": getattr(qseed, "beacon_round", None),
                "attested": getattr(qseed, "attested", False)}
    base = dict(base)
    base["seed_applied"] = bool(seed_applied)
    base["attestation_backend"] = getattr(qseed, "_attestation_backend", "software")
    return base


def seed_everything(qseed: Any) -> dict[str, Any]:
    """Seed the process RNGs (random/numpy/torch when present) from ``qseed``."""
    from aicf.integration.quantum_seed import seed_everything as _se
    return _se(qseed)


def beacon_round_cache_clear() -> None:
    global _beacon_cache, _beacon_cache_at
    _beacon_cache, _beacon_cache_at = None, 0.0
