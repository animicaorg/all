"""
Test transaction application idempotency guard.

This test verifies that the has_applied_tx / mark_tx_applied functionality
prevents transactions from being applied twice.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB


def test_tx_idempotency_mark_and_check():
    """
    Test that mark_tx_applied and has_applied_tx work correctly.
    """
    # Create temporary database
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        kv = SQLiteKV(str(db_path))
        state = StateDB(kv)
        
        tx_hash = b"\xaa" * 32
        height = 100
        
        # Initially, tx should not be marked as applied
        assert not state.has_applied_tx(tx_hash), "TX should not be applied initially"
        assert state.get_tx_applied_height(tx_hash) is None, "TX height should be None"
        
        # Mark tx as applied
        state.mark_tx_applied(tx_hash, height)
        
        # Now it should be marked
        assert state.has_applied_tx(tx_hash), "TX should be marked as applied"
        assert state.get_tx_applied_height(tx_hash) == height, "TX height should match"
        
        # Close and reopen to verify persistence
        state.close()
        kv2 = SQLiteKV(str(db_path))
        state2 = StateDB(kv2)
        
        assert state2.has_applied_tx(tx_hash), "TX should still be marked after reopen"
        assert state2.get_tx_applied_height(tx_hash) == height, "TX height should still match"
        
        state2.close()


def test_tx_idempotency_multiple_txs():
    """
    Test that multiple transactions can be tracked independently.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        kv = SQLiteKV(str(db_path))
        state = StateDB(kv)
        
        tx1 = b"\x01" * 32
        tx2 = b"\x02" * 32
        tx3 = b"\x03" * 32
        
        # Mark tx1 and tx2 as applied
        state.mark_tx_applied(tx1, 100)
        state.mark_tx_applied(tx2, 101)
        
        # Check that tx1 and tx2 are marked, but tx3 is not
        assert state.has_applied_tx(tx1), "TX1 should be applied"
        assert state.has_applied_tx(tx2), "TX2 should be applied"
        assert not state.has_applied_tx(tx3), "TX3 should not be applied"
        
        # Check heights
        assert state.get_tx_applied_height(tx1) == 100
        assert state.get_tx_applied_height(tx2) == 101
        assert state.get_tx_applied_height(tx3) is None
        
        state.close()


def test_tx_idempotency_atomic_with_batch():
    """
    Test that mark_tx_applied works atomically with batch operations.
    
    This is critical: marking a tx as applied must be atomic with
    the balance mutations to ensure idempotency.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        kv = SQLiteKV(str(db_path))
        state = StateDB(kv)
        
        sender = b"\x11" * 32
        recipient = b"\x22" * 32
        tx_hash = b"\xab" * 32
        height = 50
        
        # Apply transaction with batch
        with state.batch() as batch:
            # Check idempotency first (should not be applied)
            if state.has_applied_tx(tx_hash):
                # Skip - already applied
                pass
            else:
                # Mark as applied
                state.mark_tx_applied(tx_hash, height, batch=batch)
                
                # Perform balance mutations
                state.set_balance(sender, 900, batch=batch)
                state.set_balance(recipient, 100, batch=batch)
        
        # Verify tx is marked and balances are updated
        assert state.has_applied_tx(tx_hash), "TX should be marked as applied"
        assert state.get_balance(sender) == 900, "Sender balance should be updated"
        assert state.get_balance(recipient) == 100, "Recipient balance should be updated"
        
        # Try to apply again (should be skipped due to idempotency)
        with state.batch() as batch:
            if state.has_applied_tx(tx_hash):
                # Skip - already applied
                pass
            else:
                # This should not happen
                state.mark_tx_applied(tx_hash, height + 1, batch=batch)
                state.set_balance(sender, 800, batch=batch)  # Wrong!
                state.set_balance(recipient, 200, batch=batch)  # Wrong!
        
        # Balances should be unchanged (tx was skipped)
        assert state.get_balance(sender) == 900, "Sender balance should not change"
        assert state.get_balance(recipient) == 100, "Recipient balance should not change"
        assert state.get_tx_applied_height(tx_hash) == height, "TX height should not change"
        
        state.close()


def test_tx_idempotency_different_hash_lengths():
    """
    Test that tx hashes of different lengths work correctly.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        kv = SQLiteKV(str(db_path))
        state = StateDB(kv)
        
        # Standard 32-byte hash
        tx_32 = b"\x01" * 32
        state.mark_tx_applied(tx_32, 100)
        assert state.has_applied_tx(tx_32)
        
        # Shorter hash (20 bytes, like Ethereum)
        tx_20 = b"\x02" * 20
        state.mark_tx_applied(tx_20, 101)
        assert state.has_applied_tx(tx_20)
        
        # Longer hash (64 bytes)
        tx_64 = b"\x03" * 64
        state.mark_tx_applied(tx_64, 102)
        assert state.has_applied_tx(tx_64)
        
        # Verify they don't interfere with each other
        assert state.get_tx_applied_height(tx_32) == 100
        assert state.get_tx_applied_height(tx_20) == 101
        assert state.get_tx_applied_height(tx_64) == 102
        
        state.close()


def test_snapshot_revert_restores_applied_tx_markers():
    """
    State snapshots must include idempotency markers so reorg/rebuild can
    deterministically restore tx-application guards.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        kv = SQLiteKV(str(db_path))
        state = StateDB(kv)

        tx_a = b"\xaa" * 32
        tx_b = b"\xbb" * 32

        state.mark_tx_applied(tx_a, 50)
        snap = state.snapshot()
        state.mark_tx_applied(tx_b, 51)

        assert state.has_applied_tx(tx_a)
        assert state.has_applied_tx(tx_b)

        state.revert(snap)

        assert state.has_applied_tx(tx_a)
        assert state.get_tx_applied_height(tx_a) == 50
        assert not state.has_applied_tx(tx_b)
        assert state.get_tx_applied_height(tx_b) is None

        state.close()
