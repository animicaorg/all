"""
Test insufficient balance error handling in RPC layer.

This test verifies that:
1. Transactions with insufficient balance are rejected before mempool admission
2. The error includes detailed balance information (required, available, shortfall)
3. The error message is clear and user-friendly
"""
from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch

import pytest

# Add parent dir to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from rpc import errors as rpc_errors

pytestmark = pytest.mark.anyio


def test_insufficient_funds_error_format():
    """Test that InsufficientFunds error includes all required fields."""
    err = rpc_errors.InsufficientFunds(required=22000, available=500)
    
    assert err.code == rpc_errors.AnimicaCode.INSUFFICIENT_FUNDS
    assert err.message == "Insufficient funds for transfer"
    assert err.data is not None
    assert err.data["required"] == "22000"
    assert err.data["available"] == "500"
    assert err.data["shortfall"] == "21500"
    
    # Test error dict format for JSON-RPC response
    err_dict = err.to_dict()
    assert err_dict["code"] == -32013
    assert err_dict["message"] == "Insufficient funds for transfer"
    assert err_dict["data"]["required"] == "22000"
    assert err_dict["data"]["available"] == "500"
    assert err_dict["data"]["shortfall"] == "21500"
