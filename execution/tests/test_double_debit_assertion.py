"""
Test that the double-debit assertion catches multiple debits for same transaction.

This test verifies that if a bug causes the sender to be debited twice,
the assert_single_tx_balance_deltas function will detect it and raise an error.
"""

from __future__ import annotations

import os
import pytest
from types import SimpleNamespace

from execution.runtime.transfers import apply_transfer
from execution.state.apply_balance import (
    get_debug_balance_events,
    reset_debug_balance_events,
    _mutate_balance,
    assert_single_tx_balance_deltas,
)
from execution.types.status import TxStatus
from execution.errors import ExecError


class MockState:
    """Mock state that allows manual balance mutations for testing."""
    
    def __init__(self, balances: dict[bytes, int], nonces: dict[bytes, int] | None = None) -> None:
        self._balances = dict(balances)
        self._nonces = dict(nonces or {})

    def get_balance(self, addr: bytes) -> int:
        return int(self._balances.get(addr, 0))

    def set_balance(self, addr: bytes, value: int) -> None:
        self._balances[addr] = int(value)

    def get_nonce(self, addr: bytes) -> int:
        return int(self._nonces.get(addr, 0))

    def set_nonce(self, addr: bytes, value: int) -> None:
        self._nonces[addr] = int(value)

    def ensure_account(self, addr: bytes) -> None:
        self._balances.setdefault(addr, 0)


def test_double_debit_assertion_catches_bug(monkeypatch):
    """
    Simulate a double-debit bug and verify the assertion catches it.
    
    This test manually performs a second debit to simulate the bug,
    then verifies that assert_single_tx_balance_deltas raises an error.
    """
    monkeypatch.setenv("ANIMICA_DEBUG_BALANCE", "1")
    
    sender = b"\x11" * 32
    recipient = b"\x22" * 32
    tx_hash = "0xdouble-debit-test"
    
    reset_debug_balance_events()
    
    # Manually create two debits to simulate the bug
    _mutate_balance(
        MockState({sender: 100}),
        sender,
        -10,
        reason="FIRST_DEBIT",
        tx_hash=tx_hash,
        height=1,
        callsite="test.first_debit",
    )
    
    _mutate_balance(
        MockState({sender: 90}),
        sender,
        -10,
        reason="SECOND_DEBIT",
        tx_hash=tx_hash,
        height=1,
        callsite="test.second_debit",
    )
    
    # Credit recipient once
    _mutate_balance(
        MockState({recipient: 0}),
        recipient,
        +10,
        reason="RECIPIENT_CREDIT",
        tx_hash=tx_hash,
        height=1,
        callsite="test.credit",
    )
    
    # Now assert_single_tx_balance_deltas should detect the double debit
    with pytest.raises(ExecError) as exc_info:
        assert_single_tx_balance_deltas(
            tx_hash=tx_hash,
            sender=sender,
            recipient=recipient,
            amount=10,
            total_fee=0,
        )
    
    error = exc_info.value
    assert error.code == "DOUBLE_DEBIT_BUG"
    assert "2 debits" in str(error)
    
    # Verify complete error structure
    assert "FIRST_DEBIT" in str(error.data)
    assert "SECOND_DEBIT" in str(error.data)
    assert error.data["num_debits"] == 2
    assert len(error.data["callsites"]) == 2
    assert "test.first_debit" in error.data["callsites"]
    assert "test.second_debit" in error.data["callsites"]
    
    # Verify diagnosis field
    assert "diagnosis" in error.data
    assert "Multiple code paths" in error.data["diagnosis"]
    
    # Verify debits array structure
    assert "debits" in error.data
    assert len(error.data["debits"]) == 2
    for debit in error.data["debits"]:
        assert "delta" in debit
        assert "reason" in debit
        assert "site" in debit


def test_single_debit_assertion_passes(monkeypatch):
    """
    Verify that a correct single debit passes the assertion.
    """
    monkeypatch.setenv("ANIMICA_DEBUG_BALANCE", "1")
    
    sender = b"\x33" * 32
    recipient = b"\x44" * 32
    tx_hash = "0xsingle-debit-test"
    
    reset_debug_balance_events()
    
    # Single debit (correct behavior)
    _mutate_balance(
        MockState({sender: 100}),
        sender,
        -11,  # value + fee
        reason="SINGLE_DEBIT",
        tx_hash=tx_hash,
        height=1,
        callsite="test.single_debit",
    )
    
    # Credit recipient
    _mutate_balance(
        MockState({recipient: 0}),
        recipient,
        +10,
        reason="RECIPIENT_CREDIT",
        tx_hash=tx_hash,
        height=1,
        callsite="test.credit",
    )
    
    # This should NOT raise an error
    assert_single_tx_balance_deltas(
        tx_hash=tx_hash,
        sender=sender,
        recipient=recipient,
        amount=10,
        total_fee=1,
    )


def test_zero_debits_detected(monkeypatch):
    """
    Verify that missing sender debit is detected.
    """
    monkeypatch.setenv("ANIMICA_DEBUG_BALANCE", "1")
    
    sender = b"\x55" * 32
    recipient = b"\x66" * 32
    tx_hash = "0xzero-debit-test"
    
    reset_debug_balance_events()
    
    # Only credit recipient (no sender debit - BUG)
    _mutate_balance(
        MockState({recipient: 0}),
        recipient,
        +10,
        reason="RECIPIENT_CREDIT",
        tx_hash=tx_hash,
        height=1,
        callsite="test.credit",
    )
    
    # This should raise an error about missing debit
    with pytest.raises(ExecError) as exc_info:
        assert_single_tx_balance_deltas(
            tx_hash=tx_hash,
            sender=sender,
            recipient=recipient,
            amount=10,
            total_fee=0,
        )
    
    error = exc_info.value
    assert error.code == "SENDER_DEBIT_MISSING"


def test_wrong_amount_detected(monkeypatch):
    """
    Verify that incorrect debit amount is detected.
    """
    monkeypatch.setenv("ANIMICA_DEBUG_BALANCE", "1")
    
    sender = b"\x77" * 32
    recipient = b"\x88" * 32
    tx_hash = "0xwrong-amount-test"
    
    reset_debug_balance_events()
    
    # Debit wrong amount
    _mutate_balance(
        MockState({sender: 100}),
        sender,
        -5,  # Should be -11 (value=10 + fee=1)
        reason="WRONG_AMOUNT",
        tx_hash=tx_hash,
        height=1,
        callsite="test.wrong_debit",
    )
    
    # Credit recipient correct amount
    _mutate_balance(
        MockState({recipient: 0}),
        recipient,
        +10,
        reason="RECIPIENT_CREDIT",
        tx_hash=tx_hash,
        height=1,
        callsite="test.credit",
    )
    
    # This should raise an error about wrong amount
    with pytest.raises(ExecError) as exc_info:
        assert_single_tx_balance_deltas(
            tx_hash=tx_hash,
            sender=sender,
            recipient=recipient,
            amount=10,
            total_fee=1,
        )
    
    error = exc_info.value
    assert error.code == "SENDER_DEBIT_AMOUNT_MISMATCH"
    assert error.data["actual"] == -5
    assert error.data["expected"] == -11
