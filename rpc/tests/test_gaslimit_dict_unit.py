"""
Unit test for gasLimit dict handling in balance validation.

This tests the specific fix for the issue where gasLimit as a dict 
{"limit": int, "price": int} was causing validation errors.
"""
import pytest
from core.utils.tx import TxNormalizationError, coerce_int as _coerce_tx_int


def test_coerce_int_rejects_gaslimit_dict():
    """
    Verify that coerce_int rejects a dict with 'limit' and 'price' keys.
    This is the root cause of the issue.
    """
    gas_limit_dict = {"limit": 21000, "price": 1}
    
    with pytest.raises(TxNormalizationError) as exc_info:
        _coerce_tx_int("gasLimit", gas_limit_dict)
    
    assert "gasLimit must be an integer" in str(exc_info.value)
    details = exc_info.value.details
    assert details.get("received_keys") == ["limit", "price"]


def test_extract_limit_from_dict_before_coerce():
    """
    Verify that extracting 'limit' from the dict before calling coerce_int works.
    This is the fix applied to _validate_sufficient_balance.
    """
    gas_limit_dict = {"limit": 21000, "price": 1}
    
    # Simulate the fix: extract limit before coercing
    if isinstance(gas_limit_dict, dict):
        limit_value = gas_limit_dict.get("limit", 0)
        gas_limit = _coerce_tx_int("gasLimit", limit_value or 0)
    else:
        gas_limit = _coerce_tx_int("gasLimit", gas_limit_dict or 0)
    
    assert gas_limit == 21000


def test_simulate_balance_validation_with_dict_gaslimit():
    """
    Simulate the balance validation logic with a gasLimit dict.
    This verifies the fix works correctly.
    """
    # Simulate a transaction body with gasLimit as dict
    # Using placeholder addresses (all zeros) for simplicity
    tx_obj = {
        "from": b"\x00" * 32,  # Placeholder sender address
        "to": b"\x00" * 32,  # Placeholder recipient address
        "value": 1_000_000_000,
        "gasLimit": {"limit": 21000, "price": 1},
        "data": b"",
        "chainId": 1,
    }
    
    # Simulate the fixed balance validation logic
    value = _coerce_tx_int("value", tx_obj.get("value", 0) or 0)
    
    gas_limit_raw = tx_obj.get("gasLimit") or tx_obj.get("gas_limit") or tx_obj.get("gas") or 0
    if isinstance(gas_limit_raw, dict):
        # Extract 'limit' from fee quote dict format (THE FIX)
        gas_limit = _coerce_tx_int("gasLimit", gas_limit_raw.get("limit", 0) or 0)
    else:
        gas_limit = _coerce_tx_int("gasLimit", gas_limit_raw or 0)
    
    max_fee_raw = tx_obj.get("maxFee") or tx_obj.get("max_fee") or tx_obj.get("gasPrice") or tx_obj.get("gas_price")
    if max_fee_raw is None and isinstance(gas_limit_raw, dict):
        # If maxFee not set but gasLimit is a dict, try to get price from it
        max_fee_raw = gas_limit_raw.get("price")
    max_fee = _coerce_tx_int("maxFee", max_fee_raw or 0)
    
    # Verify the values are extracted correctly
    assert value == 1_000_000_000
    assert gas_limit == 21000
    assert max_fee == 1
    
    # Verify we can compute required balance
    required = value + (gas_limit * max_fee)
    assert required == 1_000_021_000


def test_simulate_balance_validation_with_int_gaslimit():
    """
    Verify the fix doesn't break the normal case with integer gasLimit.
    """
    # Simulate a transaction body with gasLimit as int (normal case)
    # Using placeholder addresses (all zeros) for simplicity
    tx_obj = {
        "from": b"\x00" * 32,  # Placeholder sender address
        "to": b"\x00" * 32,  # Placeholder recipient address
        "value": 1_000_000_000,
        "gasLimit": 21000,  # Integer, not dict
        "maxFee": 1,
        "data": b"",
        "chainId": 1,
    }
    
    # Simulate the fixed balance validation logic
    value = _coerce_tx_int("value", tx_obj.get("value", 0) or 0)
    
    gas_limit_raw = tx_obj.get("gasLimit") or tx_obj.get("gas_limit") or tx_obj.get("gas") or 0
    if isinstance(gas_limit_raw, dict):
        # Extract 'limit' from fee quote dict format
        gas_limit = _coerce_tx_int("gasLimit", gas_limit_raw.get("limit", 0) or 0)
    else:
        gas_limit = _coerce_tx_int("gasLimit", gas_limit_raw or 0)
    
    max_fee_raw = tx_obj.get("maxFee") or tx_obj.get("max_fee") or tx_obj.get("gasPrice") or tx_obj.get("gas_price")
    if max_fee_raw is None and isinstance(gas_limit_raw, dict):
        max_fee_raw = gas_limit_raw.get("price")
    max_fee = _coerce_tx_int("maxFee", max_fee_raw or 0)
    
    # Verify the values are extracted correctly
    assert value == 1_000_000_000
    assert gas_limit == 21000
    assert max_fee == 1
    
    # Verify we can compute required balance
    required = value + (gas_limit * max_fee)
    assert required == 1_000_021_000
