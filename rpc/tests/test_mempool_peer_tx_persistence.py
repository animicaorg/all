"""
Test that transactions from peers are persisted to pending.jsonl
"""
from pathlib import Path

import pytest

from core.types.tx import Tx
from rpc.mempool_service import MempoolService


@pytest.fixture(autouse=True)
def _mempool_persistence_test_env(monkeypatch):
    """Scope these persistence tests to their original intent.

    These tests verify mempool PERSISTENCE and REPLAY of (intentionally
    unsigned) fixture transactions, not the ANM-H10 in-mempool signature
    admission policy (which is a mainnet security invariant covered by
    dedicated tests). The production code exposes ``ANIMICA_MEMPOOL_VERIFY_SIGS``
    precisely as an emergency/test kill-switch for that invariant, so we opt the
    unsigned fixture txs out of signature verification here without weakening any
    production/security code. All other admission checks (fee floor, gas bounds,
    funded-sender) still pass: the fixture offers a 1 gwei fee, uses exactly the
    intrinsic gas limit, and runs with ``state_db=None``.
    """
    monkeypatch.setenv("ANIMICA_MEMPOOL_VERIFY_SIGS", "0")


def _build_transfer_tx(nonce: int = 0) -> tuple[Tx, bytes]:
    sender = b"\x11" * 32
    to = b"\x22" * 32
    tx = Tx.transfer(
        chain_id=1,
        nonce=nonce,
        gas_price=1_000_000_000,  # 1 gwei - high enough to pass fee floor
        gas_limit=21_000,
        sender=sender,
        to=to,
        amount=1,
        version=1,
    )
    return tx, tx.to_cbor()


def test_peer_tx_persists_to_pending_jsonl(tmp_path):
    """Test that transactions from peers are persisted to pending.jsonl"""
    # Create mempool service with persistence enabled
    service = MempoolService.create(
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
    )
    
    # Submit a transaction as if it came from a peer (local=False)
    tx, raw = _build_transfer_tx(nonce=0)
    tx_hash = service.submit(tx=tx, raw=raw, local=False, origin_peer="peer123")
    
    # Verify the persist file exists
    persist_path = tmp_path / "mempool" / "pending.jsonl"
    assert persist_path.exists(), f"Expected {persist_path} to exist"
    
    # Verify the transaction is in the persist file
    content = persist_path.read_text(encoding="utf-8")
    assert tx_hash.replace("0x", "") in content, f"Expected {tx_hash} in {persist_path}"
    
    # Verify we can restore from the file
    restored = MempoolService.create(
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
    )
    assert restored.has_hash(tx_hash), f"Expected restored service to have {tx_hash}"
    assert restored.get_raw(tx_hash) == raw


def test_multiple_peer_txs_persisted(tmp_path):
    """Test that multiple transactions from peers are all persisted"""
    service = MempoolService.create(
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
    )
    
    # Submit multiple transactions from different peers
    tx_hashes = []
    for i in range(3):
        tx, raw = _build_transfer_tx(nonce=i)
        tx_hash = service.submit(tx=tx, raw=raw, local=False, origin_peer=f"peer{i}")
        tx_hashes.append(tx_hash)
    
    # Verify all are persisted
    persist_path = tmp_path / "mempool" / "pending.jsonl"
    content = persist_path.read_text(encoding="utf-8")
    
    for tx_hash in tx_hashes:
        assert tx_hash.replace("0x", "") in content, f"Expected {tx_hash} in persist file"
    
    # Verify restore gets all of them
    restored = MempoolService.create(
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
    )
    
    for tx_hash in tx_hashes:
        assert restored.has_hash(tx_hash), f"Expected restored service to have {tx_hash}"


def test_local_and_peer_txs_both_persisted(tmp_path):
    """Test that both local and peer transactions are persisted"""
    service = MempoolService.create(
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
    )
    
    # Submit a local transaction
    tx_local, raw_local = _build_transfer_tx(nonce=0)
    tx_hash_local = service.submit(tx=tx_local, raw=raw_local, local=True)
    
    # Submit a peer transaction
    tx_peer, raw_peer = _build_transfer_tx(nonce=1)
    tx_hash_peer = service.submit(tx=tx_peer, raw=raw_peer, local=False, origin_peer="peer456")
    
    # Verify both are persisted
    persist_path = tmp_path / "mempool" / "pending.jsonl"
    content = persist_path.read_text(encoding="utf-8")
    
    assert tx_hash_local.replace("0x", "") in content
    assert tx_hash_peer.replace("0x", "") in content
    
    # Verify restore gets both
    restored = MempoolService.create(
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
    )
    
    assert restored.has_hash(tx_hash_local)
    assert restored.has_hash(tx_hash_peer)
