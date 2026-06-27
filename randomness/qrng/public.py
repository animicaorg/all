"""
randomness.qrng.public
======================

Public, verifiable quantum randomness — the part that makes Quantum Useful Work
*truly useful* to applications. A quantum-attested beacon value drives a family
of deterministic, domain-separated primitives (lottery draws, weighted choice,
shuffles, dice, ranges, coin flips, raw bytes). Every output is a PURE function
of (beacon, request_id, params), so anyone can recompute and verify it without
trusting the node that served it — unbiasable (the beacon is fixed/quantum) and
unpredictable before the beacon is revealed.

Use cases: fair lotteries/raffles, NFT trait/mint randomization, random committee
/ auditor / validator selection, games (dice/coinflip/shuffle), tie-breaks,
sampling — all with a one-line client-side `verify_result()`.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Any, Dict, List, Optional, Sequence

from .aggregate import AggregateResult

_PUB_DOMAIN = b"animica/qrng/public/v1"
_BEACON_DOMAIN = b"animica/qrng/beacon/v1"


def _sha3(*parts: bytes) -> bytes:
    h = hashlib.sha3_256()
    for p in parts:
        h.update(p)
    return h.digest()


# --------------------------------------------------------------------------- #
# Deterministic verifiable RNG (SHA3-256 counter-mode keystream)
# --------------------------------------------------------------------------- #


class QDRNG:
    """Deterministic CSPRNG seeded by 32 bytes; identical across processes/langs."""

    __slots__ = ("_seed", "_ctr", "_buf")

    def __init__(self, seed: bytes) -> None:
        self._seed = bytes(seed)
        self._ctr = 0
        self._buf = bytearray()

    def _refill(self) -> None:
        self._buf += _sha3(self._seed, self._ctr.to_bytes(8, "big"))
        self._ctr += 1

    def randbytes(self, n: int) -> bytes:
        if n < 0:
            raise ValueError("n must be non-negative")
        while len(self._buf) < n:
            self._refill()
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def randbelow(self, n: int) -> int:
        """Uniform int in [0, n) via rejection sampling (no modulo bias)."""
        if n <= 0:
            raise ValueError("n must be positive")
        if n == 1:
            return 0
        k = (n.bit_length() + 7) // 8 + 1
        limit = 1 << (8 * k)
        threshold = limit - (limit % n)
        while True:
            x = int.from_bytes(self.randbytes(k), "big")
            if x < threshold:
                return x % n

    def randrange(self, lo: int, hi: int) -> int:
        """Uniform int in [lo, hi] inclusive."""
        if hi < lo:
            raise ValueError("hi must be >= lo")
        return lo + self.randbelow(hi - lo + 1)

    def shuffle(self, items: List[Any]) -> List[Any]:
        a = list(items)
        for i in range(len(a) - 1, 0, -1):
            j = self.randbelow(i + 1)
            a[i], a[j] = a[j], a[i]
        return a

    def sample(self, items: Sequence[Any], k: int) -> List[Any]:
        if k < 0 or k > len(items):
            raise ValueError("k out of range")
        a = list(items)
        out: List[Any] = []
        for i in range(k):
            j = i + self.randbelow(len(a) - i)
            a[i], a[j] = a[j], a[i]
            out.append(a[i])
        return out

    def weighted_index(self, weights: Sequence[int]) -> int:
        total = sum(int(w) for w in weights)
        if total <= 0:
            raise ValueError("weights must sum to a positive integer")
        r = self.randbelow(total)
        acc = 0
        for i, w in enumerate(weights):
            acc += int(w)
            if r < acc:
                return i
        return len(weights) - 1  # pragma: no cover


def drng_for(beacon: bytes, kind: str, request_id: str) -> QDRNG:
    """Derive an independent DRNG stream for (beacon, kind, request_id)."""
    seed = _sha3(_PUB_DOMAIN, b"|k:", kind.encode("utf-8"),
                 b"|r:", request_id.encode("utf-8"), b"|b:", bytes(beacon))
    return QDRNG(seed)


# --------------------------------------------------------------------------- #
# Quantum beacon (consumable + verifiable)
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class QuantumBeacon:
    round_id: int
    value_hex: str
    prev_hex: str
    attested: bool
    n_contributors: int
    n_attested: int
    quantum_commitment: str

    def value(self) -> bytes:
        return bytes.fromhex(self.value_hex)

    def as_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def build_quantum_beacon(round_id: int, prev: bytes, agg: AggregateResult) -> QuantumBeacon:
    """
    beacon = H(DOMAIN || round || prev || aggregated_quantum_entropy). Binding the
    previous beacon chains rounds; binding the aggregate makes it quantum-backed.
    """
    value = _sha3(_BEACON_DOMAIN, int(round_id).to_bytes(8, "big"),
                  bytes(prev), agg.value)
    return QuantumBeacon(
        round_id=int(round_id), value_hex=value.hex(), prev_hex=bytes(prev).hex(),
        attested=agg.attested, n_contributors=agg.n_total, n_attested=agg.n_attested,
        quantum_commitment=agg.commitment,
    )


def verify_quantum_beacon(beacon: QuantumBeacon, *, aggregate_value_hex: str) -> bool:
    """Recompute the beacon from its prev + the aggregate commitment and compare."""
    value = _sha3(_BEACON_DOMAIN, int(beacon.round_id).to_bytes(8, "big"),
                  bytes.fromhex(beacon.prev_hex), bytes.fromhex(aggregate_value_hex))
    return value.hex() == beacon.value_hex and aggregate_value_hex == beacon.quantum_commitment


# --------------------------------------------------------------------------- #
# Application primitives — each returns a self-describing, verifiable result
# --------------------------------------------------------------------------- #


def _result(kind: str, beacon: bytes, round_id: int, request_id: str,
            params: Dict[str, Any], output: Any) -> Dict[str, Any]:
    return {
        "kind": kind,
        "beacon_hex": bytes(beacon).hex(),
        "round_id": int(round_id),
        "request_id": request_id,
        "params": params,
        "output": output,
    }


def lottery_draw(beacon: bytes, round_id: int, request_id: str,
                 entries: Sequence[Any], k: int) -> Dict[str, Any]:
    """Pick ``k`` distinct winners from ``entries`` (order = draw order)."""
    rng = drng_for(beacon, "lottery", request_id)
    winners = rng.sample(list(entries), k)
    return _result("lottery", beacon, round_id, request_id,
                   {"entries": list(entries), "k": k}, winners)


def random_choice(beacon: bytes, round_id: int, request_id: str,
                  items: Sequence[Any]) -> Dict[str, Any]:
    rng = drng_for(beacon, "choice", request_id)
    idx = rng.randbelow(len(items))
    return _result("choice", beacon, round_id, request_id,
                   {"items": list(items)}, list(items)[idx])


def weighted_choice(beacon: bytes, round_id: int, request_id: str,
                    items: Sequence[Any], weights: Sequence[int]) -> Dict[str, Any]:
    if len(items) != len(weights):
        raise ValueError("items and weights must be the same length")
    rng = drng_for(beacon, "weighted", request_id)
    idx = rng.weighted_index(list(weights))
    return _result("weighted", beacon, round_id, request_id,
                   {"items": list(items), "weights": list(weights)}, list(items)[idx])


def shuffle(beacon: bytes, round_id: int, request_id: str,
            items: Sequence[Any]) -> Dict[str, Any]:
    rng = drng_for(beacon, "shuffle", request_id)
    return _result("shuffle", beacon, round_id, request_id,
                   {"items": list(items)}, rng.shuffle(list(items)))


def random_in_range(beacon: bytes, round_id: int, request_id: str,
                    lo: int, hi: int, count: int = 1) -> Dict[str, Any]:
    rng = drng_for(beacon, "range", request_id)
    out = [rng.randrange(lo, hi) for _ in range(count)]
    return _result("range", beacon, round_id, request_id,
                   {"lo": lo, "hi": hi, "count": count}, out)


def coin_flip(beacon: bytes, round_id: int, request_id: str, count: int = 1) -> Dict[str, Any]:
    rng = drng_for(beacon, "coin", request_id)
    out = ["H" if rng.randbelow(2) else "T" for _ in range(count)]
    return _result("coin", beacon, round_id, request_id, {"count": count}, out)


def dice(beacon: bytes, round_id: int, request_id: str, sides: int = 6, count: int = 1) -> Dict[str, Any]:
    if sides < 2:
        raise ValueError("sides must be >= 2")
    rng = drng_for(beacon, "dice", request_id)
    out = [1 + rng.randbelow(sides) for _ in range(count)]
    return _result("dice", beacon, round_id, request_id, {"sides": sides, "count": count}, out)


def random_bytes(beacon: bytes, round_id: int, request_id: str, n: int) -> Dict[str, Any]:
    rng = drng_for(beacon, "bytes", request_id)
    return _result("bytes", beacon, round_id, request_id, {"n": n}, rng.randbytes(n).hex())


_DISPATCH = {
    "lottery": lambda b, r, q, p: lottery_draw(b, r, q, p["entries"], p["k"]),
    "choice": lambda b, r, q, p: random_choice(b, r, q, p["items"]),
    "weighted": lambda b, r, q, p: weighted_choice(b, r, q, p["items"], p["weights"]),
    "shuffle": lambda b, r, q, p: shuffle(b, r, q, p["items"]),
    "range": lambda b, r, q, p: random_in_range(b, r, q, p["lo"], p["hi"], p.get("count", 1)),
    "coin": lambda b, r, q, p: coin_flip(b, r, q, p.get("count", 1)),
    "dice": lambda b, r, q, p: dice(b, r, q, p.get("sides", 6), p.get("count", 1)),
    "bytes": lambda b, r, q, p: random_bytes(b, r, q, p["n"]),
}


def compute(kind: str, beacon: bytes, round_id: int, request_id: str,
            params: Dict[str, Any]) -> Dict[str, Any]:
    """Compute a randomness result of ``kind`` (used by RPC/CLI)."""
    fn = _DISPATCH.get(kind)
    if fn is None:
        raise ValueError(f"unknown randomness kind: {kind!r}")
    return fn(bytes(beacon), int(round_id), str(request_id), dict(params))


def verify_result(result: Dict[str, Any]) -> bool:
    """
    Client-side verification: recompute the result from its declared inputs
    (beacon, round, request_id, params) and confirm the output matches.
    """
    try:
        kind = result["kind"]
        beacon = bytes.fromhex(result["beacon_hex"])
        recomputed = compute(kind, beacon, int(result["round_id"]),
                             str(result["request_id"]), dict(result["params"]))
        return recomputed["output"] == result["output"]
    except Exception:
        return False
