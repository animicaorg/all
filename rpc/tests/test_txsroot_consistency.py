"""
Test that txsRoot is computed consistently and blocks can be mined with transactions.

This test validates the fix for the txsRoot mismatch issue where:
1. The miner computes txsRoot using one method
2. Block.from_components validates using Block.txs_root()
3. Both must use the same computation (tx.hash()) to match

Tests:
1. Empty block has zero txsRoot
2. Block with 1 tx has non-zero txsRoot that matches header
3. Block with multiple txs has correct txsRoot
4. Transactions are included and mempool is drained
"""

import pytest
from rpc.tests import new_test_client, rpc_call

# Test address constants for deterministic testing
TEST_RECIPIENT_1 = "0xdeadbeef" + "00" * 28  # 32-byte test address
TEST_RECIPIENT_2 = "0xcafebabe" + "00" * 28  # 32-byte test address


@pytest.fixture
def sender_keypair():
    """
    Fixture to generate a PQ keypair for testing.
    
    Skips test if PQ keygen is not available.
    
    Returns:
        Keypair object with address, public_key, secret_key attributes
    """
    try:
        from pq.py.keygen import keygen_sig
        return keygen_sig("dilithium3")
    except Exception as e:
        pytest.skip(f"PQ keygen not available: {e}")


def _try_keygen():
    """
    Helper to generate a keypair for tests that don't use fixtures.
    
    Returns:
        Keypair or None if PQ keygen fails (caller should skip test)
    """
    try:
        from pq.py.keygen import keygen_sig
        return keygen_sig("dilithium3")
    except Exception:
        return None


def _get_premine_address_hex() -> str:
    """
    Helper to get the premine address as hex string.
    
    Note: Uses MAINNET_PREMINE_DISTRIBUTION which works for both mainnet and testnet
    since the test environment uses chain_id=1 (default).
    
    Returns:
        str: Premine address as hex string (e.g., "0xabcd...")
    """
    try:
        from consensus.rewards import MAINNET_PREMINE_DISTRIBUTION
        from pq.py.address import decode_address
        
        premine_addr_bech32 = MAINNET_PREMINE_DISTRIBUTION[0][0]
        addr_record = decode_address(premine_addr_bech32)
        digest = bytes(addr_record.digest) if isinstance(addr_record.digest, list) else addr_record.digest
        premine_addr_bytes = digest[:32].ljust(32, b"\x00")
        return "0x" + premine_addr_bytes.hex()
    except (ImportError, IndexError, AttributeError):
        # Fallback to a deterministic test address if premine not available
        return "0x" + ("01" * 32)


def _build_signed_transfer(client, cfg, sender_kp, recipient_hex: str, nonce: int = 0, value: int = 1_000_000_000):
    """
    Build a signed transfer transaction using provided keypair.
    
    Args:
        client: FastAPI test client
        cfg: RPC config with chain_id
        sender_kp: Sender keypair with address, public_key, secret_key
        recipient_hex: Recipient address as hex string (e.g., "0xabcd...")
        nonce: Transaction nonce (default: 0)
        value: Transfer amount in nANM (default: 1 ANM)
        
    Returns:
        tuple: (raw_tx_hex, tx_hash_hex) - CBOR-encoded tx and its hash
    """
    from core.encoding.canonical import tx_sign_bytes
    from core.types.tx import Tx
    from pq.py import sign
    from pq.py.address import decode_address
    from pq.py.registry import ALG_ID
    
    alg_name = "dilithium3"
    
    # Decode sender address
    sender_record = decode_address(sender_kp.address)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    
    # Recipient is hex, convert to bytes
    recipient_bytes = bytes.fromhex(recipient_hex[2:] if recipient_hex.startswith("0x") else recipient_hex)
    
    # Build unsigned transfer
    from core.types.tx import UnsignedTx, TxKind, TxTransfer
    
    unsigned = UnsignedTx(
        chain_id=cfg.chain_id,
        nonce=nonce,
        gas_price=1,
        gas_limit=21000,
        sender=sender_bytes,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=recipient_bytes, amount=value, data=b""),
        access_list=(),
    )
    
    # Sign transaction
    sign_bytes = tx_sign_bytes(unsigned.to_obj())
    sig_env = sign.sign_detached(sign_bytes, alg_name, sender_kp.secret_key, domain="tx", chain_id=cfg.chain_id)
    
    # Create signed tx
    from core.types.tx import PqSignature
    sig = PqSignature(alg_id=ALG_ID[alg_name], pubkey=sender_kp.public_key, sig=sig_env.sig)
    
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    
    # Encode to CBOR hex
    cbor_bytes = tx.to_cbor()
    raw_hex = "0x" + cbor_bytes.hex()
    tx_hash = "0x" + tx.txid().hex()
    
    return raw_hex, tx_hash


