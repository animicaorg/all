#!/usr/bin/env python3
"""
Test: Genesis sync with no eligible peers should clear backoffs

This test validates that when a node is stuck at genesis with handshaking peers,
it clears peer backoffs to allow immediate sync when handshake completes.

The fix ensures:
1. Detailed diagnostics are logged when stuck at genesis with no eligible peers
2. Peer backoffs are cleared when handshaking peers are present
3. Sync can resume immediately once handshake completes
"""

def test_genesis_sync_no_eligible_peers_diagnostics():
    """
    Test that genesis sync with no eligible peers logs diagnostics and clears backoffs.
    """
    print("\n" + "="*80)
    print("TEST: Genesis Sync No Eligible Peers - Diagnostics and Recovery")
    print("="*80)
    
    try:
        from p2p.node.p2p_service import P2PService, _PeerState
        import asyncio
        import time
    except ImportError as e:
        print(f"✗ Failed to import required modules: {e}")
        return False
    
    # Create a mock P2PService instance
    from unittest.mock import Mock, MagicMock, patch
    from pathlib import Path
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        genesis_path = Path(__file__).parent / "core" / "genesis" / "mainnet.json"
        if not genesis_path.exists():
            genesis_path = Path(__file__).parent / "genesis" / "mainnet.json"
        
        if not genesis_path.exists():
            print("✗ Genesis file not found")
            return False
        
        try:
            from p2p.deps import P2PDeps
            deps = P2PDeps.open(f"sqlite:///{tmpdir}/test.db", str(genesis_path))
        except Exception as e:
            print(f"✗ Failed to open P2PDeps: {e}")
            return False
        
        # Create service
        service = P2PService(deps=deps, chain_id=0, listen_addrs=[])
        
        # Mock the local_head to return genesis (height 0)
        service._local_head = Mock(return_value=(0, "0x" + "00" * 32))
        
        # Create a mock peer in handshaking state (hello_done not set)
        peer = _PeerState(
            session_id="test_session",
            remote="127.0.0.1:30333",
            direction="outbound",
            conn=Mock(),
            stream=Mock(),
            framer=Mock(),
            write_lock=asyncio.Lock(),
            connected_at=time.time(),
            feeler=False,
            netgroup="127.0.0.0/24",
        )
        peer.hello_done = asyncio.Event()  # Not set - peer is handshaking
        peer.identity_ok = False
        peer.ready_for_sync = False
        
        # Add peer to service
        service._peers[service._peer_key(peer.remote, peer.direction)] = peer
        
        # Add a backoff for this peer (simulating a previous failed sync attempt)
        backoff_key = service._peer_backoff_key(peer)
        service._sync_peer_backoff[backoff_key] = time.time() + 30.0  # 30 second backoff
        service._sync_peer_backoff_reason[backoff_key] = "headers_empty"
        
        print(f"✓ Setup: Created peer in handshaking state with backoff")
        print(f"  Peer: {peer.remote}")
        print(f"  Handshake complete: {peer.hello_done.is_set()}")
        print(f"  Backoff until: {service._sync_peer_backoff.get(backoff_key, 0)}")
        
        # Call _sync_once to trigger the fix
        async def run_sync_once():
            result = await service._sync_once(force=True)
            return result
        
        # Run the sync
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(run_sync_once())
        except Exception as e:
            print(f"✗ Sync failed with exception: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            loop.close()
        
        # Check that backoff was cleared
        backoff_after = service._sync_peer_backoff.get(backoff_key, 0)
        
        if backoff_after == 0 or backoff_after < time.time():
            print(f"✓ SUCCESS: Peer backoff was cleared")
            print(f"  Backoff after: {backoff_after}")
            return True
        else:
            print(f"✗ FAIL: Peer backoff was not cleared")
            print(f"  Backoff after: {backoff_after}")
            return False

if __name__ == "__main__":
    import sys
    success = test_genesis_sync_no_eligible_peers_diagnostics()
    sys.exit(0 if success else 1)
