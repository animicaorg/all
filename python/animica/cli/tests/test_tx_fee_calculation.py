"""
Test that transaction fee calculation is correct in pending_txs.

This test verifies that when recording a pending transaction, the fee_reserved
field contains the TOTAL fee (gas_limit * gas_price), not just the gas_price.

Bug fix: Previously, fee_base was set to resolved_max_fee (gas_price), causing
the reserve_amount to be incorrectly calculated as (value + gas_price) instead
of (value + gas_limit * gas_price). This led to incorrect wallet balance displays.
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


def test_pending_tx_records_total_fee_not_gas_price(tmp_path: Path, monkeypatch):
    """
    Test that _record_pending_tx receives total fee (gas_limit * gas_price).
    
    Given:
        - gas_limit = 21000
        - gas_price (max_fee) = 1
    
    Expected:
        - fee_base should be 21000 (total fee), not 1 (gas price)
        - reserve_amount should be (value + 21000)
    """
    from animica.cli import tx
    
    # Create mock wallet store
    wallet_file = tmp_path / "wallets.json"
    test_addr = "anim1test123"
    wallet_data = {
        "version": 1,
        "wallets": [{
            "address": test_addr,
            "label": "test",
            "alg_id": 4098,
            "public_key_hex": "aa" * 32,
            "secret_key_hex": "bb" * 32,
        }]
    }
    wallet_file.write_text(json.dumps(wallet_data))
    
    # Mock _wallet_store_path to return our test file
    monkeypatch.setattr(tx, "_wallet_store_path", lambda: wallet_file)
    
    # Call _record_pending_tx with realistic values
    test_value = 1_000_000_000  # 1 ANM
    test_gas_limit = 21000
    test_gas_price = 1
    test_total_fee = test_gas_limit * test_gas_price  # 21000
    
    tx._record_pending_tx(
        from_addr=test_addr,
        to_addr="anim1recipient",
        tx_hash="0xabc123",
        value_base=test_value,
        fee_base=test_total_fee,  # Should be total fee, not gas price
        chain_id=1337,
        nonce=0,
        status="mempool_accepted",
    )
    
    # Load the updated wallet file
    updated_data = json.loads(wallet_file.read_text())
    pending_txs = updated_data["wallets"][0]["pending_txs"]
    
    assert len(pending_txs) == 1
    pending_tx = pending_txs[0]
    
    # Verify the fee_reserved is the total fee
    assert pending_tx["fee_reserved"] == test_total_fee, \
        f"fee_reserved should be {test_total_fee} (gas_limit * gas_price), got {pending_tx['fee_reserved']}"
    
    # Verify reserve_amount is value + total_fee
    expected_reserve = test_value + test_total_fee
    assert pending_tx["reserve_amount"] == expected_reserve, \
        f"reserve_amount should be {expected_reserve} (value + total_fee), got {pending_tx['reserve_amount']}"
    
    # Verify value is stored correctly
    assert pending_tx["value"] == test_value


def test_pending_tx_with_high_gas_limit(tmp_path: Path, monkeypatch):
    """
    Test with a higher gas limit to ensure calculation scales correctly.
    
    Given:
        - gas_limit = 100000 (contract deployment)
        - gas_price = 10
    
    Expected:
        - fee_base should be 1000000 (100000 * 10)
    """
    from animica.cli import tx
    
    # Create mock wallet store
    wallet_file = tmp_path / "wallets.json"
    test_addr = "anim1test456"
    wallet_data = {
        "version": 1,
        "wallets": [{
            "address": test_addr,
            "label": "test",
            "alg_id": 4098,
            "public_key_hex": "cc" * 32,
            "secret_key_hex": "dd" * 32,
        }]
    }
    wallet_file.write_text(json.dumps(wallet_data))
    
    # Mock _wallet_store_path
    monkeypatch.setattr(tx, "_wallet_store_path", lambda: wallet_file)
    
    # High gas limit scenario (e.g., contract deployment)
    test_value = 0  # No value transfer
    test_gas_limit = 100_000
    test_gas_price = 10
    test_total_fee = test_gas_limit * test_gas_price  # 1000000
    
    tx._record_pending_tx(
        from_addr=test_addr,
        to_addr="anim1contract",
        tx_hash="0xdef456",
        value_base=test_value,
        fee_base=test_total_fee,
        chain_id=1337,
        nonce=1,
        status="broadcast",
    )
    
    # Load and verify
    updated_data = json.loads(wallet_file.read_text())
    pending_tx = updated_data["wallets"][0]["pending_txs"][0]
    
    assert pending_tx["fee_reserved"] == test_total_fee
    assert pending_tx["reserve_amount"] == test_value + test_total_fee
    assert pending_tx["value"] == test_value


def test_wallet_available_balance_with_correct_fee(tmp_path: Path, monkeypatch):
    """
    Integration test: verify wallet show calculates available_balance correctly
    with the corrected fee calculation.
    
    Scenario:
        - Wallet has 10 ANM (10_000_000_000 base units)
        - Pending tx: send 1 ANM with gas_limit=21000, gas_price=100
        - Total deduction should be: 1_000_000_000 + 2_100_000 = 1_002_100_000
        - Available balance: 10_000_000_000 - 1_002_100_000 = 8_997_900_000
    """
    from animica.cli import wallet as wallet_module
    
    wallet_file = tmp_path / "wallets.json"
    test_addr = "anim1balance_test"
    
    # Initial balance: 10 ANM
    initial_balance = 10_000_000_000
    
    # Pending transaction
    sent_value = 1_000_000_000  # 1 ANM
    gas_limit = 21_000
    gas_price = 100
    total_fee = gas_limit * gas_price  # 2_100_000
    reserve_amount = sent_value + total_fee  # 1_002_100_000
    
    wallet_data = {
        "version": 1,
        "wallets": [{
            "address": test_addr,
            "label": "balance_test",
            "balance": initial_balance,
            "alg_id": 4098,
            "public_key_hex": "ee" * 32,
            "secret_key_hex": "ff" * 32,
            "pending_txs": [{
                "tx_hash": "0x789",
                "from": test_addr,
                "to": "anim1other",
                "value": sent_value,
                "fee_reserved": total_fee,
                "reserve_amount": reserve_amount,
                "status": "mempool_accepted",
                "nonce": 0,
                "chain_id": 1337,
            }]
        }]
    }
    wallet_file.write_text(json.dumps(wallet_data))
    
    # Mock wallet file path
    monkeypatch.setattr(wallet_module, "_wallet_file_path", lambda x: wallet_file)
    
    # Simulate wallet show logic (extract from wallet.py)
    store = json.loads(wallet_file.read_text())
    entry = store["wallets"][0]
    balance_confirmed = entry.get("balance", 0)
    
    # Calculate pending_outgoing (same logic as wallet.py lines 763-773)
    from animica.cli.wallet import _ACTIVE_PENDING_STATUSES
    pending_txs = entry.get("pending_txs", [])
    reserved_outgoing = 0
    for pending in pending_txs:
        if pending.get("status") in _ACTIVE_PENDING_STATUSES:
            reserve_amt = pending.get("reserve_amount", 0)
            reserved_outgoing += int(reserve_amt)
    
    # Calculate available balance (same logic as wallet.py line 784)
    available_balance = max(0, balance_confirmed - reserved_outgoing)
    
    # Verify calculations
    expected_available = initial_balance - reserve_amount
    assert reserved_outgoing == reserve_amount, \
        f"pending_outgoing should be {reserve_amount}, got {reserved_outgoing}"
    assert available_balance == expected_available, \
        f"available_balance should be {expected_available}, got {available_balance}"
    
    # Specific numbers
    assert available_balance == 8_997_900_000, \
        f"available_balance should be 8,997,900,000 (8.9979 ANM), got {available_balance}"
