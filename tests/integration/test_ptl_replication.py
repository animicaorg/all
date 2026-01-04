"""Integration tests for PTL replication between nodes."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import pytest

from core.ptl.model import TxStatus
from core.ptl.service import PtlService
from core.ptl.store import PtlStore
from p2p.ptl_relay import PtlRelayService


class MockP2PNetwork:
    """Mock P2P network for testing PTL relay."""
    
    def __init__(self):
        self.nodes = {}
        self.message_log = []
    
    def add_node(self, node_id: str, ptl_service: PtlService):
        """Add a node to the network."""
        self.nodes[node_id] = {
            "ptl_service": ptl_service,
            "relay": None,
            "peers": [],
        }
    
    def connect_peers(self, node1_id: str, node2_id: str):
        """Connect two nodes as peers."""
        if node1_id not in self.nodes[node2_id]["peers"]:
            self.nodes[node2_id]["peers"].append(node1_id)
        if node2_id not in self.nodes[node1_id]["peers"]:
            self.nodes[node1_id]["peers"].append(node2_id)
    
    def create_relay(self, node_id: str):
        """Create PTL relay for a node."""
        node = self.nodes[node_id]
        
        def peer_ids():
            return node["peers"]
        
        def peer_eligible(peer_id):
            return peer_id in node["peers"]
        
        async def send_announce(peer_id, txids):
            self.message_log.append({
                "type": "announce",
                "from": node_id,
                "to": peer_id,
                "txids": txids,
            })
            # Deliver message to peer
            peer_node = self.nodes[peer_id]
            if peer_node["relay"]:
                await peer_node["relay"].on_ptl_announce(node_id, txids)
        
        async def send_want(peer_id, txids):
            self.message_log.append({
                "type": "want",
                "from": node_id,
                "to": peer_id,
                "txids": txids,
            })
            peer_node = self.nodes[peer_id]
            if peer_node["relay"]:
                await peer_node["relay"].on_ptl_want(node_id, txids)
        
        async def send_push(peer_id, items):
            self.message_log.append({
                "type": "push",
                "from": node_id,
                "to": peer_id,
                "count": len(items),
            })
            peer_node = self.nodes[peer_id]
            if peer_node["relay"]:
                await peer_node["relay"].on_ptl_push(node_id, items)
        
        async def send_ack(peer_id, data):
            self.message_log.append({
                "type": "ack",
                "from": node_id,
                "to": peer_id,
                "status": data.get("status"),
                "count": len(data.get("txids", [])),
            })
            peer_node = self.nodes[peer_id]
            if peer_node["relay"]:
                await peer_node["relay"].on_ptl_ack(node_id, data)
        
        relay = PtlRelayService(
            node["ptl_service"],
            reconcile_interval_s=0.5,  # Fast for testing
            peer_ids=peer_ids,
            peer_eligible=peer_eligible,
            send_announce=send_announce,
            send_want=send_want,
            send_push=send_push,
            send_ack=send_ack,
        )
        
        node["relay"] = relay
        return relay


@pytest.fixture
def mock_network():
    """Create a mock P2P network."""
    return MockP2PNetwork()


@pytest.fixture
def node_a(mock_network):
    """Create node A."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "ptl_a.db"
        store = PtlStore(db_path)
        service = PtlService(store, ttl_seconds=3600, min_peer_acks=1)
        mock_network.add_node("node_a", service)
        relay = mock_network.create_relay("node_a")
        yield {"service": service, "relay": relay, "id": "node_a"}
        store.close()


