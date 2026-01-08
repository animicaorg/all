"""
Test snapshot export and import with PqSignature transactions.
"""

import tempfile
from pathlib import Path

import pytest

from core.db.block_db import BlockDB
from core.db.snapshot import export_snapshot, import_snapshot
from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB
from core.types.block import Block
from core.types.header import Header
from core.types.tx import PqSignature, Tx, TxKind, TxTransfer, UnsignedTx
from core.utils.hash import sha3_256


def _zero_hash() -> bytes:
    return b"\x00" * 32


def _dummy_hash() -> bytes:
    return sha3_256(b"test")


def test_snapshot_export_import_roundtrip_with_pq_signatures():
    """
    Test that snapshots can be exported and imported with PQ-signed transactions.
    This verifies the complete roundtrip works correctly.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        
        # Create databases for export
        export_block_kv = SQLiteKV(":memory:")
        export_state_kv = SQLiteKV(":memory:")
        export_block_db = BlockDB(export_block_kv)
        export_state_db = StateDB(export_state_kv)
        
        # Create a transaction with PqSignature
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
        
        pq_sig = PqSignature(
            alg_id=1,
            pubkey=b"dummy_public_key_" + b"x" * 16,
            sig=b"dummy_signature_" + b"y" * 2000,
        )
        
        signed_tx = Tx(unsigned=unsigned_tx, sigs=(pq_sig,))
        
        # Create header with correct txs root
        from core.types.block import compute_txs_root_from_txs
        txs_root = compute_txs_root_from_txs((signed_tx,))
        
        genesis_header = Header(
            v=1,
            chainId=1,
            height=0,
            parentHash=_zero_hash(),
            timestamp=1000000,
            stateRoot=_zero_hash(),
            txsRoot=txs_root,
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
        
        genesis_block = Block(
            header=genesis_header,
            txs=(signed_tx,),
            proofs=(),
            receipts=None,
        )
        
        # Store in export DB
        genesis_hash = genesis_header.hash()
        export_block_db.put_header(genesis_header)
        export_block_db.put_block(genesis_block)
        export_block_db.set_canonical(0, genesis_hash)
        export_block_db.set_head(0, genesis_hash)
        export_block_db.set_chain_id(1)
        export_state_db.kv.put(b"\x01account_key", b"account_data")
        
        # Export snapshot
        snapshot_dir = tmppath / "snapshot_test"
        snapshot_dir.mkdir()
        
        manifest = export_snapshot(
            block_db=export_block_db,
            state_db=export_state_db,
            checkpoint_height=0,
            output_dir=snapshot_dir,
            compress=False,
        )
        
        assert manifest is not None
        assert manifest.blocks_count == 1
        assert manifest.headers_count == 1
        
        # Create new databases for import
        import_block_kv = SQLiteKV(":memory:")
        import_state_kv = SQLiteKV(":memory:")
        import_block_db = BlockDB(import_block_kv)
        import_state_db = StateDB(import_state_kv)
        
        # Import snapshot
        imported_manifest = import_snapshot(
            block_db=import_block_db,
            state_db=import_state_db,
            snapshot_dir=snapshot_dir,
            verify_hashes=True,
        )
        
        assert imported_manifest.checkpoint_height == 0
        
        # Verify imported data
        imported_block = import_block_db.get_block_by_height(0)
        assert imported_block is not None
        assert len(imported_block.txs) == 1
        
        # Verify the transaction has PQ signature
        imported_tx = imported_block.txs[0]
        assert len(imported_tx.sigs) == 1
        imported_sig = imported_tx.sigs[0]
        assert isinstance(imported_sig, PqSignature)
        assert imported_sig.alg_id == 1
        assert imported_sig.pubkey == pq_sig.pubkey
        assert imported_sig.sig == pq_sig.sig
        
        # Verify block hash matches
        assert imported_block.header.hash() == genesis_hash


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
