from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

policy_mod = pytest.importorskip(
    "mempool.policy", reason="mempool.policy module not found"
)
errors_mod = pytest.importorskip(
    "mempool.errors", reason="mempool.errors module not found"
)

AdmissionPolicy = policy_mod.AdmissionPolicy
AdmissionConfig = policy_mod.AdmissionConfig

FeeTooLow = errors_mod.FeeTooLow
MempoolErrorCode = errors_mod.MempoolErrorCode


@dataclass
class FakeTx:
    sender: bytes
    nonce: int = 0
    chain_id: Optional[int] = None
    effective_fee_wei: Optional[int] = None


@dataclass
class FakeMeta:
    size_bytes: int
    effective_fee_wei: Optional[int] = None


class DummyThresholds:
    def __init__(self, admit_floor_wei: int) -> None:
        self.admit_floor_wei = admit_floor_wei


class DummyWatermark:
    """
    Very small FeeWatermark stub.

    We only care that AdmissionPolicy consults it (for non-local / no-bypass)
    and respects the `admit_floor_wei` it reports.
    """

    def __init__(self, admit_floor_wei: int) -> None:
        self._thresholds = DummyThresholds(admit_floor_wei)
        self.calls: list[tuple[int, int]] = []

    def thresholds(self, pool_size: int, capacity: int) -> DummyThresholds:
        self.calls.append((pool_size, capacity))
        return self._thresholds


def _policy(
    *,
    floor_wei: int,
    accept_below_floor_for_local: bool,
) -> tuple[AdmissionPolicy, DummyWatermark]:
    wm = DummyWatermark(admit_floor_wei=floor_wei)
    cfg = AdmissionConfig(
        max_tx_size_bytes=128_000,
        accept_below_floor_for_local=accept_below_floor_for_local,
        min_effective_fee_override_wei=None,
        allow_chain_id=None,
    )
    return AdmissionPolicy(cfg=cfg, watermark=wm), wm


# ---------------------------------------------------------------------------
# Non-local: below dynamic floor → rejected with FeeTooLow
# ---------------------------------------------------------------------------


def test_non_local_below_floor_rejected_with_fee_too_low() -> None:
    floor = 200
    offered = 100  # strictly below floor

    policy, wm = _policy(
        floor_wei=floor,
        accept_below_floor_for_local=True,  # should NOT affect non-local behavior
    )

    tx = FakeTx(sender=b"A" * 20, effective_fee_wei=offered)
    meta = FakeMeta(size_bytes=500, effective_fee_wei=offered)

    pool_size = 5
    capacity = 50

    with pytest.raises(FeeTooLow) as excinfo:
        policy.check_admit(
            tx=tx,
            meta=meta,
            pool_size=pool_size,
            capacity=capacity,
            is_local=False,
        )

    err: FeeTooLow = excinfo.value  # type: ignore[assignment]

    # Watermark consulted with correct arguments
    assert wm.calls == [(pool_size, capacity)]

    # Error shape: stable code and reason
    assert err.code == MempoolErrorCode.FEE_TOO_LOW
    assert err.reason == "fee_too_low"

    # Context must carry both offered and minimum required fee (in wei)
    ctx = err.context
    assert ctx.get("offered_gas_price_wei") == offered
    assert ctx.get("min_required_wei") == floor

    # Human message should indicate "too low" and mention gwei units
    msg = err.message
    assert "low" in msg
    assert "gwei" in msg


# ---------------------------------------------------------------------------
# Local: below floor is allowed when accept_below_floor_for_local = True
# ---------------------------------------------------------------------------


def test_local_below_floor_is_accepted_when_bypass_enabled() -> None:
    floor = 500
    offered = 100  # well below floor

    policy, wm = _policy(
        floor_wei=floor,
        accept_below_floor_for_local=True,
    )

    tx = FakeTx(sender=b"B" * 20, effective_fee_wei=offered)
    meta = FakeMeta(size_bytes=500, effective_fee_wei=offered)

    pool_size = 1
    capacity = 100

    # Should *not* raise FeeTooLow due to local bypass.
    policy.check_admit(
        tx=tx,
        meta=meta,
        pool_size=pool_size,
        capacity=capacity,
        is_local=True,
    )

    # Current implementation short-circuits the floor check for local
    # bypass, so we *do not* require that the watermark was consulted here.
    # (wm.calls may legitimately be empty for this scenario.)


# ---------------------------------------------------------------------------
# Local: below floor is rejected when accept_below_floor_for_local = False
# ---------------------------------------------------------------------------


def test_local_below_floor_rejected_when_bypass_disabled() -> None:
    floor = 300
    offered = 100

    policy, _ = _policy(
        floor_wei=floor,
        accept_below_floor_for_local=False,
    )

    tx = FakeTx(sender=b"C" * 20, effective_fee_wei=offered)
    meta = FakeMeta(size_bytes=500, effective_fee_wei=offered)

    with pytest.raises(FeeTooLow):
        policy.check_admit(
            tx=tx,
            meta=meta,
            pool_size=0,
            capacity=10,
            is_local=True,
        )