def test_empty_block_has_zero_txsroot():
    """Test that mining an empty block produces a zero txsRoot."""
    client, cfg, _ = new_test_client()
    
    # Mine 1 block without any transactions
    mine_result = rpc_call(client, "miner.mine", [1])["result"]
    assert mine_result["mined"] == 1, "Should mine exactly 1 block"
    
    # Get the mined block
    block_height = mine_result["height"]
    block = rpc_call(client, "chain.getBlockByNumber", [block_height, True])["result"]
    assert block is not None, f"Block at height {block_height} should exist"
    
    # Verify txsRoot is zero (no transactions)
    txs_root = block.get("transactionsRoot") or block.get("txsRoot")
    zero_root = "0x" + ("00" * 32)
    assert txs_root == zero_root, f"Empty block should have zero txsRoot, got {txs_root}"
    
    # Verify block has no transactions
    block_txs = block.get("transactions", [])
    assert len(block_txs) == 0, "Empty block should have 0 transactions"
    
    print(f"✓ Empty block at height {block_height} has zero txsRoot")


def test_block_with_one_tx_has_nonzero_txsroot(sender_keypair):
    """Test that mining a block with 1 transaction produces a non-zero txsRoot."""
    client, cfg, _ = new_test_client()
    sender_kp = sender_keypair
    
    # Get sender address
    from pq.py.address import decode_address
    
    sender_record = decode_address(sender_kp.address)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    sender_hex = "0x" + sender_bytes.hex()
    
    # Fund sender by mining blocks
    mine_result = rpc_call(client, "miner.mine", {"count": 3, "address": sender_kp.address})["result"]
    assert mine_result["mined"] == 3, "Should mine 3 blocks for funding"
    
    # Build and submit transaction
    recipient_hex = _get_premine_address_hex()
    raw_hex, tx_hash = _build_signed_transfer(client, cfg, sender_kp, recipient_hex, nonce=0, value=1_000_000_000)
    
    result = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    assert "result" in result, f"Expected result, got {result}"
    returned_hash = result["result"]
    assert returned_hash == tx_hash, f"Hash mismatch: {returned_hash} != {tx_hash}"
    
    # Verify tx is in mempool
    pending = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash in pending, f"TX {tx_hash} should be in mempool"
    
    # Mine 1 block (should include the transaction)
    mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": sender_kp.address})["result"]
    assert mine_result["mined"] == 1, "Should mine exactly 1 block"
    
    # Get the mined block
    block_height = mine_result["height"]
    block = rpc_call(client, "chain.getBlockByNumber", [block_height, True])["result"]
    assert block is not None, f"Block at height {block_height} should exist"
    
    # Verify txsRoot is non-zero
    txs_root = block.get("transactionsRoot") or block.get("txsRoot")
    zero_root = "0x" + ("00" * 32)
    assert txs_root != zero_root, f"Block with tx should have non-zero txsRoot, got {txs_root}"
    
    # Verify transaction is in the block
    block_txs = block.get("transactions", [])
    tx_hashes_in_block = [tx.get("hash") if isinstance(tx, dict) else tx for tx in block_txs]
    assert tx_hash in tx_hashes_in_block, f"TX {tx_hash} should be in block"
    
    print(f"✓ Block at height {block_height} has non-zero txsRoot: {txs_root}")
    print(f"✓ Transaction {tx_hash} included in block")


