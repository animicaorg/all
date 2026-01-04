"""Integration test for PTL replication acknowledgment tracking."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path

import pytest

from core.ptl.model import TxStatus
from core.ptl.service import PtlService
from core.ptl.store import PtlStore
from core.utils.hash import sha3_256
from rpc import deps
from rpc.methods.ptl import ptl_replication_status


@pytest.fixture
async def ptl_service_fixture():
    """Create a temporary PTL service."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "ptl.db"
        store = PtlStore(db_path)
        service = PtlService(store, ttl_seconds=3600, min_peer_acks=2)
        
        # Register in deps
        deps.register("ptl_service", service)
        
        yield service
        
        # Clean up
        service.stop()
        store.close()
        deps.register("ptl_service", None)


@pytest.mark.asyncio
async def test_replication_status_rpc_with_acks(ptl_service_fixture):
    """Test that replicationStatus RPC returns proper ack counts."""
    service = ptl_service_fixture
    
    # Submit a transaction
    tx_bytes = b"test transaction with acks"
    txid, entry = await service.submit(tx_bytes, origin="rpc_test")
    txid_hex = "0x" + txid.hex()
    
    # Initially no acks
    result = await ptl_replication_status({"txid": txid_hex})
    assert result is not None
    assert result["tx_hash"] == txid_hex
    assert result["local_status"] == "seen"
    assert result["quorum"]["observed_acks"] == 0
    assert result["quorum"]["required_acks"] == 2
    assert result["quorum"]["quorum_met"] is False
    
    # Add first peer ack
    await service.add_receipt(txid, "peer1", "ack")
    
    result = await ptl_replication_status({"txid": txid_hex})
    assert result["quorum"]["observed_acks"] == 1
    assert result["quorum"]["quorum_met"] is False
    assert len(result["peers"]) == 1
    assert result["peers"][0]["peer_id"] == "peer1"
    assert result["peers"][0]["status"] == "ack"
    
    # Add second peer ack (should reach quorum)
    await service.add_receipt(txid, "peer2", "ack")
    
    result = await ptl_replication_status({"txid": txid_hex})
    assert result["quorum"]["observed_acks"] == 2
    assert result["quorum"]["quorum_met"] is True
    assert len(result["peers"]) == 2
    
    # Check that status upgraded to ATTESTED
    entry = await service.get(txid)
    assert entry.status == TxStatus.ATTESTED


@pytest.mark.asyncio
async def test_replication_status_with_reject(ptl_service_fixture):
    """Test that replicationStatus handles reject receipts."""
    service = ptl_service_fixture
    
    # Submit a transaction
    tx_bytes = b"test transaction with reject"
    txid, entry = await service.submit(tx_bytes, origin="rpc_test")
    txid_hex = "0x" + txid.hex()
    
    # Add an ack
    await service.add_receipt(txid, "peer1", "ack")
    
    # Add a reject
    await service.add_receipt(txid, "peer2", "reject", reason="invalid signature")
    
    result = await ptl_replication_status({"txid": txid_hex})
    assert result["quorum"]["observed_acks"] == 1  # Only acks count
    assert len(result["peers"]) == 2
    
    # Find the reject receipt
    reject_receipt = next(p for p in result["peers"] if p["status"] == "reject")
    assert reject_receipt["peer_id"] == "peer2"
    assert reject_receipt["reason"] == "invalid signature"


@pytest.mark.asyncio
async def test_replication_status_persistence(ptl_service_fixture):
    """Test that receipts are persisted across service restarts."""
    service = ptl_service_fixture
    
    # Submit a transaction and add receipts
    tx_bytes = b"test transaction for persistence"
    txid, _ = await service.submit(tx_bytes, origin="rpc_test")
    txid_hex = "0x" + txid.hex()
    
    await service.add_receipt(txid, "peer1", "ack")
    await service.add_receipt(txid, "peer2", "ack")
    
    # Verify receipts are stored
    db_path = service.store.db_path
    service.store.close()
    
    # Create new service with same database
    new_store = PtlStore(db_path)
    new_service = PtlService(new_store, ttl_seconds=3600, min_peer_acks=2)
    deps.register("ptl_service", new_service)
    
    # Check that receipts persisted
    result = await ptl_replication_status({"txid": txid_hex})
    assert result["quorum"]["observed_acks"] == 2
    assert len(result["peers"]) == 2
    assert result["persistence"]["stored_receipts_count"] == 2
    
    # Clean up
    new_store.close()


@pytest.mark.asyncio
async def test_replication_status_unknown_transaction(ptl_service_fixture):
    """Test replicationStatus for unknown transaction."""
    service = ptl_service_fixture
    
    # Query unknown transaction
    unknown_txid = "0x" + ("00" * 32)
    result = await ptl_replication_status({"txid": unknown_txid})
    
    assert result is not None
    assert result["local_status"] == "unknown"
    assert result["quorum"]["observed_acks"] == 0
    assert len(result["peers"]) == 0


@pytest.mark.asyncio
async def test_replication_status_json_serializable(ptl_service_fixture):
    """Test that replicationStatus result is JSON-serializable."""
    service = ptl_service_fixture
    
    # Submit a transaction with full metadata
    tx_bytes = b"test transaction for json"
    txid, entry = await service.submit(tx_bytes, origin="rpc_test")
    txid_hex = "0x" + txid.hex()
    
    await service.add_receipt(txid, "peer1", "ack")
    await service.mark_included(txid, height=100)
    await service.mark_finalized(txid, height=105)
    
    result = await ptl_replication_status({"txid": txid_hex})
    
    # Should be JSON-serializable
    json_str = json.dumps(result)
    parsed = json.loads(json_str)
    
    assert parsed["tx_hash"] == txid_hex
    assert parsed["local_status"] == "mined"
    assert parsed["mined"]["block_height"] == 100
    assert parsed["mined"]["finalized_height"] == 105


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
