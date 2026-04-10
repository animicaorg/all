"""
Test InsufficientBalance error with detailed amounts.

This test verifies that:
1. InsufficientBalance error is raised with detailed information
2. The error includes required, available, and shortfall amounts
3. The error is properly propagated through the execution layer
"""
from __future__ import annotations

import pytest

from execution.errors import ExecError
from execution.state.apply_balance import InsufficientBalance, debit, credit


class MockState:
    """Mock state with get_balance and set_balance methods."""
    
    def __init__(self, initial_balances: dict[bytes, int] | None = None):
        self.balances = initial_balances or {}
    
    def get_balance(self, address: bytes) -> int:
        return self.balances.get(address, 0)
    
    def set_balance(self, address: bytes, value: int) -> None:
        self.balances[address] = value


def test_insufficient_balance_error_includes_amounts():
    """Test that InsufficientBalance error includes detailed amounts."""
    sender = b"sender_address_32bytes_padded!"
    state = MockState({sender: 1000})
    
    # Try to debit more than available
    with pytest.raises(InsufficientBalance) as exc_info:
        debit(state, sender, 5000)
    
    err = exc_info.value
    assert err.code == "INSUFFICIENT_BALANCE"
    assert err.data is not None
    assert "required" in err.data
    assert "available" in err.data
    assert "shortfall" in err.data
    
    # Verify amounts
    assert err.data["required"] == "5000"
    assert err.data["available"] == "1000"
    assert err.data["shortfall"] == "4000"


def test_insufficient_balance_error_message():
    """Test that InsufficientBalance error has a clear message."""
    err = InsufficientBalance(
        "Insufficient balance for transfer",
        required=5000,
        available=1000,
        shortfall=4000,
    )
    
    assert err.message == "Insufficient balance for transfer"
    assert err.code == "INSUFFICIENT_BALANCE"
    
    # Test to_dict format
    err_dict = err.to_dict()
    assert err_dict["code"] == "INSUFFICIENT_BALANCE"
    assert err_dict["message"] == "Insufficient balance for transfer"
    assert err_dict["data"]["required"] == "5000"
    assert err_dict["data"]["available"] == "1000"
    assert err_dict["data"]["shortfall"] == "4000"


def test_sufficient_balance_does_not_raise():
    """Test that debit succeeds when balance is sufficient."""
    sender = b"sender_address_32bytes_padded!"
    state = MockState({sender: 10000})
    
    # Should not raise
    new_balance = debit(state, sender, 5000)
    assert new_balance == 5000
    assert state.get_balance(sender) == 5000


def test_exact_balance_does_not_raise():
    """Test that debit succeeds when balance exactly matches debit amount."""
    sender = b"sender_address_32bytes_padded!"
    state = MockState({sender: 5000})
    
    # Should not raise
    new_balance = debit(state, sender, 5000)
    assert new_balance == 0
    assert state.get_balance(sender) == 0


def test_zero_debit_does_not_raise():
    """Test that zero debit is a no-op."""
    sender = b"sender_address_32bytes_padded!"
    state = MockState({sender: 1000})
    
    # Should not raise
    new_balance = debit(state, sender, 0)
    assert new_balance == 1000
    assert state.get_balance(sender) == 1000


def test_credit_increases_balance():
    """Test that credit increases balance correctly."""
    recipient = b"recipient_address_32bytes____!"
    state = MockState({recipient: 1000})
    
    new_balance = credit(state, recipient, 5000)
    assert new_balance == 6000
    assert state.get_balance(recipient) == 6000


def test_insufficient_balance_is_exec_error():
    """Test that InsufficientBalance is a subclass of ExecError."""
    err = InsufficientBalance()
    assert isinstance(err, ExecError)
    assert isinstance(err, Exception)