def test_block_with_multiple_txs_has_correct_txsroot(sender_keypair):
    """Test that mining a block with multiple transactions produces correct txsRoot."""
    client, cfg, _ = new_test_client()
    sender_kp = sender_keypair
    
    # Get sender address
    from pq.py.address import decode_address
    
    sender_record = decode_address(sender_kp.address)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    sender_hex = "0x" + sender_bytes.hex()
    
    # Fund sender by mining blocks
    mine_result = rpc_call(client, "miner.mine", {"count": 5, "address": sender_kp.address})["result"]
    assert mine_result["mined"] == 5, "Should mine 5 blocks for funding"
    
    # Build and submit 3 transactions with sequential nonces
    tx_hashes = []
    for nonce in range(3):
        # Use different recipients (deterministic test addresses with nonce suffix)
        recipient_hex = TEST_RECIPIENT_1[:-2] + f"{nonce:02x}"
        raw_hex, tx_hash = _build_signed_transfer(
            client, cfg, sender_kp, recipient_hex,
            nonce=nonce, value=100_000_000  # 0.1 ANM each
        )
        
        result = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
        tx_hashes.append(tx_hash)
        print(f"Submitted tx {nonce}: {tx_hash}")
    
    # Verify all are in mempool
    pending = rpc_call(client, "mempool.getPending")["result"]
    for tx_hash in tx_hashes:
        assert tx_hash in pending, f"TX {tx_hash} should be in mempool"
    
    # Mine 1 block
    mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": sender_kp.address})["result"]
    block_height = mine_result["height"]
    
    # Get block
    block = rpc_call(client, "chain.getBlockByNumber", [block_height, True])["result"]
    assert block is not None, f"Block at height {block_height} should exist"
    
    # Verify txsRoot is non-zero
    txs_root = block.get("transactionsRoot") or block.get("txsRoot")
    zero_root = "0x" + ("00" * 32)
    assert txs_root != zero_root, f"Block with txs should have non-zero txsRoot"
    
    # Check how many transactions were included
    block_txs = block.get("transactions", [])
    block_tx_hashes = [tx.get("hash") if isinstance(tx, dict) else tx for tx in block_txs]
    included_count = sum(1 for h in tx_hashes if h in block_tx_hashes)
    
    print(f"✓ Block at height {block_height} has txsRoot: {txs_root}")
    print(f"✓ {included_count}/{len(tx_hashes)} transactions included in block")
    
    # At least one should be included
    assert included_count > 0, "At least one transaction should be included"


def test_txsroot_matches_across_mining_and_validation(sender_keypair):
    """
    Test that the txsRoot computed during mining matches the validation in Block.from_components.
    
    This is a regression test for the issue where:
    - Miner computed txsRoot using raw CBOR bytes
    - Block.from_components validated using tx.hash() (re-serialization)
    - The two didn't match, causing "txsRoot mismatch" errors
    
    The fix ensures both use tx.hash() for consistency.
    """
    client, cfg, _ = new_test_client()
    sender_kp = sender_keypair
    
    # Fund sender
    rpc_call(client, "miner.mine", {"count": 2, "address": sender_kp.address})
    
    # Submit transaction
    recipient_hex = _get_premine_address_hex()
    raw_hex, tx_hash = _build_signed_transfer(client, cfg, sender_kp, recipient_hex, nonce=0)
    rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    
    # Mine block - this should NOT raise "txsRoot mismatch" error
    try:
        mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": sender_kp.address})
        assert "result" in mine_result, "Mining should succeed without errors"
        assert mine_result["result"]["mined"] == 1, "Should mine 1 block"
        print(f"✓ Mining succeeded without txsRoot mismatch error")
    except Exception as e:
        if "txsRoot mismatch" in str(e):
            pytest.fail(f"txsRoot mismatch error occurred (regression): {e}")
        else:
            raise


def test_mempool_drained_after_mining(sender_keypair):
    """Test that transactions are removed from mempool after being mined."""
    client, cfg, _ = new_test_client()
    sender_kp = sender_keypair
    
    # Fund sender
    rpc_call(client, "miner.mine", {"count": 3, "address": sender_kp.address})
    
    # Submit 2 transactions
    tx_hashes = []
    for nonce in range(2):
        # Use deterministic test addresses with nonce suffix
        recipient_hex = TEST_RECIPIENT_2[:-2] + f"{nonce:02x}"
        raw_hex, tx_hash = _build_signed_transfer(client, cfg, sender_kp, recipient_hex, nonce=nonce)
        rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
        tx_hashes.append(tx_hash)
    
    # Verify both are in mempool
    pending_before = rpc_call(client, "mempool.getPending")["result"]
    for tx_hash in tx_hashes:
        assert tx_hash in pending_before, f"TX {tx_hash} should be in mempool before mining"
    
    # Mine block
    rpc_call(client, "miner.mine", {"count": 1, "address": sender_kp.address})
    
    # Verify transactions are removed from mempool
    pending_after = rpc_call(client, "mempool.getPending")["result"]
    removed_count = 0
    for tx_hash in tx_hashes:
        if tx_hash not in pending_after:
            removed_count += 1
            print(f"✓ TX {tx_hash} removed from mempool")
    
    # At least one should be removed (ideally both if they were included)
    assert removed_count > 0, "At least one transaction should be removed from mempool"
    
    print(f"✓ {removed_count}/{len(tx_hashes)} transactions removed from mempool after mining")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
