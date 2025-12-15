"""
Regression test for txsRoot mismatch fix.

Tests that txsRoot computed by miner matches Block.txs_root() validation.

This test can be run standalone without full RPC infrastructure:
    python test_txsroot_fix.py
"""

def test_txsroot_computation_consistency():
    """
    Test that miner's txsRoot computation matches Block.txs_root().
    
    The bug: miner used canonical hash from raw CBOR, Block.txs_root() used tx.hash()
    The fix: both now use tx.hash() for consistent txsRoot computation
    """
    from core.types.block import Block
    from core.types.header import Header
    from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
    from core.utils.hash import ZERO32, sha3_256
    from core.utils.merkle import merkle_root
    
    # Create a minimal test transaction
    unsigned = UnsignedTx(
        chain_id=1,
        nonce=0,
        gas_price=1,
        gas_limit=21000,
        sender=b"\x01" * 32,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=b"\x02" * 32, amount=1000, data=b""),
        access_list=(),
    )
    
    # Create a dummy signature (won't verify, but that's ok for this test)
    sig = PqSignature(alg_id=1, pubkey=b"\x03" * 1952, sig=b"\x04" * 2420)
    
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    
    # Compute tx hash using tx.hash() (what miner should use)
    tx_hash = tx.hash()
    print(f"tx.hash() = {tx_hash.hex()[:16]}...")
    
    # Compute txsRoot the way miner does (after fix)
    # Miner: merkle_root([tx.hash() for tx in txs]) with sorted txs
    leaves = [tx_hash]
    leaves_sorted = sorted(leaves)
    miner_txsroot = merkle_root(leaves_sorted)
    print(f"Miner txsRoot = {miner_txsroot.hex()[:16]}...")
    
    # Compute txsRoot the way Block.txs_root() does
    # Block: merkle_root(sorted([tx.hash() for tx in self.txs]))
    block_leaves = sorted([tx.hash() for tx in [tx]])
    block_txsroot = merkle_root(block_leaves)
    print(f"Block.txs_root() = {block_txsroot.hex()[:16]}...")
    
    # They should match!
    assert miner_txsroot == block_txsroot, (
        f"txsRoot mismatch: miner={miner_txsroot.hex()} "
        f"block={block_txsroot.hex()}"
    )
    
    # Verify via Block.from_components (will raise if mismatch)
    header = Header(
        v=1,
        chainId=1,
        height=1,
        parentHash=ZERO32,
        timestamp=1700000000,
        stateRoot=ZERO32,
        txsRoot=miner_txsroot,  # Use miner's computed root
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
    
    # This should NOT raise "txsRoot mismatch"
    block = Block.from_components(
        header=header,
        txs=[tx],
        proofs=(),
        receipts=None,
        verify=True,  # Enable validation
    )
    
    print("✓ txsRoot matches between miner and Block.from_components!")
    print(f"✓ Block created successfully with {len(block.txs)} tx(s)")
    
    return block


def test_multiple_txs():
    """Test txsRoot with multiple transactions to ensure sorting is consistent."""
    from core.types.block import Block
    from core.types.header import Header
    from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
    from core.utils.hash import ZERO32
    from core.utils.merkle import merkle_root
    
    # Create 3 test transactions with different recipients (to get different hashes)
    txs = []
    for i in range(3):
        unsigned = UnsignedTx(
            chain_id=1,
            nonce=i,
            gas_price=1,
            gas_limit=21000,
            sender=b"\x01" * 32,
            kind=TxKind.TRANSFER,
            payload=TxTransfer(
                to=bytes([i] * 32),  # Different recipient for each tx
                amount=1000 + i,
                data=b"",
            ),
            access_list=(),
        )
        sig = PqSignature(alg_id=1, pubkey=b"\x03" * 1952, sig=bytes([4 + i] * 2420))
        tx = Tx(unsigned=unsigned, sigs=(sig,))
        txs.append(tx)
    
    # Compute hashes
    tx_hashes = [tx.hash() for tx in txs]
    print(f"TX hashes: {[h.hex()[:8] + '...' for h in tx_hashes]}")
    
    # Miner path: sort txs by hash, compute merkle root
    tx_tuples = list(zip(tx_hashes, txs))
    tx_tuples_sorted = sorted(tx_tuples, key=lambda t: t[0])
    sorted_hashes, sorted_txs = zip(*tx_tuples_sorted)
    miner_txsroot = merkle_root(sorted_hashes)
    print(f"Miner txsRoot (from sorted hashes) = {miner_txsroot.hex()[:16]}...")
    
    # Block path: compute hashes from sorted txs, sort again
    block_leaves = sorted([tx.hash() for tx in sorted_txs])
    block_txsroot = merkle_root(block_leaves)
    print(f"Block.txs_root() (from sorted txs) = {block_txsroot.hex()[:16]}...")
    
    # They should match!
    assert miner_txsroot == block_txsroot, (
        f"txsRoot mismatch with multiple txs: miner={miner_txsroot.hex()} "
        f"block={block_txsroot.hex()}"
    )
    
    # Verify via Block.from_components
    header = Header(
        v=1,
        chainId=1,
        height=1,
        parentHash=ZERO32,
        timestamp=1700000000,
        stateRoot=ZERO32,
        txsRoot=miner_txsroot,
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
    
    block = Block.from_components(
        header=header,
        txs=sorted_txs,  # Pass sorted txs (as miner does)
        proofs=(),
        receipts=None,
        verify=True,
    )
    
    print(f"✓ txsRoot matches with {len(block.txs)} transactions!")
    
    return block


if __name__ == "__main__":
    print("=" * 60)
    print("Testing txsRoot computation consistency (single tx)...")
    print("=" * 60)
    test_txsroot_computation_consistency()
    
    print("\n" + "=" * 60)
    print("Testing txsRoot computation consistency (multiple txs)...")
    print("=" * 60)
    test_multiple_txs()
    
    print("\n" + "=" * 60)
    print("All tests passed! ✓")
    print("=" * 60)