@pytest.fixture
def node_b(mock_network):
    """Create node B."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "ptl_b.db"
        store = PtlStore(db_path)
        service = PtlService(store, ttl_seconds=3600, min_peer_acks=1)
        mock_network.add_node("node_b", service)
        relay = mock_network.create_relay("node_b")
        yield {"service": service, "relay": relay, "id": "node_b"}
        store.close()


@pytest.mark.asyncio
async def test_ptl_two_node_replication(mock_network, node_a, node_b):
    """Test transaction replication between two nodes within 3 seconds."""
    # Connect the nodes
    mock_network.connect_peers("node_a", "node_b")
    
    # Register peers in relay services
    node_a["relay"].register_peer("node_b", peer_node_id="node_b")
    node_b["relay"].register_peer("node_a", peer_node_id="node_a")
    
    # Submit transaction on node A
    tx_bytes = b"test transaction for replication"
    start_time = time.time()
    txid, entry = await node_a["service"].submit(tx_bytes, origin="node_a")
    
    # Announce to peers
    await node_a["relay"].announce_new_transaction(txid)
    
    # Wait for replication (should be quick)
    max_wait = 3.0
    replicated = False
    
    while time.time() - start_time < max_wait:
        # Check if node B has the transaction
        entry_b = await node_b["service"].get(txid)
        if entry_b is not None:
            replicated = True
            elapsed = time.time() - start_time
            break
        await asyncio.sleep(0.1)
    
    # Verify replication happened
    assert replicated, "Transaction should replicate within 3 seconds"
    assert elapsed < 3.0, f"Replication took {elapsed:.2f}s, expected < 3s"
    
    # Verify transaction exists on node B
    entry_b = await node_b["service"].get(txid)
    assert entry_b is not None
    assert entry_b.txid == txid
    assert entry_b.tx_bytes == tx_bytes
    
    # Check message log
    announces = [m for m in mock_network.message_log if m["type"] == "announce"]
    wants = [m for m in mock_network.message_log if m["type"] == "want"]
    pushes = [m for m in mock_network.message_log if m["type"] == "push"]
    acks = [m for m in mock_network.message_log if m["type"] == "ack"]
    
    assert len(announces) >= 1, "Should have at least one announce"
    assert len(wants) >= 1, "Should have at least one want"
    assert len(pushes) >= 1, "Should have at least one push"
    assert len(acks) >= 1, "Should have at least one ack"


@pytest.mark.asyncio
async def test_ptl_anti_entropy_reconciliation(mock_network, node_a, node_b):
    """Test anti-entropy reconciliation after disconnect/reconnect."""
    # Connect the nodes
    mock_network.connect_peers("node_a", "node_b")
    node_a["relay"].register_peer("node_b", peer_node_id="node_b")
    node_b["relay"].register_peer("node_a", peer_node_id="node_a")
    
    # Submit transaction on node A while "disconnected"
    mock_network.nodes["node_a"]["peers"] = []  # Simulate disconnect
    
    tx_bytes = b"transaction during disconnect"
    txid, _ = await node_a["service"].submit(tx_bytes, origin="node_a")
    
    # Verify node B doesn't have it yet
    entry_b = await node_b["service"].get(txid)
    assert entry_b is None
    
    # Reconnect
    mock_network.connect_peers("node_a", "node_b")
    
    # Trigger reconciliation
    await node_a["relay"]._reconcile_with_peer("node_b")
    
    # Wait for anti-entropy to pull the transaction
    max_wait = 30.0
    start_time = time.time()
    replicated = False
    
    while time.time() - start_time < max_wait:
        entry_b = await node_b["service"].get(txid)
        if entry_b is not None:
            replicated = True
            elapsed = time.time() - start_time
            break
        await asyncio.sleep(0.5)
    
    # Verify reconciliation happened within 30 seconds
    assert replicated, "Anti-entropy should reconcile within 30 seconds"
    assert elapsed < 30.0, f"Reconciliation took {elapsed:.2f}s, expected < 30s"
    
    # Verify transaction
    entry_b = await node_b["service"].get(txid)
    assert entry_b.txid == txid
    assert entry_b.tx_bytes == tx_bytes


@pytest.mark.asyncio
async def test_ptl_invalid_transaction_rejection(mock_network, node_a, node_b):
    """Test that invalid transactions are rejected with reason."""
    # Connect the nodes
    mock_network.connect_peers("node_a", "node_b")
    node_a["relay"].register_peer("node_b", peer_node_id="node_b")
    node_b["relay"].register_peer("node_a", peer_node_id="node_a")
    
    # Submit a transaction and mark it as rejected on node A
    tx_bytes = b"invalid transaction"
    txid, _ = await node_a["service"].submit(tx_bytes, origin="node_a")
    await node_a["service"].mark_rejected(txid, reason="invalid signature")
    
    # Get entry on node A
    entry_a = await node_a["service"].get(txid)
    assert entry_a.status == TxStatus.REJECTED
    assert entry_a.reject_reason == "invalid signature"
    
    # Add a reject receipt
    await node_a["service"].add_receipt(txid, "node_b", "reject", reason="invalid signature")
    
    # Verify receipt
    status = await node_a["service"].get_replication_status(txid)
    assert status is not None
    
    receipts = status["receipts"]
    assert len(receipts) >= 1
    
    reject_receipt = next((r for r in receipts if r["status"] == "reject"), None)
    assert reject_receipt is not None
    assert reject_receipt["reason"] == "invalid signature"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
