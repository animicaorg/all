#!/usr/bin/env python3
"""
Quick verification test for transaction handling fixes.
Tests that don't require full RPC infrastructure.
"""

def test_compute_txs_root():
    """Test the new compute_txs_root helper."""
    print("\n=== Testing compute_txs_root ===")
    
    from core.utils.merkle import compute_txs_root
    from core.utils.hash import ZERO32
    
    # Test 1: Empty list returns ZERO32
    root = compute_txs_root([])
    assert root == ZERO32, f"Empty txs should return ZERO32, got {root.hex()}"
    print("✓ Empty list returns ZERO32")
    
    # Test 2: Single tx
    tx_hash1 = b"\x01" * 32
    root = compute_txs_root([tx_hash1])
    assert root != ZERO32, "Single tx should not return ZERO32"
    print(f"✓ Single tx returns non-zero root: {root.hex()[:16]}...")
    
    # Test 3: Multiple txs (sorted order)
    tx_hash2 = b"\x02" * 32
    tx_hash3 = b"\x03" * 32
    
    # Test that order doesn't matter (compute_txs_root sorts internally)
    root1 = compute_txs_root([tx_hash1, tx_hash2, tx_hash3])
    root2 = compute_txs_root([tx_hash3, tx_hash1, tx_hash2])  # Different order
    root3 = compute_txs_root([tx_hash2, tx_hash3, tx_hash1])  # Yet another order
    
    assert root1 == root2 == root3, "Roots should match regardless of input order"
    print(f"✓ Multiple txs produce consistent root: {root1.hex()[:16]}...")
    
    # Test 4: Verify it matches Block.txs_root() behavior
    from core.types.block import Block
    from core.types.header import Header
    from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
    
    # Create dummy txs
    txs = []
    for i in range(3):
        unsigned = UnsignedTx(
            chain_id=1,
            nonce=i,
            gas_price=1,
            gas_limit=21000,
            sender=bytes([i+1]) * 32,
            kind=TxKind.TRANSFER,
            payload=TxTransfer(to=bytes([i+2]) * 32, amount=1000, data=b""),
            access_list=(),
        )
        # Dummy signature (won't verify but ok for this test)
        sig = PqSignature(alg_id=1, pubkey=b"\x03" * 1952, sig=bytes([4+i]) * 2420)
        tx = Tx(unsigned=unsigned, sigs=(sig,))
        txs.append(tx)
    
    # Compute using Block.txs_root()
    header = Header(
        v=1,
        chainId=1,
        height=1,
        parentHash=ZERO32,
        timestamp=1700000000,
        stateRoot=ZERO32,
        txsRoot=ZERO32,
        receiptsRoot=ZERO32,
        proofsRoot=ZERO32,
        daRoot=ZERO32,
        mixSeed=ZERO32,
        poiesPolicyRoot=ZERO32,
        pqAlgPolicyRoot=ZERO32,
        thetaMicro=1000000,
        nonce=0,
        extra=b"",
    )
    
    # Create block (will compute txs_root internally)
    block = Block.from_components(
        header=header,
        txs=txs,
        proofs=(),
        receipts=None,
        verify=False,  # Skip verification since we're testing the computation
    )
    block_txs_root = block.txs_root()
    
    # Compute using our helper
    tx_hashes = [tx.hash() for tx in txs]
    our_root = compute_txs_root(tx_hashes)
    
    assert block_txs_root == our_root, f"Roots should match: block={block_txs_root.hex()[:16]} ours={our_root.hex()[:16]}"
    print(f"✓ compute_txs_root matches Block.txs_root(): {our_root.hex()[:16]}...")
    
    print("\n=== All compute_txs_root tests passed! ===\n")
    return True


def test_receipt_module_loaded():
    """Test that receipt.py is loaded and tx.getTransactionReceipt is registered."""
    print("\n=== Testing receipt module loading ===")
    
    from rpc.methods import ensure_loaded, get_methods
    
    # Ensure modules are loaded
    ensure_loaded()
    
    # Check that tx.getTransactionReceipt is registered
    methods = get_methods()
    
    if "tx.getTransactionReceipt" in methods:
        print("✓ tx.getTransactionReceipt is registered")
    else:
        print("✗ tx.getTransactionReceipt is NOT registered")
        print(f"Available methods: {sorted(methods.keys())[:10]}...")
        return False
    
    # Check that it's not duplicated
    receipt_method = methods["tx.getTransactionReceipt"]
    print(f"✓ Method function: {receipt_method.func.__name__}")
    print(f"✓ Module: {receipt_method.func.__module__}")
    
    print("\n=== Receipt module loading test passed! ===\n")
    return True


if __name__ == "__main__":
    try:
        # Run tests
        success = True
        success = test_compute_txs_root() and success
        success = test_receipt_module_loaded() and success
        
        if success:
            print("\n" + "="*60)
            print("ALL VERIFICATION TESTS PASSED ✓")
            print("="*60)
        else:
            print("\n" + "="*60)
            print("SOME TESTS FAILED ✗")
            print("="*60)
            exit(1)
    except Exception as e:
        print(f"\n✗ TEST FAILED WITH EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
