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
ReplacementError = errors_mod.ReplacementError
MempoolErrorCode = errors_mod.MempoolErrorCode


@dataclass
class FakeMeta:
    """Minimal metadata object for RBF tests."""
    effective_fee_wei: int


def _policy() -> AdmissionPolicy:
    """
    Construct a minimal AdmissionPolicy suitable for replacement tests.

    Replacement policy is independent from fee floors / size limits, so we
    just feed default-ish values into AdmissionConfig.
    """
    cfg = AdmissionConfig(
        max_tx_size_bytes=128_000,
        accept_below_floor_for_local=True,
        min_effective_fee_override_wei=None,
        allow_chain_id=None,
    )
    return AdmissionPolicy(cfg=cfg, watermark=None)


# ---------------------------------------------------------------------------
# Default RBF bump logic (no priority override)
# ---------------------------------------------------------------------------


def test_replacement_rejected_when_bump_below_min_ratio() -> None:
    """
    With the default min_bump_ratio (1.10), a candidate replacement whose
    effective fee is below the required threshold must be rejected with a
    structured ReplacementError.

    We assert:
      - error type is ReplacementError
      - code == MempoolErrorCode.REPLACEMENT
      - reason is "replacement_underpriced"
      - context carries required_bump and effective gas prices
    """
    policy = _policy()

    old_fee = 1_000
    # Below the ~+10% requirement (required ~1100)
    new_fee = 1_050

    old_meta = FakeMeta(effective_fee_wei=old_fee)
    new_meta = FakeMeta(effective_fee_wei=new_fee)

    with pytest.raises(ReplacementError) as excinfo:
        policy.check_replacement(old_meta=old_meta, new_meta=new_meta)

    err: ReplacementError = excinfo.value  # type: ignore[assignment]

    # Numeric code and reason should be stable
    assert err.code == MempoolErrorCode.REPLACEMENT
    assert err.reason == "replacement_underpriced"

    # Context should expose the bump ratio and both effective fees
    ctx = err.context
    assert ctx.get("current_effective_gas_price_wei") == old_fee
    assert ctx.get("offered_effective_gas_price_wei") == new_fee
    assert ctx.get("required_bump") == pytest.approx(1.10, rel=1e-9)

    # The human message should mention "underpriced" or equivalent.
    assert "underpriced" in err.message or "bump" in err.message


def test_replacement_accepted_when_fee_meets_required_ratio() -> None:
    """
    A replacement whose effective fee is at least the required bumped amount
    should be accepted (no ReplacementError).
    """
    policy = _policy()

    old_fee = 1_000
    # With min_bump_ratio=1.10, this is just at the required threshold (~1100)
    new_fee = 1_100

    old_meta = FakeMeta(effective_fee_wei=old_fee)
    new_meta = FakeMeta(effective_fee_wei=new_fee)

    # Should NOT raise
    policy.check_replacement(old_meta=old_meta, new_meta=new_meta)


# ---------------------------------------------------------------------------
# Priority override via _rbf_ratio_from_priority
# ---------------------------------------------------------------------------


def test_rbf_min_bump_override_from_priority_module() -> None:
    """
    If the priority module (or any override) provides a custom bump ratio via
    _rbf_ratio_from_priority, that value should take precedence over the
    min_bump_ratio argument.

    We monkeypatch AdmissionPolicy._rbf_ratio_from_priority to simulate a
    higher requirement (e.g., 2.0x), then ensure that a tx which would have
    passed at 1.10x is now rejected and that ReplacementError.context reflects
    the overridden required_bump.
    """
    policy = _policy()

    old_fee = 1_000
    new_fee = 1_500  # > 1.10x but < 2.0x

    old_meta = FakeMeta(effective_fee_wei=old_fee)
    new_meta = FakeMeta(effective_fee_wei=new_fee)

    # Save original and monkeypatch to behave like a priority override.
    orig = AdmissionPolicy._rbf_ratio_from_priority

    try:
        # Simulate a priority policy that demands a 2.0x bump.
        def fake_rbf_ratio(old_meta_arg, new_meta_arg) -> float:
            return 2.0

        AdmissionPolicy._rbf_ratio_from_priority = staticmethod(fake_rbf_ratio)  # type: ignore[assignment]

        with pytest.raises(ReplacementError) as excinfo:
            # Even if we pass a smaller min_bump_ratio, the override should win.
            policy.check_replacement(
                old_meta=old_meta,
                new_meta=new_meta,
                min_bump_ratio=1.05,
            )

        err: ReplacementError = excinfo.value  # type: ignore[assignment]
        ctx = err.context

        assert ctx.get("current_effective_gas_price_wei") == old_fee
        assert ctx.get("offered_effective_gas_price_wei") == new_fee
        # Required bump should reflect the override (≈2.0x), not 1.05x.
        assert ctx.get("required_bump") == pytest.approx(2.0, rel=1e-9)

    finally:
        # Restore original method to avoid leaking state into other tests.
        AdmissionPolicy._rbf_ratio_from_priority = orig  # type: ignore[assignment]
