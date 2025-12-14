"""
Unit tests for deterministic transaction ordering in txsRoot computation.

These tests verify that transactions are sorted by tx_hash before computing
the merkle root, ensuring consistent txsRoot values regardless of input order.

This fixes the "txsRoot mismatch" error that occurred when mining blocks with
transactions from the mempool.
"""

import pytest
from core.types.block import Block
from core.types.header import Header
from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
from core.utils.hash import ZERO32
from core.utils.merkle import merkle_root


def test_sorted_merkle_leaves_produce_deterministic_root():
    """
    Test that sorting transaction hashes before merkle root computation
    produces consistent results regardless of input order.
    
    This is the fundamental property needed to fix txsRoot mismatch.
    """
    # Create 3 transaction hashes in ascending order
    tx_hash_a = bytes.fromhex("1111111111111111111111111111111111111111111111111111111111111111")
    tx_hash_b = bytes.fromhex("5555555555555555555555555555555555555555555555555555555555555555")
    tx_hash_c = bytes.fromhex("9999999999999999999999999999999999999999999999999999999999999999")
    
    # Try different orderings (simulating different mempool iteration orders)
    order_1 = [tx_hash_a, tx_hash_b, tx_hash_c]
    order_2 = [tx_hash_c, tx_hash_a, tx_hash_b]
    order_3 = [tx_hash_b, tx_hash_c, tx_hash_a]
    
    # Without sorting, different orders produce different roots
    root_unsorted_1 = merkle_root(order_1)
    root_unsorted_2 = merkle_root(order_2)
    root_unsorted_3 = merkle_root(order_3)
    
    # Verify they differ (this proves ordering matters)
    assert root_unsorted_1 != root_unsorted_2 or root_unsorted_1 != root_unsorted_3, \
        "Different orderings should produce different roots when unsorted"
    
    # With sorting, all orders produce the same root
    root_sorted_1 = merkle_root(sorted(order_1))
    root_sorted_2 = merkle_root(sorted(order_2))
    root_sorted_3 = merkle_root(sorted(order_3))
    
    # All sorted roots should be identical
    assert root_sorted_1 == root_sorted_2 == root_sorted_3, \
        "Sorted merkle roots should be identical regardless of input order"
    
    print(f"✓ Sorted merkle root: {root_sorted_1.hex()[:16]}... (consistent across all orderings)")


def test_block_txs_root_handles_reordered_transactions():
    """
    Test that Block.txs_root() produces the same result regardless of
    transaction order in the input tuple.
    
    This verifies the fix in core/types/block.py.
    """
    # Create a minimal genesis header
    header = Header.genesis(
        chain_id=1,
        timestamp=1700000000,
        state_root=ZERO32,
        txs_root=ZERO32,
        receipts_root=ZERO32,
        proofs_root=ZERO32,
        da_root=ZERO32,
        mix_seed=b"\x42" * 32,
        poies_policy_root=b"\x11" * 32,
        pq_alg_policy_root=b"\x22" * 32,
        theta_micro=1000000,
        extra=b"",
    )
    
    # Create 3 dummy transactions with different hashes
    txs = []
    for i in range(3):
        sender = bytes([i + 1]) * 32
        recipient = bytes([100 + i]) * 32
        
        unsigned = UnsignedTx(
            chain_id=1,
            nonce=0,
            gas_price=1,
            gas_limit=21000,
            sender=sender,
            kind=TxKind.TRANSFER,
            payload=TxTransfer(to=recipient, amount=1000000000, data=b""),
            access_list=(),
        )
        
        # Dummy signature (hash will still work)
        sig = PqSignature(
            alg_id=0,
            pubkey=bytes([i + 1]) * 100,
            sig=bytes([200 + i]) * 200,
        )
        
        tx = Tx(unsigned=unsigned, sigs=(sig,))
        txs.append(tx)
    
    # Create blocks with transactions in different orders
    block_order_abc = Block(header=header, txs=tuple([txs[0], txs[1], txs[2]]), proofs=(), receipts=None)
    block_order_cba = Block(header=header, txs=tuple([txs[2], txs[1], txs[0]]), proofs=(), receipts=None)
    block_order_bca = Block(header=header, txs=tuple([txs[1], txs[2], txs[0]]), proofs=(), receipts=None)
    
    # Compute txs_root for each ordering
    root_abc = block_order_abc.txs_root()
    root_cba = block_order_cba.txs_root()
    root_bca = block_order_bca.txs_root()
    
    # All roots should be identical (due to sorting in Block.txs_root())
    assert root_abc == root_cba, "Block.txs_root() should produce same result for ABC and CBA ordering"
    assert root_abc == root_bca, "Block.txs_root() should produce same result for ABC and BCA ordering"
    
    print(f"✓ Block.txs_root() is deterministic: {root_abc.hex()[:16]}... (same for all tx orderings)")


def test_empty_block_txs_root_is_zero():
    """Test that empty blocks (no transactions) have zero txsRoot."""
    header = Header.genesis(
        chain_id=1,
        timestamp=1700000000,
        state_root=ZERO32,
        txs_root=ZERO32,
        receipts_root=ZERO32,
        proofs_root=ZERO32,
        da_root=ZERO32,
        mix_seed=b"\x42" * 32,
        poies_policy_root=b"\x11" * 32,
        pq_alg_policy_root=b"\x22" * 32,
        theta_micro=1000000,
        extra=b"",
    )
    
    block = Block(header=header, txs=(), proofs=(), receipts=None)
    root = block.txs_root()
    
    assert root == ZERO32, "Empty block should have zero txsRoot"
    print(f"✓ Empty block txsRoot is zero: {root.hex()}")


def test_single_transaction_block_has_consistent_root():
    """Test that a block with a single transaction produces consistent root."""
    header = Header.genesis(
        chain_id=1,
        timestamp=1700000000,
        state_root=ZERO32,
        txs_root=ZERO32,
        receipts_root=ZERO32,
        proofs_root=ZERO32,
        da_root=ZERO32,
        mix_seed=b"\x42" * 32,
        poies_policy_root=b"\x11" * 32,
        pq_alg_policy_root=b"\x22" * 32,
        theta_micro=1000000,
        extra=b"",
    )
    
    # Create a single transaction
    unsigned = UnsignedTx(
        chain_id=1,
        nonce=0,
        gas_price=1,
        gas_limit=21000,
        sender=bytes([1]) * 32,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=bytes([2]) * 32, amount=1000000000, data=b""),
        access_list=(),
    )
    
    sig = PqSignature(alg_id=0, pubkey=bytes([1]) * 100, sig=bytes([200]) * 200)
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    
    # Create block with single tx (sorting doesn't matter with 1 element)
    block = Block(header=header, txs=(tx,), proofs=(), receipts=None)
    root1 = block.txs_root()
    
    # Compute expected root manually
    expected_root = merkle_root([tx.hash()])
    
    assert root1 == expected_root, "Single transaction root should match manual computation"
    assert root1 != ZERO32, "Single transaction block should have non-zero txsRoot"
    
    print(f"✓ Single transaction block has consistent root: {root1.hex()[:16]}...")


if __name__ == "__main__":
    # Run tests manually if pytest not available
    print("Running deterministic transaction ordering tests...\n")
    
    test_sorted_merkle_leaves_produce_deterministic_root()
    test_block_txs_root_handles_reordered_transactions()
    test_empty_block_txs_root_is_zero()
    test_single_transaction_block_has_consistent_root()
    
    print("\n" + "="*70)
    print("All deterministic ordering tests passed!")
    print("="*70)
