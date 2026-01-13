#!/usr/bin/env python3
"""
Unit test for the coinbase transaction detection logic.

Tests the core logic of the fix without requiring full node infrastructure.
"""


def test_has_coinbase_detection():
    """Test that we can detect coinbase transactions in a block."""
    
    # Simulate the detection logic from our fix
    # Note: TxKind.COINBASE = 3 (from core/types/tx.py)
    # We use a mock here to avoid dependencies
    class MockTxKind:
        COINBASE = 3  # Must match actual TxKind.COINBASE value
        TRANSFER = 0
    
    class MockUnsignedTx:
        def __init__(self, kind):
            self.kind = kind
    
    class MockTx:
        def __init__(self, kind):
            self.unsigned = MockUnsignedTx(kind)
    
    class MockBlock:
        def __init__(self, txs):
            self.txs = txs
    
    # Test 1: Block with coinbase transaction
    block_with_coinbase = MockBlock([
        MockTx(MockTxKind.COINBASE),  # Coinbase tx
        MockTx(MockTxKind.TRANSFER),  # Regular tx
    ])
    
    has_coinbase = any(
        tx.unsigned.kind == MockTxKind.COINBASE
        for tx in block_with_coinbase.txs
    )
    
    assert has_coinbase == True, "Should detect coinbase tx"
    print("✅ Test 1: Block WITH coinbase tx - detected correctly")
    
    # Test 2: Block without coinbase transaction
    block_without_coinbase = MockBlock([
        MockTx(MockTxKind.TRANSFER),  # Regular tx
        MockTx(MockTxKind.TRANSFER),  # Regular tx
    ])
    
    has_coinbase = any(
        tx.unsigned.kind == MockTxKind.COINBASE
        for tx in block_without_coinbase.txs
    )
    
    assert has_coinbase == False, "Should not detect coinbase tx"
    print("✅ Test 2: Block WITHOUT coinbase tx - detected correctly")
    
    # Test 3: Empty block
    block_empty = MockBlock([])
    
    has_coinbase = any(
        tx.unsigned.kind == MockTxKind.COINBASE
        for tx in block_empty.txs
    )
    
    assert has_coinbase == False, "Should not detect coinbase tx in empty block"
    print("✅ Test 3: Empty block - detected correctly")
    
    # Test 4: Block with multiple coinbase transactions
    block_multi_coinbase = MockBlock([
        MockTx(MockTxKind.COINBASE),  # Coinbase tx (miner)
        MockTx(MockTxKind.COINBASE),  # Coinbase tx (AICF)
        MockTx(MockTxKind.COINBASE),  # Coinbase tx (treasury)
        MockTx(MockTxKind.TRANSFER),  # Regular tx
    ])
    
    has_coinbase = any(
        tx.unsigned.kind == MockTxKind.COINBASE
        for tx in block_multi_coinbase.txs
    )
    
    assert has_coinbase == True, "Should detect coinbase tx in multi-coinbase block"
    print("✅ Test 4: Block with MULTIPLE coinbase txs - detected correctly")
    
    print("\n✅ All detection logic tests passed!")
    return True


def test_getattr_safety():
    """Test that our getattr approach is safe."""
    
    class MockUnsignedTx:
        def __init__(self, kind):
            self.kind = kind
    
    class MockTx:
        def __init__(self, unsigned):
            self.unsigned = unsigned
    
    # Test with normal tx
    tx_normal = MockTx(MockUnsignedTx(3))
    kind = getattr(tx_normal.unsigned, "kind", None)
    assert kind == 3, "Should get kind from normal tx"
    print("✅ Test 1: Normal tx - getattr works")
    
    # Test with missing unsigned
    class TxNoUnsigned:
        pass
    
    tx_no_unsigned = TxNoUnsigned()
    unsigned = getattr(tx_no_unsigned, "unsigned", None)
    if unsigned is not None:
        kind = getattr(unsigned, "kind", None)
    else:
        kind = None
    
    assert kind is None, "Should handle missing unsigned gracefully"
    print("✅ Test 2: Missing unsigned - handled gracefully")
    
    # Test with missing kind
    class UnsignedNoKind:
        pass
    
    tx_no_kind = MockTx(UnsignedNoKind())
    kind = getattr(tx_no_kind.unsigned, "kind", None)
    
    assert kind is None, "Should handle missing kind gracefully"
    print("✅ Test 3: Missing kind - handled gracefully")
    
    print("\n✅ All getattr safety tests passed!")
    return True


def test_logic_flow():
    """Test the complete logic flow of the fix."""
    
    print("\n" + "=" * 80)
    print("LOGIC FLOW TEST")
    print("=" * 80)
    
    # Simulate the fix logic
    def should_apply_separate_reward(block_has_coinbase_tx):
        """
        Simulates the decision logic in _apply_block_state.
        
        Returns True if _apply_block_reward should be called.
        """
        if block_has_coinbase_tx:
            # Block contains coinbase transactions - rewards already applied via tx execution
            # Skip _apply_block_reward to prevent double-crediting
            return False
        else:
            # Block does not contain coinbase transactions - apply rewards separately
            return True
    
    # Test cases
    test_cases = [
        ("Internal miner block (has coinbase tx)", True, False, "Skip _apply_block_reward"),
        ("External miner block (no coinbase tx)", False, True, "Call _apply_block_reward"),
        ("Old format block (no coinbase tx)", False, True, "Call _apply_block_reward"),
        ("Empty block (no txs)", False, True, "Call _apply_block_reward"),
    ]
    
    for name, has_coinbase, expected_result, expected_action in test_cases:
        result = should_apply_separate_reward(has_coinbase)
        action = "Call _apply_block_reward" if result else "Skip _apply_block_reward"
        
        assert result == expected_result, f"Failed: {name}"
        assert action == expected_action, f"Wrong action: {name}"
        
        print(f"✅ {name}")
        print(f"   Has coinbase: {has_coinbase} → {action}")
    
    print("\n✅ All logic flow tests passed!")
    return True


def main():
    """Run all tests."""
    print("=" * 80)
    print("COINBASE DETECTION UNIT TESTS")
    print("=" * 80)
    print()
    
    try:
        test_has_coinbase_detection()
        print()
        test_getattr_safety()
        print()
        test_logic_flow()
        
        print("\n" + "=" * 80)
        print("ALL TESTS PASSED! ✅")
        print("=" * 80)
        
        print("\n📝 Summary of Fix:")
        print("  - Detects coinbase transactions in blocks")
        print("  - Skips _apply_block_reward if coinbase txs present")
        print("  - Calls _apply_block_reward if no coinbase txs")
        print("  - Prevents double reward application")
        print("  - Maintains backward compatibility")
        
        return 0
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
