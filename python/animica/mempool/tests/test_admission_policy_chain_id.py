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
AdmissionError = errors_mod.AdmissionError


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


def _policy(allow_chain_id: Optional[int]) -> AdmissionPolicy:
    """
    Minimal AdmissionPolicy instance for chainId tests.

    We don't care about dynamic fee floors here, so we omit a FeeWatermark.
    """
    cfg = AdmissionConfig(
        allow_chain_id=allow_chain_id,
        max_tx_size_bytes=128_000,
    )
    return AdmissionPolicy(cfg=cfg, watermark=None)


def test_matching_chain_id_is_accepted() -> None:
    """
    When AdmissionConfig.allow_chain_id is set and tx.chain_id matches,
    admission should succeed (no AdmissionError).
    """
    policy = _policy(allow_chain_id=1337)

    tx = FakeTx(sender=b"A" * 20, nonce=0, chain_id=1337, effective_fee_wei=1_000)
    meta = FakeMeta(size_bytes=500, effective_fee_wei=1_000)

    # Should not raise
    policy.check_admit(
        tx=tx,
        meta=meta,
        pool_size=0,
        capacity=10_000,
        is_local=False,
    )


def test_mismatched_chain_id_is_rejected() -> None:
    """
    When allow_chain_id is set and tx.chain_id is present but different,
    AdmissionPolicy.check_admit must raise AdmissionError.
    """
    policy = _policy(allow_chain_id=42)

    tx = FakeTx(sender=b"B" * 20, nonce=0, chain_id=99, effective_fee_wei=1_000)
    meta = FakeMeta(size_bytes=500, effective_fee_wei=1_000)

    with pytest.raises(AdmissionError) as excinfo:
        policy.check_admit(
            tx=tx,
            meta=meta,
            pool_size=0,
            capacity=10_000,
            is_local=False,
        )

    # Sanity check on error message so callers can pattern-match if needed.
    msg = str(excinfo.value)
    assert "wrong chainId" in msg
    assert "expected 42" in msg


def test_allow_chain_id_none_bypasses_chain_id_checks() -> None:
    """
    When allow_chain_id is None, the chainId check is disabled and the
    transaction should not be rejected purely based on its chain_id field.
    """
    policy = _policy(allow_chain_id=None)

    # Deliberately "wrong" chainId relative to any specific network.
    tx = FakeTx(sender=b"C" * 20, nonce=0, chain_id=9999, effective_fee_wei=1_000)
    meta = FakeMeta(size_bytes=500, effective_fee_wei=1_000)

    # Should not raise AdmissionError even though chain_id looks arbitrary.
    policy.check_admit(
        tx=tx,
        meta=meta,
        pool_size=0,
        capacity=10_000,
        is_local=False,
    )


def test_missing_tx_chain_id_is_allowed_even_when_configured() -> None:
    """
    If allow_chain_id is set but the tx does not carry a chain_id at all,
    the current policy treats this as *not* a mismatch and allows it
    (other validators may still reject later).
    """
    policy = _policy(allow_chain_id=7)

    # No chain_id set on the tx
    tx = FakeTx(sender=b"D" * 20, nonce=0, chain_id=None, effective_fee_wei=1_000)
    meta = FakeMeta(size_bytes=500, effective_fee_wei=1_000)

    # Should not raise AdmissionError from the chainId gate.
    policy.check_admit(
        tx=tx,
        meta=meta,
        pool_size=0,
        capacity=10_000,
        is_local=False,
    )
