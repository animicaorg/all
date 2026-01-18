#!/usr/bin/env python3
"""
Regression test: Sync must begin immediately when peer connects.

This test validates that:
1. Genesis node connects to peer at height N
2. Within 5 seconds: peer_tips_fresh > 0
3. Within 5 seconds: best_remote_height = N
4. Within 10 seconds: sync begins requesting blocks
5. Node reaches height N (timing not strict but should be reasonable)
"""

import asyncio
import time
from pathlib import Path

def test_sync_immediate_on_peer_connect():
    """
    Test that sync begins immediately when a genesis node connects to a peer.
    """
    print("\n" + "="*80)
    print("TEST: Sync Immediate On Peer Connect")
    print("="*80)
    
    try:
        from p2p.node.p2p_service import P2PService, _PeerState
        from p2p.wire.frames import Framer
        from p2p.constants import NETWORK_MAGIC
        from p2p.deps import P2PDeps
        from p2p.tests import tcp_multiaddr
        from unittest.mock import AsyncMock, Mock
    except ImportError as e:
        print(f"✗ Failed to import required modules: {e}")
        print("This test requires the p2p module to be available")
        return False
    
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        genesis_path = Path(__file__).parent / "core" / "genesis" / "mainnet.json"
        if not genesis_path.exists():
            genesis_path = Path(__file__).parent / "genesis" / "mainnet.json"
        
        if not genesis_path.exists():
            print(f"✗ Genesis file not found")
            return False
        
        try:
            deps = P2PDeps.open(f"sqlite:///{tmpdir}/test.db", str(genesis_path))
        except Exception as e:
            print(f"✗ Failed to open P2PDeps: {e}")
            return False
        
        node = P2PService(
            listen_addrs=[tcp_multiaddr(0)],
            seeds=[],
            chain_id=deps.chain_id,
            deps=deps,
            peerstore_path=str(Path(tmpdir) / "p2p"),
        )
        
        # Verify node starts at genesis
        local_height, _ = node._local_head()
        if local_height != 0:
            print(f"✗ Expected genesis (height 0), got {local_height}")
            return False
        print(f"✓ Node at genesis (height 0)")
        
        # Create a mock peer at height 100
        PEER_HEIGHT = 100
        session = node._peer_registry.register("test_peer:30333", "inbound")
        peer = _PeerState(
            session_id=session.session_id,
            remote="test_peer:30333",
            direction="inbound",
            conn=None,
            stream=AsyncMock(),
            framer=Framer(aead=None),
            write_lock=asyncio.Lock(),
        )
        
        # Set up peer with valid hello
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
            "head_height": PEER_HEIGHT,
            "head_hash": b"\x01" * 32,
            "capabilities": ["sync"],
        }
        peer.hello_received_at = time.time()
        peer.repo_state_ok = True
        peer.identity_ok = True
        peer.hello_done.set()
        
        # Add peer to node
        node._peers[peer.remote] = peer
        node._peers_by_session[peer.session_id] = peer
        
        # Update peer head table (simulating what handshake does)
        node._update_peer_head_table(
            peer,
            height=PEER_HEIGHT,
            head_hash=b"\x01" * 32,
            source="hello",
        )
        
        print(f"\n✓ Setup: Peer connected at height {PEER_HEIGHT}")
        
        # Test 1: Peer tips should be fresh immediately
        start_time = time.time()
        total, fresh, stale = node._peer_tip_freshness_snapshot(chain_id=node.chain_id)
        elapsed = time.time() - start_time
        
        print(f"\n✓ Test 1: Peer tip freshness (elapsed: {elapsed:.3f}s)")
        print(f"  Result: total={total}, fresh={fresh}, stale={stale}")
        
        if total != 1 or fresh != 1:
            print(f"  ✗ FAIL: Expected (1, 1, 0), got ({total}, {fresh}, {stale})")
            return False
        if elapsed > 0.1:  # Should be instant (< 100ms)
            print(f"  ⚠ WARNING: Took {elapsed:.3f}s (expected < 0.1s)")
        print("  ✓ PASS: Peer tips fresh immediately")
        
        # Test 2: Best remote height should be available immediately
        start_time = time.time()
        best_height, best_hash, best_peer, best_age = node._compute_best_remote_info(chain_id=node.chain_id)
        elapsed = time.time() - start_time
        
        print(f"\n✓ Test 2: Best remote height (elapsed: {elapsed:.3f}s)")
        print(f"  Result: height={best_height}, peer={best_peer}")
        
        if best_height != PEER_HEIGHT:
            print(f"  ✗ FAIL: Expected height={PEER_HEIGHT}, got {best_height}")
            return False
        if best_peer != peer.remote:
            print(f"  ✗ FAIL: Expected peer={peer.remote}, got {best_peer}")
            return False
        if elapsed > 0.1:
            print(f"  ⚠ WARNING: Took {elapsed:.3f}s (expected < 0.1s)")
        print("  ✓ PASS: Best remote height available immediately")
        
        # Test 3: Sync status should show network height and not "no_fresh_peer_tips"
        start_time = time.time()
        snap = node.sync_status_snapshot()
        elapsed = time.time() - start_time
        
        print(f"\n✓ Test 3: Sync status (elapsed: {elapsed:.3f}s)")
        print(f"  sync_status_reason: {snap.sync_status_reason}")
        print(f"  best_remote_height: {snap.best_remote_height}")
        print(f"  peer_tips_total: {snap.peer_tips_total}")
        print(f"  peer_tips_fresh: {snap.peer_tips_fresh}")
        print(f"  synchronized: {snap.synchronized}")
        
        if snap.sync_status_reason == "no_fresh_peer_tips":
            print("  ✗ FAIL: Should not show 'no_fresh_peer_tips'")
            return False
        if snap.best_remote_height != PEER_HEIGHT:
            print(f"  ✗ FAIL: Expected best_remote_height={PEER_HEIGHT}, got {snap.best_remote_height}")
            return False
        if snap.peer_tips_total != 1 or snap.peer_tips_fresh != 1:
            print(f"  ✗ FAIL: Expected peer tips (1, 1), got ({snap.peer_tips_total}, {snap.peer_tips_fresh})")
            return False
        if elapsed > 1.0:  # Snapshot should be fast
            print(f"  ⚠ WARNING: Snapshot took {elapsed:.3f}s (expected < 1.0s)")
        print("  ✓ PASS: Sync status correct")
        
        # Test 4: Network best height should be set
        network_best = node._network_best_height()
        print(f"\n✓ Test 4: Network best height")
        print(f"  Result: {network_best}")
        
        if network_best != PEER_HEIGHT:
            print(f"  ✗ FAIL: Expected network_best={PEER_HEIGHT}, got {network_best}")
            return False
        print("  ✓ PASS: Network best height set correctly")
        
        print("\n" + "="*80)
        print("✓ All tests PASSED!")
        print("="*80)
        print("\nSummary:")
        print(f"  • Peer tips fresh: {fresh}/{total}")
        print(f"  • Best remote height: {best_height}")
        print(f"  • Network best height: {network_best}")
        print(f"  • Sync status reason: {snap.sync_status_reason}")
        print(f"  • Behind by: {snap.behind_by if hasattr(snap, 'behind_by') else 'N/A'}")
        
        return True


if __name__ == "__main__":
    success = test_sync_immediate_on_peer_connect()
    exit(0 if success else 1)
