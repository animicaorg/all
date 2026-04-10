from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

policy_mod = pytest.importorskip(
    "mempool.policy", reason="mempool.policy module not found"
)

AdmissionPolicy = policy_mod.AdmissionPolicy
AdmissionConfig = policy_mod.AdmissionConfig


# -------------------------
# Test scaffolding
# -------------------------


@dataclass
class FakeTx:
    sender: bytes
    nonce: int = 0
    chain_id: Optional[int] = None
    # Some implementations may put effective_fee_wei on the tx instead of meta
    effective_fee_wei: Optional[int] = None


@dataclass
class FakeMeta:
    size_bytes: int
    # Primary place for effective_fee_wei in most callers
    effective_fee_wei: Optional[int] = None


class DummyThresholds:
    """Minimal object returned by FeeWatermark.thresholds()."""

    def __init__(self, admit_floor_wei: int) -> None:
        self.admit_floor_wei = admit_floor_wei


class DummyWatermark:
    """
    Lightweight FeeWatermark stub.

    We track calls so tests can assert that AdmissionPolicy actually consults
    the watermark when computing dynamic fee floors.
    """

    def __init__(self, admit_floor_wei: int) -> None:
        self._thresholds = DummyThresholds(admit_floor_wei)
        self.calls: list[tuple[int, int]] = []

    def thresholds(self, pool_size: int, capacity: int) -> DummyThresholds:
        self.calls.append((pool_size, capacity))
        return self._thresholds


# -------------------------
# Happy-path admission
# -------------------------


def test_non_local_tx_within_size_and_dynamic_fee_is_accepted() -> None:
    """
    A non-local tx that is within size limits and pays at/above the dynamic
    floor should be accepted without raising an AdmissionError.
    """
    wm = DummyWatermark(admit_floor_wei=100)
    policy = AdmissionPolicy(
        cfg=AdmissionConfig(max_tx_size_bytes=128_000),
        watermark=wm,
    )

    tx = FakeTx(sender=b"A" * 20, nonce=0)
    meta = FakeMeta(size_bytes=500, effective_fee_wei=150)

    pool_size = 10
    capacity = 100

    # Should not raise
    policy.check_admit(
        tx=tx,
        meta=meta,
        pool_size=pool_size,
        capacity=capacity,
        is_local=False,
    )

    # Ensure watermark thresholds were consulted with the right arguments
    assert wm.calls == [(pool_size, capacity)]


def test_local_and_non_local_both_accepted_when_fee_above_floor() -> None:
    """
    When the effective fee is comfortably above the floor, both local and
    non-local txs should pass admission checks.

    This also ensures that the is_local flag does not *tighten* policy for
    correctly-priced transactions.
    """
    wm = DummyWatermark(admit_floor_wei=50)
    policy = AdmissionPolicy(
        cfg=AdmissionConfig(
            max_tx_size_bytes=64_000,
            accept_below_floor_for_local=True,
        ),
        watermark=wm,
    )

    tx = FakeTx(sender=b"B" * 20, nonce=1)
    meta = FakeMeta(size_bytes=1_000, effective_fee_wei=200)

    # Non-local: above floor, should pass.
    policy.check_admit(
        tx=tx,
        meta=meta,
        pool_size=5,
        capacity=50,
        is_local=False,
    )

    # Local: also above floor; bypass flag should not change behavior here.
    policy.check_admit(
        tx=tx,
        meta=meta,
        pool_size=5,
        capacity=50,
        is_local=True,
    )


def test_effective_fee_prefers_meta_but_falls_back_to_tx() -> None:
    """
    AdmissionPolicy._effective_fee() should:

    1) Prefer meta.effective_fee_wei when present and non-None.
    2) Fall back to tx.effective_fee_wei when meta has None or no value.

    We structure the two sub-cases so that picking the wrong source would
    cause the admission check to fail with FeeTooLow.
    """
    wm = DummyWatermark(admit_floor_wei=60)
    policy = AdmissionPolicy(
        cfg=AdmissionConfig(max_tx_size_bytes=128_000),
        watermark=wm,
    )

    # Case A: meta has high fee, tx has low fee.
    # If the policy accidentally uses tx.effective_fee_wei first, this would
    # fall below the floor and raise.
    tx_a = FakeTx(sender=b"C" * 20, nonce=0, effective_fee_wei=10)
    meta_a = FakeMeta(size_bytes=800, effective_fee_wei=80)

    policy.check_admit(
        tx=tx_a,
        meta=meta_a,
        pool_size=0,
        capacity=100,
        is_local=False,
    )

    # Case B: meta has None, tx carries the real fee.
    # This checks the fallback path.
    tx_b = FakeTx(sender=b"D" * 20, nonce=0, effective_fee_wei=100)
    meta_b = FakeMeta(size_bytes=800, effective_fee_wei=None)

    policy.check_admit(
        tx=tx_b,
        meta=meta_b,
        pool_size=0,
        capacity=100,
        is_local=False,
    )
