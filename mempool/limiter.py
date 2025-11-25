"""
mempool.limiter
===============

Token-bucket based ingress limits for the mempool and surrounding services.

Features
--------
- Global TX-rate bucket (tx/s)
- Global BYTES-rate bucket (bytes/s)
- Per-peer TX-rate buckets (tx/s), created on demand with LRU/TTL cleanup
- Admission API that atomically checks/consumes all relevant buckets
- Deterministic (injectable 'now') and thread-safe (single lock)

Design notes
------------
We use classic token buckets:
    tokens(t_now) = min(capacity, tokens(t_prev) + rate * (t_now - t_prev))
An operation of "size" consumes 'size' tokens. If insufficient tokens are
available, the caller receives a 'wait_seconds' hint for when to retry.

Units:
- TX buckets consume 1 token per submitted transaction.
- BYTES bucket consumes exactly the byte length of the incoming payload.

Set any rate <= 0 to disable a bucket (i.e., it will always reject with infinite wait)
or set capacity <= 0 to make the bucket effectively disabled as well.

This module is pure logic and does not do any IO or sleeping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
import time
import math
import threading
from collections import OrderedDict


# -------------------------------
# Token bucket implementation
# -------------------------------

@dataclass
class TokenBucket:
    capacity: float            # maximum number of tokens
    rate_per_sec: float        # refill rate (tokens/sec)
    tokens: float = 0.0        # current tokens
    last_refill: float = field(default_factory=time.monotonic)

    def refill(self, now: Optional[float] = None) -> None:
        """Refill tokens according to elapsed time."""
        if now is None:
            now = time.monotonic()
        if self.rate_per_sec <= 0:
            self.last_refill = now
            # No refill (disabled); tokens remain as-is
            return
        dt = max(0.0, now - self.last_refill)
        if dt > 0:
            self.tokens = min(self.capacity, self.tokens + self.rate_per_sec * dt)
            self.last_refill = now

    def try_consume(self, amount: float, now: Optional[float] = None) -> Tuple[bool, float]:
        """
        Attempt to consume 'amount' tokens.
        Returns (ok, wait_seconds_if_denied).
        """
        if amount <= 0:
            return True, 0.0
        self.refill(now)
        if self.tokens >= amount:
            self.tokens -= amount
            return True, 0.0
        # Not enough tokens: compute wait time
        deficit = amount - self.tokens
        if self.rate_per_sec <= 0:
            return False, math.inf
        wait = deficit / self.rate_per_sec
        return False, wait

    def peek_wait(self, amount: float, now: Optional[float] = None) -> float:
        """
        Compute time until 'amount' tokens are available without consuming.
        """
        if amount <= 0:
            return 0.0
        self.refill(now)
        if self.tokens >= amount:
            return 0.0
        deficit = amount - self.tokens
        if self.rate_per_sec <= 0:
            return math.inf
        return deficit / self.rate_per_sec

    def remaining(self, now: Optional[float] = None) -> float:
        self.refill(now)
        return self.tokens

    def set_level(self, tokens: float, now: Optional[float] = None) -> None:
        self.refill(now)
        self.tokens = max(0.0, min(self.capacity, tokens))


# -------------------------------
# Limiter config/state
# -------------------------------

@dataclass(frozen=True)
class LimiterConfig:
    # Global TX
    global_tx_rate_per_sec: float = 1_000.0
    global_tx_burst: float = 2_000.0

    # Global BYTES
    global_bytes_rate_per_sec: float = 10_000_000.0  # ~10 MB/s
    global_bytes_burst: float = 20_000_000.0

    # Per-peer TX
    per_peer_tx_rate_per_sec: float = 20.0
    per_peer_tx_burst: float = 40.0

    # Housekeeping
    peer_bucket_ttl_sec: float = 600.0     # drop idle peers after 10 min
    peer_bucket_max: int = 10_000          # upper bound on peers kept (LRU)


@dataclass
class PeerBucket:
    bucket: TokenBucket
    last_seen: float


@dataclass(frozen=True)
class AdmissionDecision:
    accept: bool
    reason: str
    wait_seconds: float
    # Remaining budgets (approximate) for introspection/metrics
    remaining_global_tx: float
    remaining_global_bytes: float
    remaining_peer_tx: float


class Limiter:
    """
    Composite limiter that enforces:
      - global tx/s
      - global bytes/s
      - per-peer tx/s
    """

    def __init__(self, cfg: LimiterConfig):
        self.cfg = cfg
        now = time.monotonic()
        self._global_tx = TokenBucket(cfg.global_tx_burst, cfg.global_tx_rate_per_sec, cfg.global_tx_burst, now)
        self._global_bytes = TokenBucket(cfg.global_bytes_burst, cfg.global_bytes_rate_per_sec, cfg.global_bytes_burst, now)
        # Per-peer buckets stored in an OrderedDict to support LRU-style cleanup
        self._peers: "OrderedDict[str, PeerBucket]" = OrderedDict()
        self._lock = threading.Lock()

    # --------- housekeeping ---------

    def _get_peer_bucket(self, peer_id: str, now: float) -> PeerBucket:
        pb = self._peers.get(peer_id)
        if pb is None:
            pb = PeerBucket(
                bucket=TokenBucket(self.cfg.per_peer_tx_burst, self.cfg.per_peer_tx_rate_per_sec,
                                   self.cfg.per_peer_tx_burst, now),
                last_seen=now,
            )
            self._peers[peer_id] = pb
        else:
            pb.last_seen = now
            # move to end to mark as recently used
            self._peers.move_to_end(peer_id, last=True)
        return pb

    def _cleanup_peers(self, now: float) -> None:
        # TTL eviction
        ttl = self.cfg.peer_bucket_ttl_sec
        if ttl > 0:
            expired = [pid for pid, pb in self._peers.items() if (now - pb.last_seen) > ttl]
            for pid in expired:
                self._peers.pop(pid, None)
        # Size cap eviction (LRU)
        max_n = self.cfg.peer_bucket_max
        while max_n > 0 and len(self._peers) > max_n:
            self._peers.popitem(last=False)  # pop oldest

    # --------- admission ---------

    def admit(self, peer_id: str, tx_bytes: int, *, now: Optional[float] = None) -> AdmissionDecision:
        """
        Atomically check & consume tokens for:
          - 1 TX from 'peer_id'
          - 1 TX globally
          - 'tx_bytes' from global bytes

        If any bucket would deny, nothing is consumed and a wait hint is returned.
        """
        if now is None:
            now = time.monotonic()
        if tx_bytes < 0:
            tx_bytes = 0

        with self._lock:
            self._cleanup_peers(now)
            pb = self._get_peer_bucket(peer_id, now)

            # Refill to compute waits
            gtx_wait = self._global_tx.peek_wait(1.0, now)
            gby_wait = self._global_bytes.peek_wait(float(tx_bytes), now)
            ptx_wait = pb.bucket.peek_wait(1.0, now)

            max_wait = max(gtx_wait, gby_wait, ptx_wait)

            if max_wait > 0.0:
                # Deny, don't consume
                return AdmissionDecision(
                    accept=False,
                    reason="RateLimited",
                    wait_seconds=max_wait,
                    remaining_global_tx=self._global_tx.remaining(now),
                    remaining_global_bytes=self._global_bytes.remaining(now),
                    remaining_peer_tx=pb.bucket.remaining(now),
                )

            # All buckets can satisfy; consume atomically
            ok1, _ = self._global_tx.try_consume(1.0, now)
            ok2, _ = self._global_bytes.try_consume(float(tx_bytes), now)
            ok3, _ = pb.bucket.try_consume(1.0, now)

            # In principle, all must be True since we peeked; but be robust.
            if not (ok1 and ok2 and ok3):
                # Rollback is not strictly necessary since a failure implies
                # we didn't consume in at least one bucket; but ensure consistency:
                # (We accept slight drift rather than complex two-phase)
                return AdmissionDecision(
                    accept=False,
                    reason="RacingLimiter",
                    wait_seconds=0.01,
                    remaining_global_tx=self._global_tx.remaining(now),
                    remaining_global_bytes=self._global_bytes.remaining(now),
                    remaining_peer_tx=pb.bucket.remaining(now),
                )

            return AdmissionDecision(
                accept=True,
                reason="OK",
                wait_seconds=0.0,
                remaining_global_tx=self._global_tx.remaining(now),
                remaining_global_bytes=self._global_bytes.remaining(now),
                remaining_peer_tx=pb.bucket.remaining(now),
            )

    # --------- config updates & stats ---------

    def reconfigure(self, cfg: LimiterConfig, *, now: Optional[float] = None) -> None:
        """Swap configuration and adjust capacities/rates for existing buckets."""
        if now is None:
            now = time.monotonic()
        with self._lock:
            self.cfg = cfg
            # Update globals
            self._global_tx.refill(now); self._global_tx.capacity = cfg.global_tx_burst; self._global_tx.rate_per_sec = cfg.global_tx_rate_per_sec
            self._global_tx.tokens = min(self._global_tx.tokens, self._global_tx.capacity)
            self._global_bytes.refill(now); self._global_bytes.capacity = cfg.global_bytes_burst; self._global_bytes.rate_per_sec = cfg.global_bytes_rate_per_sec
            self._global_bytes.tokens = min(self._global_bytes.tokens, self._global_bytes.capacity)
            # Update peers
            for pb in self._peers.values():
                pb.bucket.refill(now)
                pb.bucket.capacity = cfg.per_peer_tx_burst
                pb.bucket.rate_per_sec = cfg.per_peer_tx_rate_per_sec
                pb.bucket.tokens = min(pb.bucket.tokens, pb.bucket.capacity)
            self._cleanup_peers(now)

    def snapshot(self, *, now: Optional[float] = None) -> dict:
        """Return a lightweight snapshot of limiter state for metrics."""
        if now is None:
            now = time.monotonic()
        with self._lock:
            gtx = self._global_tx.remaining(now)
            gby = self._global_bytes.remaining(now)
            peers = len(self._peers)
            return {
                "global_tx_tokens": gtx,
                "global_bytes_tokens": gby,
                "peer_buckets": peers,
                "config": {
                    "global_tx_rate_per_sec": self.cfg.global_tx_rate_per_sec,
                    "global_tx_burst": self.cfg.global_tx_burst,
                    "global_bytes_rate_per_sec": self.cfg.global_bytes_rate_per_sec,
                    "global_bytes_burst": self.cfg.global_bytes_burst,
                    "per_peer_tx_rate_per_sec": self.cfg.per_peer_tx_rate_per_sec,
                    "per_peer_tx_burst": self.cfg.per_peer_tx_burst,
                },
            }


# -------------------------------
# Minimal self-test
# -------------------------------

if __name__ == "__main__":
    cfg = LimiterConfig(
        global_tx_rate_per_sec=5.0,
        global_tx_burst=5.0,
        global_bytes_rate_per_sec=1000.0,
        global_bytes_burst=1000.0,
        per_peer_tx_rate_per_sec=2.0,
        per_peer_tx_burst=2.0,
    )
    lim = Limiter(cfg)

    peer = "peer:alice"
    now = time.monotonic()

    # Burst two tx quickly should pass; third should rate-limit per-peer
    print("admit#1", lim.admit(peer, 100, now=now))
    print("admit#2", lim.admit(peer, 100, now=now))
    print("admit#3", lim.admit(peer, 100, now=now))

    # Advance time 0.5s -> should still be limited; 1s -> allow one
    print("admit#4", lim.admit(peer, 100, now=now + 0.5))
    print("admit#5", lim.admit(peer, 100, now=now + 1.1))

    # Global bytes limit test: submit a jumbo that exceeds burst -> limited
    print("admit#6", lim.admit("peer:bob", 5000, now=now + 1.1))
