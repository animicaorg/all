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
Oversize = errors_mod.Oversize
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


def _policy(max_bytes: int) -> AdmissionPolicy:
    """
    Minimal AdmissionPolicy instance focused purely on size limits.

    We pass watermark=None so that dynamic fee floors are effectively 0 and
    cannot interfere with the size checks. We still provide a non-zero fee
    in tests so the FeeTooLow gate (if invoked) is trivially satisfied.
    """
    cfg = AdmissionConfig(
        max_tx_size_bytes=max_bytes,
        accept_below_floor_for_local=True,
        min_effective_fee_override_wei=None,
        allow_chain_id=None,
    )
    return AdmissionPolicy(cfg=cfg, watermark=None)


# -------------------------
# Acceptance at / below limit
# -------------------------


def test_tx_below_size_limit_is_accepted() -> None:
    max_bytes = 1024
    policy = _policy(max_bytes=max_bytes)

    tx = FakeTx(sender=b"A" * 20, effective_fee_wei=1_000_000_000)
    meta = FakeMeta(size_bytes=512, effective_fee_wei=1_000_000_000)

    # Should pass without raising Oversize or any AdmissionError.
    policy.check_admit(
        tx=tx,
        meta=meta,
        pool_size=0,
        capacity=10_000,
        is_local=False,
    )


def test_tx_exactly_at_size_limit_is_accepted() -> None:
    max_bytes = 2048
    policy = _policy(max_bytes=max_bytes)

    tx = FakeTx(sender=b"B" * 20, effective_fee_wei=1_000_000_000)
    meta = FakeMeta(size_bytes=max_bytes, effective_fee_wei=1_000_000_000)

    # Boundary case: equal to max should still be accepted.
    policy.check_admit(
        tx=tx,
        meta=meta,
        pool_size=0,
        capacity=10_000,
        is_local=False,
    )


# -------------------------
# Oversize detection & error fields
# -------------------------


def test_oversized_tx_raises_oversize_with_correct_fields() -> None:
    """
    A transaction whose serialized size exceeds max_tx_size_bytes must
    be rejected with an Oversize error that carries the actual size
    and limit in its structured fields.

    This test asserts:
      - The raised error type is Oversize.
      - The numeric code is MempoolErrorCode.OVERSIZE.
      - The reason is the dedicated oversize reason string.
      - context["size_bytes"] and context["max_bytes"] are populated correctly.
      - The human message includes both the actual size and the limit.
    """
    max_bytes = 1024
    policy = _policy(max_bytes=max_bytes)

    actual_size = 2048
    tx = FakeTx(sender=b"C" * 20, effective_fee_wei=1_000_000_000)
    meta = FakeMeta(size_bytes=actual_size, effective_fee_wei=1_000_000_000)

    with pytest.raises(Oversize) as excinfo:
        policy.check_admit(
            tx=tx,
            meta=meta,
            pool_size=0,
            capacity=10_000,
            is_local=False,
        )

    err: Oversize = excinfo.value  # type: ignore[assignment]

    # Numeric code and reason should be stable for metrics and JSON-RPC.
    assert err.code == MempoolErrorCode.OVERSIZE
    assert err.reason in ("tx_too_large", "oversize")

    # Structured context must expose actual size and configured limit.
    assert err.context.get("size_bytes") == actual_size
    assert err.context.get("max_bytes") == max_bytes

    # The human-readable message should mention both values.
    msg = err.message
    assert str(actual_size) in msg
    assert str(max_bytes) in msg
