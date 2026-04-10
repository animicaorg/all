"""
Test snapshot export with transactions containing PqSignatures.

This test verifies that blocks with transactions containing post-quantum
signatures can be successfully exported to snapshots without CBOR encoding errors.
"""

import tempfile
from pathlib import Path

import pytest

from core.db.block_db import BlockDB
from core.db.snapshot import export_snapshot
from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB
from core.types.block import Block
from core.types.header import Header
from core.types.receipt import Receipt
from core.types.tx import PqSignature, Tx, TxKind, TxTransfer, UnsignedTx
from core.utils.hash import sha3_256


def _zero_hash() -> bytes:
    """Return a zero 32-byte hash."""
    return b"\x00" * 32


def _dummy_hash() -> bytes:
    """Return a dummy 32-byte hash for testing."""
    return sha3_256(b"test")


def test_snapshot_export_with_pq_signature_tx():
    """
    Test that exporting a snapshot with blocks containing PQ-signed transactions works.
    
    Previously, this would fail with:
    EncodeError: unsupported type for canonical CBOR: PqSignature
    
    The fix ensures that blocks and headers are converted to dictionaries via .to_obj()
    before CBOR encoding, which properly serializes PqSignature objects.
    """
    # Create temporary directory for test databases
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        
        # Create in-memory SQLite KV stores for block and state DBs
        block_kv = SQLiteKV(":memory:")
        state_kv = SQLiteKV(":memory:")
        
        block_db = BlockDB(block_kv)
        state_db = StateDB(state_kv)
        
        # Create a block with a transaction containing a PqSignature
        # Build an unsigned transaction
        sender_addr = _dummy_hash()
        recipient_addr = sha3_256(b"recipient")
        
        unsigned_tx = UnsignedTx(
            version=1,
            chain_id=1,
            fork_id=None,
            valid_after=None,
            valid_until=None,
            salt=None,
            gas_price=1,
            gas_limit=21000,
            sender=sender_addr,
            kind=TxKind.TRANSFER,
            payload=TxTransfer(to=recipient_addr, amount=100, data=b""),
            access_list=(),
            nonce=0,
        )
        
        # Create a PqSignature (using dummy data)
        pq_sig = PqSignature(
            alg_id=1,  # Dilithium3
            pubkey=b"dummy_public_key_" + b"x" * 16,  # 32 bytes
            sig=b"dummy_signature_" + b"y" * 2000,  # ~2016 bytes (realistic for Dilithium3)
        )
        
        # Create signed transaction
        signed_tx = Tx(unsigned=unsigned_tx, sigs=(pq_sig,))
        
        # Compute transaction root for the header
        from core.types.block import compute_txs_root_from_txs
        txs_root = compute_txs_root_from_txs((signed_tx,))
        
        # Create genesis header with correct roots
        genesis_header = Header(
            v=1,
            chainId=1,
            height=0,
            parentHash=_zero_hash(),
            timestamp=1000000,
            stateRoot=_zero_hash(),
            txsRoot=txs_root,  # Use computed txs root
            receiptsRoot=_zero_hash(),
            proofsRoot=_zero_hash(),
            daRoot=_zero_hash(),
            mixSeed=_zero_hash(),
            poiesPolicyRoot=_zero_hash(),
            pqAlgPolicyRoot=_zero_hash(),
            thetaMicro=1000000,
            workType=0,
            nonce=0,
            extra=b"",
        )
        
        # Create genesis block with this transaction
        genesis_block = Block(
            header=genesis_header,
            txs=(signed_tx,),
            proofs=(),
            receipts=None,
        )
        
        # Store genesis block in the database
        genesis_hash = genesis_header.hash()
        block_db.put_header(genesis_header)
        block_db.put_block(genesis_block)
        block_db.set_canonical(0, genesis_hash)
        block_db.set_head(0, genesis_hash)
        block_db.set_chain_id(1)
        
        # Initialize state DB with minimal data
        state_db.kv.put(b"\x01account_key", b"account_data")
        
        # Create snapshot directory
        snapshot_dir = tmppath / "snapshot_test"
        snapshot_dir.mkdir()
        
        # This should NOT raise "unsupported type for canonical CBOR: PqSignature"
        # Prior to the fix, it would fail here
        manifest = export_snapshot(
            block_db=block_db,
            state_db=state_db,
            checkpoint_height=0,
            output_dir=snapshot_dir,
            compress=False,  # Don't compress for simpler debugging
        )
        
        # Verify the snapshot was created successfully
        assert manifest is not None
        assert manifest.checkpoint_height == 0
        assert manifest.blocks_count == 1
        assert manifest.headers_count == 1
        assert manifest.chain_id == 1
        
        # Verify snapshot files exist
        blocks_file = snapshot_dir / "blocks.cbor"
        state_file = snapshot_dir / "state.cbor"
        manifest_file = snapshot_dir / "manifest.json"
        
        assert blocks_file.exists(), "Blocks file should exist"
        assert state_file.exists(), "State file should exist"
        assert manifest_file.exists(), "Manifest file should exist"
        
        # Verify blocks file is not empty
        assert blocks_file.stat().st_size > 0, "Blocks file should not be empty"
        
        # Verify we can read the blocks file back
        from core.encoding.cbor import cbor_loads
        
        with open(blocks_file, "rb") as f:
            content = f.read()
            # Split by newline delimiter
            entries = content.split(b"\n")
            # Filter out empty entries
            entries = [e for e in entries if e]
            
            assert len(entries) == 2, "Should have 1 header + 1 block"
            
            # Decode first entry (header)
            header_entry = cbor_loads(entries[0])
            assert header_entry["type"] == "header"
            assert header_entry["height"] == 0
            assert "data" in header_entry
            
            # Decode second entry (block)
            block_entry = cbor_loads(entries[1])
            assert block_entry["type"] == "block"
            assert block_entry["height"] == 0
            assert "data" in block_entry
            
            # Verify the block data contains transaction data
            block_data = block_entry["data"]
            assert "txs" in block_data
            assert len(block_data["txs"]) == 1
            
            # Verify the transaction has signatures
            tx_data = block_data["txs"][0]
            assert "sigs" in tx_data
            assert len(tx_data["sigs"]) == 1
            
            # Verify the signature is properly serialized as a dict
            sig_data = tx_data["sigs"][0]
            assert isinstance(sig_data, dict), "Signature should be a dict, not a PqSignature object"
            assert "alg" in sig_data
            assert "pubkey" in sig_data
            assert "sig" in sig_data
            assert sig_data["alg"] == 1  # Dilithium3


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
