"""
Test AICF credit minting split logic
=====================================

Validates that credit splitting is deterministic, exact, and handles edge cases.
"""

import pytest


def test_credit_split_basic():
    """Test basic split with default 10% AICF slice."""
    from aicf.credits.minting import compute_credit_split
    
    # 1000 nANM with 10% AICF slice
    miner_amount, aicf_credits = compute_credit_split(1000, aicf_slice_bps=1000)
    
    assert miner_amount == 900  # 90%
    assert aicf_credits == 100  # 10%
    assert miner_amount + aicf_credits == 1000  # total preserved


def test_credit_split_zero_amount():
    """Test split with zero amount."""
    from aicf.credits.minting import compute_credit_split
    
    miner_amount, aicf_credits = compute_credit_split(0, aicf_slice_bps=1000)
    
    assert miner_amount == 0
    assert aicf_credits == 0


def test_credit_split_zero_slice():
    """Test split with zero AICF slice (100% to miner)."""
    from aicf.credits.minting import compute_credit_split
    
    miner_amount, aicf_credits = compute_credit_split(1000, aicf_slice_bps=0)
    
    assert miner_amount == 1000  # 100%
    assert aicf_credits == 0  # 0%


def test_credit_split_max_slice():
    """Test split with 100% AICF slice."""
    from aicf.credits.minting import compute_credit_split
    
    miner_amount, aicf_credits = compute_credit_split(1000, aicf_slice_bps=10000)
    
    assert miner_amount == 0  # 0%
    assert aicf_credits == 1000  # 100%


def test_credit_split_rounding():
    """Test split with amounts that don't divide evenly."""
    from aicf.credits.minting import compute_credit_split
    
    # 999 nANM with 10% slice = 99.9 -> truncates to 99
    # Miner gets remainder: 999 - 99 = 900
    miner_amount, aicf_credits = compute_credit_split(999, aicf_slice_bps=1000)
    
    assert aicf_credits == 99  # floor(999 * 1000 / 10000)
    assert miner_amount == 900  # remainder
    assert miner_amount + aicf_credits == 999  # total preserved


def test_credit_split_large_amounts():
    """Test split with realistic block reward amounts."""
    from aicf.credits.minting import compute_credit_split
    
    # 300 ANM = 300_000_000_000 nANM
    total = 300_000_000_000
    miner_amount, aicf_credits = compute_credit_split(total, aicf_slice_bps=1000)
    
    assert aicf_credits == 30_000_000_000  # 30 ANM
    assert miner_amount == 270_000_000_000  # 270 ANM
    assert miner_amount + aicf_credits == total


def test_credit_split_various_bps():
    """Test split with different basis point values."""
    from aicf.credits.minting import compute_credit_split
    
    total = 10000
    
    # 5% = 500 bps
    m, a = compute_credit_split(total, aicf_slice_bps=500)
    assert a == 500
    assert m == 9500
    
    # 25% = 2500 bps
    m, a = compute_credit_split(total, aicf_slice_bps=2500)
    assert a == 2500
    assert m == 7500
    
    # 50% = 5000 bps
    m, a = compute_credit_split(total, aicf_slice_bps=5000)
    assert a == 5000
    assert m == 5000


def test_credit_split_invalid_bps():
    """Test that invalid basis points raise ValueError."""
    from aicf.credits.minting import compute_credit_split
    
    # Negative bps
    with pytest.raises(ValueError, match="aicf_slice_bps must be 0-10000"):
        compute_credit_split(1000, aicf_slice_bps=-1)
    
    # Too large bps
    with pytest.raises(ValueError, match="aicf_slice_bps must be 0-10000"):
        compute_credit_split(1000, aicf_slice_bps=10001)
    
    # Non-integer bps
    with pytest.raises(ValueError, match="aicf_slice_bps must be 0-10000"):
        compute_credit_split(1000, aicf_slice_bps="1000")  # type: ignore


def test_credit_split_invalid_amount():
    """Test that invalid amounts raise ValueError."""
    from aicf.credits.minting import compute_credit_split
    
    # Negative amount
    with pytest.raises(ValueError, match="total_amount must be non-negative int"):
        compute_credit_split(-1, aicf_slice_bps=1000)
    
    # Non-integer amount
    with pytest.raises(ValueError, match="total_amount must be non-negative int"):
        compute_credit_split(100.5, aicf_slice_bps=1000)  # type: ignore


def test_get_aicf_slice_bps_default():
    """Test getting AICF slice from params (default)."""
    from aicf.credits.minting import get_aicf_slice_bps
    
    # No params
    assert get_aicf_slice_bps(None) == 1000
    
    # Empty params
    assert get_aicf_slice_bps({}) == 1000


def test_get_aicf_slice_bps_from_params():
    """Test getting AICF slice from params."""
    from aicf.credits.minting import get_aicf_slice_bps
    
    params = {
        "monetary": {
            "issuance": {
                "aicf_slice_bps": 2000,  # 20%
            }
        }
    }
    
    assert get_aicf_slice_bps(params) == 2000


def test_get_aicf_slice_bps_invalid():
    """Test that invalid slice values fall back to default."""
    from aicf.credits.minting import get_aicf_slice_bps
    
    # Out of range
    params = {
        "monetary": {
            "issuance": {
                "aicf_slice_bps": 15000,  # Invalid
            }
        }
    }
    assert get_aicf_slice_bps(params) == 1000  # Fallback
    
    # Wrong type
    params = {
        "monetary": {
            "issuance": {
                "aicf_slice_bps": "not a number",
            }
        }
    }
    assert get_aicf_slice_bps(params) == 1000  # Fallback


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
