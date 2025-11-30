import hashlib
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pytest

# Make the repo root importable if tests want to peek at real modules (optional)
sys.path.insert(0, os.path.expanduser("~/animica"))


# -----------------------------
# Utilities
# -----------------------------


def sha3_256(data: bytes) -> bytes:
    return hashlib.sha3_256(data).digest()


def b2h(b: bytes) -> str:
    return "0x" + b.hex()


# -----------------------------
# Fake clock for deterministic rate tests
# -----------------------------


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def now(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += float(dt)


# -----------------------------
# Token-bucket rate limiter
# -----------------------------


@dataclass
class _BucketState:
    tokens: float
    last: float


class TokenBucket:
    """
    Per-key token bucket.
      - capacity: max tokens.
      - rate: tokens per second.
      - cost: tokens consumed per event (default 1).
    """

    def __init__(
        self, capacity: float, rate: float, clock: Optional[FakeClock] = None
    ) -> None:
        self.capacity = float(capacity)
        self.rate = float(rate)
        self._clock = clock
        self._state: Dict[str, _BucketState] = {}

    def _now(self) -> float:
        return self._clock.now() if self._clock is not None else time.monotonic()

    def _refill(self, key: str, now: float) -> None:
        st = self._state.get(key)
        if st is None:
            self._state[key] = _BucketState(tokens=self.capacity, last=now)
            return
        if now > st.last:
            st.tokens = min(self.capacity, st.tokens + self.rate * (now - st.last))
            st.last = now

    def consume(self, key: str, cost: float = 1.0) -> bool:
        now = self._now()
        self._refill(key, now)
        st = self._state[key]
        if st.tokens >= cost:
            st.tokens -= cost
            return True
        return False

    # Exposed for tests
    def tokens(self, key: str) -> float:
        self._refill(key, self._now())
        return self._state[key].tokens if key in self._state else self.capacity


# -----------------------------
# Minimal Tx relay with admission + dedupe + rate control
# -----------------------------


class AdmissionError(Exception): ...


class RateLimited(Exception): ...


class Duplicate(Exception): ...


class TxRelayService:
    """
    Minimal, self-contained relay:
      - Basic admission: bytes must start with b"TX|" and be <= MAX_TX_SIZE.
      - Dedupe by tx hash; duplicates short-circuit BEFORE rate-limit consumption.
      - Per-peer token-bucket rate control.
    """

    def __init__(
        self,
        max_tx_size: int = 1024,
        rate_per_sec: float = 20.0,
        burst: float = 40.0,
        clock: Optional[FakeClock] = None,
    ) -> None:
        self.max_tx_size = int(max_tx_size)
        self.clock = clock
        self.limiter = TokenBucket(capacity=burst, rate=rate_per_sec, clock=clock)
        self.seen: Dict[str, float] = {}  # tx_hash_hex -> first_seen_time
        self.pool: Dict[str, bytes] = {}  # tx_hash_hex -> tx bytes

    def _now(self) -> float:
        return self.clock.now() if self.clock is not None else time.monotonic()

    def _admission_checks(self, tx: bytes) -> None:
        if not tx or len(tx) < 3:
            raise AdmissionError("malformed/empty")
        if len(tx) > self.max_tx_size:
            raise AdmissionError("oversize")
        if not tx.startswith(b"TX|"):
            raise AdmissionError("malformed/prefix")

    def submit(self, peer_id: str, tx: bytes) -> Tuple[bool, str, str]:
        """
        Returns (accepted, reason, tx_hash_hex).
        Reasons: "ok", "duplicate", "rate_limited", "malformed", "oversize"
        """
        txh = b2h(sha3_256(tx))
        # Dedupe first (free)
        if txh in self.seen:
            return (False, "duplicate", txh)

        # Rate-limit next
        if not self.limiter.consume(peer_id, cost=1.0):
            return (False, "rate_limited", txh)

        # Admission
        try:
            self._admission_checks(tx)
        except AdmissionError as e:
            # Note: Not marking seen; peer may retry corrected tx
            reason = "oversize" if "oversize" in str(e) else "malformed"
            return (False, reason, txh)

        # Accept
        self.seen[txh] = self._now()
        self.pool[txh] = tx
        return (True, "ok", txh)


# -----------------------------
# Tests
# -----------------------------


def test_dedupe_suppresses_second_submit_without_spending_tokens():
    clk = FakeClock(0.0)
    relay = TxRelayService(max_tx_size=1024, rate_per_sec=1.0, burst=2.0, clock=clk)
    peer = "peerA"

    tx = b"TX|v1|alice|sig|payload-1"
    # First submit OK
    accepted, reason, h1 = relay.submit(peer, tx)
    assert accepted and reason == "ok"
    t_before_dup = relay.limiter.tokens(peer)

    # Second submit is duplicate (should NOT spend a token)
    accepted2, reason2, h2 = relay.submit(peer, tx)
    assert not accepted2 and reason2 == "duplicate" and h2 == h1

    # Token count should be unchanged for the duplicate attempt
    assert abs(relay.limiter.tokens(peer) - t_before_dup) < 1e-9
    # Pool must only contain one entry
    assert len(relay.pool) == 1 and h1 in relay.pool


def test_basic_admission_checks_malformed_and_oversize():
    clk = FakeClock(0.0)
    relay = TxRelayService(max_tx_size=16, rate_per_sec=100.0, burst=100.0, clock=clk)
    peer = "peerB"

    # Empty/malformed
    accepted, reason, _ = relay.submit(peer, b"")
    assert not accepted and reason == "malformed"

    # Wrong prefix
    accepted, reason, _ = relay.submit(peer, b"BAD|v1")
    assert not accepted and reason == "malformed"

    # Oversize (len > 16)
    oversize = b"TX|" + b"x" * 20
    accepted, reason, _ = relay.submit(peer, oversize)
    assert not accepted and reason == "oversize"

    # Valid
    valid = b"TX|ok"
    accepted, reason, _ = relay.submit(peer, valid)
    assert accepted and reason == "ok"


def test_rate_control_per_peer_with_fake_clock():
    clk = FakeClock(0.0)
    # burst=2, rate=1 token/sec → start with 2 tokens; after 1s, +1 token (capped at 2)
    relay = TxRelayService(max_tx_size=1024, rate_per_sec=1.0, burst=2.0, clock=clk)
    peer = "peerC"

    tx1 = b"TX|a"
    tx2 = b"TX|b"
    tx3 = b"TX|c"

    # Have full bucket (2 tokens)
    assert abs(relay.limiter.tokens(peer) - 2.0) < 1e-9

    ok, r, _ = relay.submit(peer, tx1)
    assert ok and r == "ok"
    # 1 token left
    assert relay.limiter.tokens(peer) <= 1.000001

    ok, r, _ = relay.submit(peer, tx2)
    assert ok and r == "ok"
    # 0 tokens left
    # Third submit at same time should be rate-limited
    ok, r, _ = relay.submit(peer, tx3)
    assert not ok and r == "rate_limited"

    # Advance time by ~1.1s to refill one token
    clk.advance(1.1)
    # Retry same (not duplicate because content differs)
    ok, r, _ = relay.submit(peer, tx3)
    assert ok and r == "ok"


def test_dedupe_is_global_across_peers_but_rate_is_per_peer():
    clk = FakeClock(0.0)
    relay = TxRelayService(max_tx_size=1024, rate_per_sec=1.0, burst=1.0, clock=clk)
    peerA, peerB = "peerA", "peerB"
    tx = b"TX|same"

    # peerA sends first → accepted
    ok, r, h = relay.submit(peerA, tx)
    assert ok and r == "ok"

    # peerB sends exact same tx → duplicate globally (no token spent for B)
    tokens_before = relay.limiter.tokens(peerB)
    ok2, r2, h2 = relay.submit(peerB, tx)
    assert not ok2 and r2 == "duplicate" and h2 == h
    assert abs(relay.limiter.tokens(peerB) - tokens_before) < 1e-9

    # peerB can still submit a different tx without having lost tokens due to dedupe
    clk.advance(0.0)  # no refill
    ok3, r3, _ = relay.submit(peerB, b"TX|different")
    assert ok3 and r3 == "ok"
