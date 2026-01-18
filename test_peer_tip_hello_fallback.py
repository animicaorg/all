#!/usr/bin/env python3
"""
Test that peer tip tracking uses hello fallback when tip tracker has no entry.

This addresses the issue where nodes get stuck at genesis with "no_fresh_peer_tips"
despite having connected peers with valid chain_id.
"""
import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock

# This test validates the fix for the sync issue where nodes show:
# - peer_tips_total: 0
# - peer_tips_fresh: 0
# - sync_status_reason: 'no_fresh_peer_tips'
# Despite having connected peers.


def test_peer_tip_freshness_with_hello_fallback():
    """
    Test that _peer_tip_freshness_snapshot uses hello age as fallback
    when tip tracker has no entry.
    """
    print("\n" + "="*80)
    print("Testing peer tip freshness with hello fallback")
    print("="*80)
    
    try:
        from p2p.node.p2p_service import P2PService, _PeerState
        from p2p.wire.frames import Framer
        from p2p.constants import NETWORK_MAGIC
        from p2p.deps import P2PDeps
        from p2p.tests import tcp_multiaddr
    except ImportError as e:
        print(f"✗ Failed to import required modules: {e}")
        print("This test requires the p2p module to be available")
        return False
    
    # Create a temporary node
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        genesis_path = Path(__file__).parent / "core" / "genesis" / "mainnet.json"
        if not genesis_path.exists():
            genesis_path = Path(__file__).parent / "genesis" / "mainnet.json"
        
        if not genesis_path.exists():
            print(f"✗ Genesis file not found at {genesis_path}")
            return False
        
        deps = P2PDeps.open(f"sqlite:///{tmpdir}/test.db", str(genesis_path))
        node = P2PService(
            listen_addrs=[tcp_multiaddr(0)],
            seeds=[],
            chain_id=deps.chain_id,
            deps=deps,
            peerstore_path=str(Path(tmpdir) / "p2p"),
        )
        
        # Create a mock peer with hello but NO tip tracker entry
        session = node._peer_registry.register("test_peer:0", "inbound")
        peer = _PeerState(
            session_id=session.session_id,
            remote="test_peer:0",
            direction="inbound",
            conn=None,
            stream=AsyncMock(),
            framer=Framer(aead=None),
            write_lock=asyncio.Lock(),
        )
        
        # Set peer state
        peer.hello = {
            "chain_id": node.chain_id,
            "network_magic": NETWORK_MAGIC,
            "genesis_header_hash": node._genesis_header_hash(),
            "genesis_block_hash": node._genesis_block_hash(),
            "genesis_hash": node._genesis_header_hash(),
            "fork_id": node._fork_id(),
            "consensus_id": node._consensus_id(),
            "protocol_version": node._protocol_version(),
            "genesis_identity": node._genesis_identity(),
            "network_params_hash": node._network_params_hash(),
            "head_height": 10,  # Peer is at height 10
            "head_hash": b"\x01" * 32,
            "capabilities": ["sync"],
        }
        peer.hello_received_at = time.time()  # Fresh hello
        peer.repo_state_ok = True
        peer.identity_ok = True
        peer.hello_done.set()
        
        # Add peer to node
        node._peers[peer.remote] = peer
        node._peers_by_session[peer.session_id] = peer
        
        # Verify tip tracker has NO entry
        assert node._peer_tip_tracker.get(peer.remote) is None, \
            "Tip tracker should not have entry initially"
        
        print(f"\n✓ Setup: Peer at height 10, hello_received_at={peer.hello_received_at}")
        print(f"  Tip tracker entry: {node._peer_tip_tracker.get(peer.remote)}")
        
        # Test 1: _peer_tip_freshness_snapshot should use hello fallback
        total, fresh, stale = node._peer_tip_freshness_snapshot(chain_id=node.chain_id)
        
        print(f"\n✓ Test 1: _peer_tip_freshness_snapshot")
        print(f"  Result: total={total}, fresh={fresh}, stale={stale}")
        
        if total == 1 and fresh == 1 and stale == 0:
            print("  ✓ PASS: Peer counted as fresh using hello fallback")
        else:
            print(f"  ✗ FAIL: Expected (1, 1, 0), got ({total}, {fresh}, {stale})")
            return False
        
        # Test 2: _compute_best_remote_info should use hello fallback
        best_height, best_hash, best_peer, best_age = node._compute_best_remote_info(chain_id=node.chain_id)
        
        print(f"\n✓ Test 2: _compute_best_remote_info")
        print(f"  Result: height={best_height}, hash={best_hash}, peer={best_peer}, age={best_age}")
        
        if best_height == 10 and best_peer == peer.remote:
            print("  ✓ PASS: Best remote info uses hello fallback")
        else:
            print(f"  ✗ FAIL: Expected height=10, peer={peer.remote}, got height={best_height}, peer={best_peer}")
            return False
        
        # Test 3: sync_status_snapshot should NOT show "no_fresh_peer_tips"
        snap = node.sync_status_snapshot()
        
        print(f"\n✓ Test 3: sync_status_snapshot")
        print(f"  sync_status_reason: {snap.sync_status_reason}")
        print(f"  best_remote_height: {snap.best_remote_height}")
        print(f"  peer_tips_total: {snap.peer_tips_total}")
        print(f"  peer_tips_fresh: {snap.peer_tips_fresh}")
        
        if snap.sync_status_reason != "no_fresh_peer_tips":
            print("  ✓ PASS: Sync status does not show 'no_fresh_peer_tips'")
        else:
            print("  ✗ FAIL: Sync status should not show 'no_fresh_peer_tips' with fresh peer")
            return False
        
        if snap.best_remote_height == 10:
            print("  ✓ PASS: Best remote height is 10")
        else:
            print(f"  ✗ FAIL: Expected best_remote_height=10, got {snap.best_remote_height}")
            return False
        
        print("\n" + "="*80)
        print("✓ All tests PASSED!")
        print("="*80)
        return True


if __name__ == "__main__":
    success = test_peer_tip_freshness_with_hello_fallback()
    exit(0 if success else 1)
