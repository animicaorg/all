import hashlib
import os
import sys
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import pytest

# Make the repo root importable if tests want to peek at real modules (optional)
sys.path.insert(0, os.path.expanduser("~/animica"))


# -----------------------------
# Helpers
# -----------------------------


def sha3_256(data: bytes) -> bytes:
    return hashlib.sha3_256(data).digest()


def b2h(b: bytes) -> str:
    return "0x" + b.hex()


# -----------------------------
# Minimal share + policy model for this test
# -----------------------------


@dataclass(frozen=True)
class Share:
    """
    Minimal share used for the relay precheck tests.

    type_id: 'hash' | 'ai' | 'quantum' | 'storage' | 'vdf' (free-form here)
    psi:     non-negative "work units" already mapped from metrics (post-scorer input);
             we treat it as an additive budget consumer for caps.
    nonce:   arbitrary payload for uniqueness; hashed to form share_id.
    """

    type_id: str
    psi: float
    nonce: bytes

    def share_id(self) -> str:
        # Deterministic ID for dedupe across peers. In practice this would be the envelope hash.
        return b2h(
            sha3_256(self.type_id.encode() + self.nonce + str(self.psi).encode())
        )


@dataclass
class CapsPolicy:
    per_type_caps: Dict[str, float]  # e.g., {'hash': 1.8, 'ai': 1.0, 'quantum': 1.0}
    gamma_cap: float  # total Γ cap across all types


class ShareRelay:
    """
    Self-contained relay with:
      - Global dedupe by share_id (free)
      - Policy precheck before acceptance
      - Rolling window accounting (reset_window() to simulate new block)
    """

    def __init__(self, policy: CapsPolicy) -> None:
        self.policy = policy
        self._seen: Dict[str, str] = {}  # share_id -> reason ("ok" or rejection)
        self._used_by_type: Dict[str, float] = {
            k: 0.0 for k in policy.per_type_caps.keys()
        }
        self._used_total: float = 0.0

    def reset_window(self) -> None:
        self._used_by_type = {k: 0.0 for k in self.policy.per_type_caps.keys()}
        self._used_total = 0.0
        # Keep dedupe across window boundaries (as in a network gossip layer)
        # Comment out the next line if you prefer window-scoped dedupe.
        # self._seen.clear()

    # Query helpers for tests
    def used_type(self, t: str) -> float:
        return self._used_by_type.get(t, 0.0)

    def used_total(self) -> float:
        return self._used_total

    def submit(self, peer_id: str, share: Share) -> Tuple[bool, str, str]:
        sid = share.share_id()

        # Duplicate first (free)
        if sid in self._seen and self._seen[sid] == "ok":
            return (False, "duplicate", sid)

        # Basic structural check
        if share.psi < 0 or not share.type_id:
            self._seen[sid] = "malformed"
            return (False, "malformed", sid)

        # Per-type cap precheck
        type_cap = self.policy.per_type_caps.get(share.type_id, None)
        if type_cap is None:
            self._seen[sid] = "unknown_type"
            return (False, "unknown_type", sid)

        next_type_used = self._used_by_type[share.type_id] + share.psi
        if next_type_used > type_cap + 1e-12:
            self._seen[sid] = "type_cap_exceeded"
            return (False, "type_cap_exceeded", sid)

        # Total Γ cap precheck
        if self._used_total + share.psi > self.policy.gamma_cap + 1e-12:
            self._seen[sid] = "gamma_cap_exceeded"
            return (False, "gamma_cap_exceeded", sid)

        # Accept and account
        self._used_by_type[share.type_id] = next_type_used
        self._used_total += share.psi
        self._seen[sid] = "ok"
        return (True, "ok", sid)


# -----------------------------
# Tests
# -----------------------------


