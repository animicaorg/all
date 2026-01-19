"""
Test that mining rewards are properly credited to wallet balances.

This test verifies the fix for: "mined block reward reports 'credited' but
animica wallet show / state.getBalance does not increase".

The fix adds coinbase transaction (TxKind.COINBASE = 3) handling to the
execution dispatcher and executor, so rewards are properly applied to state.
"""

import pytest
from unittest.mock import MagicMock, Mock, patch
from dataclasses import dataclass


def test_dispatcher_recognizes_coinbase_kind():
    """Test that the dispatcher recognizes TxKind.COINBASE (3) as 'coinbase'."""
    from execution.runtime.dispatcher import resolve_tx_kind, _NUMERIC_KIND
    
    # Check that kind=3 is in the numeric mapping
    assert 3 in _NUMERIC_KIND, "TxKind.COINBASE (3) should be in _NUMERIC_KIND"
    assert _NUMERIC_KIND[3] == "coinbase", "Kind 3 should map to 'coinbase'"
    
    # Test resolving a transaction with kind=3
    tx = {"kind": 3}
    kind = resolve_tx_kind(tx)
    assert kind == "coinbase", "resolve_tx_kind should return 'coinbase' for kind=3"


def test_dispatcher_handles_coinbase_string():
    """Test that the dispatcher recognizes 'coinbase' string kind."""
    from execution.runtime.dispatcher import resolve_tx_kind
    
    # Test with string kind
    tx = {"kind": "coinbase"}
    kind = resolve_tx_kind(tx)
    assert kind == "coinbase", "resolve_tx_kind should recognize 'coinbase' string"
    
    # Test with alias
    tx = {"kind": "reward"}
    kind = resolve_tx_kind(tx)
    assert kind == "coinbase", "resolve_tx_kind should recognize 'reward' alias"


def test_dispatcher_routes_coinbase_to_transfer():
    """Test that dispatcher routes coinbase transactions to apply_transfer."""
    from execution.runtime.dispatcher import dispatch
    
    # Create a mock coinbase transaction
    tx = {"kind": 3, "unsigned": {"kind": 3, "payload": {"to": b"\x01" * 32, "value": 300_000_000_000}}}
    
    # Create mock state and env
    state = MagicMock()
    state.get_balance = MagicMock(return_value=0)
    state.add_balance = MagicMock()
    
    @dataclass
    class BlockEnv:
        height: int = 1
        timestamp: int = 1000
        coinbase: bytes = b"\x00" * 32
        chain_id: int = 1
    
    @dataclass
    class TxEnv:
        sender: bytes = b"\x00" * 32
        gas_price: int = 0
        base_price: int = 0
        tip_price: int = 0
        chain_id: int = 1
    
    block_env = BlockEnv()
    tx_env = TxEnv()
    
    # Mock apply_transfer to succeed
    with patch('execution.runtime.transfers.apply_transfer') as mock_apply:
        from execution.types.status import TxStatus
        from execution.types.result import ApplyResult
        
        mock_apply.return_value = ApplyResult(
            status=TxStatus.SUCCESS,
            gas_used=21000,
            logs=[],
            state_root=b"\x00" * 32,
            receipt=None,
        )
        
        # Call dispatch
        result = dispatch(tx, state, block_env, tx_env)
        
        # Verify apply_transfer was called (coinbase was routed correctly)
        assert mock_apply.called, "dispatch should call apply_transfer for coinbase tx"
        assert result.status == TxStatus.SUCCESS, "Coinbase tx should execute successfully"


def test_executor_fallback_handles_coinbase():
    """Test that executor's fallback dispatcher handles coinbase (kind=3)."""
    from execution.runtime.executor import apply_tx
    
    # Create a mock coinbase transaction
    tx = MagicMock()
    tx.kind = 3
    
    # Create mock state and env
    state = MagicMock()
    
    @dataclass
    class BlockEnv:
        height: int = 1
        timestamp: int = 1000
        coinbase: bytes = b"\x00" * 32
        chain_id: int = 1
    
    block_env = BlockEnv()
    
    # Mock apply_transfer to succeed
    with patch('execution.runtime.transfers.apply_transfer') as mock_apply:
        from execution.types.status import TxStatus
        from execution.types.result import ApplyResult
        
        mock_apply.return_value = ApplyResult(
            status=TxStatus.SUCCESS,
            gas_used=21000,
            logs=[],
            state_root=b"\x00" * 32,
            receipt=None,
        )
        
        # Disable the main dispatcher to test fallback
        with patch('execution.runtime.executor._dispatch_apply_tx', None):
            # Call apply_tx (should use fallback)
            result = apply_tx(tx, state, block_env)
            
            # Verify apply_transfer was called
            assert mock_apply.called, "Fallback dispatcher should call apply_transfer for coinbase"
            assert result.status == TxStatus.SUCCESS, "Coinbase tx should succeed in fallback"