def test_policy_precheck_per_type_and_total_caps():
    policy = CapsPolicy(
        per_type_caps={"hash": 1.8, "ai": 1.0, "quantum": 1.0},
        gamma_cap=2.5,
    )
    relay = ShareRelay(policy)

    # A) First hash share — accepted
    s1 = Share("hash", 1.0, b"\x00")
    ok, r, sid1 = relay.submit("peerA", s1)
    assert ok and r == "ok"
    assert pytest.approx(relay.used_type("hash")) == 1.0
    assert pytest.approx(relay.used_total()) == 1.0

    # B) Hash share that would exceed the hash cap — rejected
    s2 = Share("hash", 0.9, b"\x01")
    ok2, r2, _ = relay.submit("peerA", s2)
    assert not ok2 and r2 == "type_cap_exceeded"
    # Usage unchanged
    assert pytest.approx(relay.used_type("hash")) == 1.0
    assert pytest.approx(relay.used_total()) == 1.0

    # C) Hash share that fits exactly to the cap — accepted
    s3 = Share("hash", 0.8, b"\x02")
    ok3, r3, _ = relay.submit("peerA", s3)
    assert ok3 and r3 == "ok"
    assert pytest.approx(relay.used_type("hash")) == 1.8
    assert pytest.approx(relay.used_total()) == 1.8

    # D) AI share over its per-type cap — rejected
    s4 = Share("ai", 1.1, b"\x03")
    ok4, r4, _ = relay.submit("peerB", s4)
    assert not ok4 and r4 == "type_cap_exceeded"
    assert pytest.approx(relay.used_type("ai")) == 0.0
    assert pytest.approx(relay.used_total()) == 1.8

    # E) AI share at cap — accepted (but pushes total to 2.5)
    s5 = Share("ai", 0.7, b"\x04")
    ok5, r5, _ = relay.submit("peerB", s5)
    assert ok5 and r5 == "ok"
    assert pytest.approx(relay.used_type("ai")) == 0.7
    assert pytest.approx(relay.used_total()) == 2.5

    # F) Any additional share exceeding total Γ — rejected
    s6 = Share("quantum", 0.1, b"\x05")
    ok6, r6, _ = relay.submit("peerC", s6)
    assert not ok6 and r6 == "gamma_cap_exceeded"
    # No accounting drift
    assert pytest.approx(relay.used_type("quantum")) == 0.0
    assert pytest.approx(relay.used_total()) == 2.5


def test_duplicate_is_rejected_without_affecting_usage():
    policy = CapsPolicy(per_type_caps={"hash": 1.0}, gamma_cap=5.0)
    relay = ShareRelay(policy)

    s1 = Share("hash", 0.6, b"dup")
    ok1, r1, sid = relay.submit("peerA", s1)
    assert ok1 and r1 == "ok"
    used_before = (relay.used_type("hash"), relay.used_total())

    # Duplicate (same payload) should be rejected without changing usage
    s1_dup = Share("hash", 0.6, b"dup")  # identical -> same ID
    ok2, r2, sid2 = relay.submit("peerB", s1_dup)
    assert not ok2 and r2 == "duplicate" and sid2 == sid
    assert pytest.approx(relay.used_type("hash")) == used_before[0]
    assert pytest.approx(relay.used_total()) == used_before[1]


def test_unknown_type_and_malformed_rejections():
    policy = CapsPolicy(per_type_caps={"hash": 1.0}, gamma_cap=1.0)
    relay = ShareRelay(policy)

    # Unknown type
    s_bad_type = Share("vdf", 0.1, b"x")  # "vdf" not whitelisted in policy
    ok1, r1, _ = relay.submit("peerA", s_bad_type)
    assert not ok1 and r1 == "unknown_type"

    # Malformed (negative psi)
    s_neg = Share("hash", -0.1, b"y")
    ok2, r2, _ = relay.submit("peerA", s_neg)
    assert not ok2 and r2 == "malformed"


def test_window_reset_allows_fresh_budget():
    policy = CapsPolicy(per_type_caps={"hash": 1.0, "ai": 0.5}, gamma_cap=1.2)
    relay = ShareRelay(policy)

    # Consume hash to its cap
    ok1, r1, _ = relay.submit("peer", Share("hash", 1.0, b"a"))
    assert ok1 and r1 == "ok"
    # Next hash would exceed — reject
    ok2, r2, _ = relay.submit("peer", Share("hash", 0.1, b"b"))
    assert not ok2 and r2 == "type_cap_exceeded"

    # New block/window → reset accounting
    relay.reset_window()

    # Now hash budget is available again
    ok3, r3, _ = relay.submit("peer", Share("hash", 0.7, b"c"))
    assert ok3 and r3 == "ok"
    assert pytest.approx(relay.used_type("hash")) == 0.7
    assert pytest.approx(relay.used_total()) == 0.7

    # Fill total Γ with AI
    ok4, r4, _ = relay.submit("peer", Share("ai", 0.5, b"d"))
    assert ok4 and r4 == "ok"
    # Any further share now exceeds total Γ
    ok5, r5, _ = relay.submit("peer", Share("hash", 0.1, b"e"))
    assert not ok5 and r5 == "gamma_cap_exceeded"