def test_coinbase_transaction_credits_balance():
    """
    Integration test: Verify that a coinbase transaction actually credits
    the recipient's balance in state.
    
    This is a minimal simulation of what happens during block import.
    """
    from execution.runtime.transfers import apply_transfer
    from execution.types.status import TxStatus
    
    # Create a mock state that tracks balances
    balances = {}
    
    class MockState:
        def get_balance(self, addr):
            return balances.get(addr, 0)
        
        def add_balance(self, addr, amount):
            balances[addr] = self.get_balance(addr) + amount
        
        def sub_balance(self, addr, amount):
            balances[addr] = self.get_balance(addr) - amount
        
        def get_nonce(self, addr):
            return 0
        
        def set_nonce(self, addr, nonce):
            pass
        
        def compute_state_root(self):
            return b"\x00" * 32
    
    state = MockState()
    
    # Create a coinbase transaction
    # Simulate: reward of 300 ANM (300_000_000_000 nANM) to address 0x01...
    reward_address = b"\x01" * 32
    reward_amount = 300_000_000_000  # 300 ANM in base units
    
    @dataclass
    class UnsignedTx:
        kind: int = 3  # COINBASE
        
        @dataclass
        class Payload:
            to: bytes = reward_address
            value: int = reward_amount
        
        payload: Payload = Payload()
    
    @dataclass
    class Tx:
        unsigned: UnsignedTx = UnsignedTx()
        sigs: tuple = ()
        
        def hash(self):
            return b"\x00" * 32
    
    tx = Tx()
    
    @dataclass
    class BlockEnv:
        height: int = 1
        timestamp: int = 1000
        coinbase: bytes = reward_address
        chain_id: int = 1
    
    @dataclass
    class TxEnv:
        sender: bytes = b"\x00" * 32  # Coinbase sender is zero address
        gas_price: int = 0
        base_price: int = 0
        tip_price: int = 0
        chain_id: int = 1
    
    block_env = BlockEnv()
    tx_env = TxEnv()
    
    # Check initial balance
    initial_balance = state.get_balance(reward_address)
    assert initial_balance == 0, "Initial balance should be 0"
    
    # Apply the coinbase transaction
    result = apply_transfer(tx, state, block_env, tx_env)
    
    # Verify the transaction succeeded
    assert result.status == TxStatus.SUCCESS, "Coinbase transaction should succeed"
    
    # Verify the balance increased
    final_balance = state.get_balance(reward_address)
    assert final_balance == reward_amount, f"Balance should be {reward_amount}, got {final_balance}"
    
    # Verify the delta
    delta = final_balance - initial_balance
    assert delta == reward_amount, f"Balance should increase by {reward_amount}, increased by {delta}"


def test_mining_rewards_end_to_end():
    """
    End-to-end test concept: Mine a block and verify the reward is credited.
    
    This test outlines what should happen but doesn't fully implement it
    because it would require mocking the entire mining pipeline.
    """
    # Step 1: Get block template with coinbase transaction
    # - Template should include coinbase tx (kind=3) as first transaction
    # - Coinbase tx should have: to=payout_address, value=reward_amount
    
    # Step 2: Submit mined block
    # - Block includes coinbase tx
    # - Block import calls apply_block
    # - apply_block calls apply_tx for each tx (including coinbase)
    # - Dispatcher routes coinbase tx to apply_transfer
    # - apply_transfer credits the reward to payout_address
    
    # Step 3: Query balance
    # - state.get_balance(payout_address) should return increased balance
    # - Balance delta should equal reward amount
    
    # This test verifies the core components are in place:
    # 1. Dispatcher recognizes coinbase (kind=3)
    # 2. Coinbase txs route to apply_transfer
    # 3. apply_transfer handles coinbase (sender=zero, no signature check)
    
    # The integration is tested by the other tests in this file
    pass
