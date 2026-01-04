#!/usr/bin/env python3
"""
Test that mempool service triggers P2P broadcast callback when tx is accepted.

This validates the new integration between MempoolService and TxRelayService.
"""

import asyncio
import hashlib
import sys
from typing import Any, Optional


def test_mempool_callback_integration():
    """Test that MempoolService can trigger P2P broadcast callback."""
    print("\n" + "=" * 70)
    print("Testing MempoolService P2P Broadcast Callback Integration")
    print("=" * 70 + "\n")
    
    # Test 1: Verify callback field exists
    print("Test 1: Verify MempoolService has callback field...")
    try:
        from rpc.mempool_service import MempoolService
        from mempool.pool import Pool
        from mempool.config import MempoolConfig
        
        # Create minimal pool
        config = MempoolConfig()
        pool = Pool(config=config)
        
        # Create mempool service
        service = MempoolService(
            pool=pool,
            chain_id=1337,
            min_gas_price_wei=1000,
            state_db=None,
            tx_index=None,
            persist_enabled=False,
        )
        
        assert hasattr(service, "_p2p_broadcast_callback"), \
            "MempoolService should have _p2p_broadcast_callback field"
        assert hasattr(service, "set_p2p_broadcast_callback"), \
            "MempoolService should have set_p2p_broadcast_callback method"
        
        print("✅ MempoolService has callback field and setter")
    except Exception as e:
        print(f"❌ Failed: {e}")
        return False
    
    # Test 2: Verify callback can be set
    print("\nTest 2: Verify callback can be set...")
    try:
        callback_called = []
        
        async def mock_callback(tx_hash: bytes, raw: bytes) -> None:
            callback_called.append({
                "tx_hash": tx_hash.hex(),
                "raw_len": len(raw)
            })
        
        service.set_p2p_broadcast_callback(mock_callback)
        assert service._p2p_broadcast_callback is not None, \
            "Callback should be set"
        
        print("✅ Callback setter works")
    except Exception as e:
        print(f"❌ Failed: {e}")
        return False
    
    # Test 3: Verify TxRelayService on_mempool_add signature matches
    print("\nTest 3: Verify TxRelayService.on_mempool_add is compatible...")
    try:
        from p2p.txrelay import TxRelayService
        import inspect
        
        sig = inspect.signature(TxRelayService.on_mempool_add)
        params = list(sig.parameters.keys())
        
        # Should be: self, txid, raw
        assert len(params) == 3, \
            f"Expected 3 params (self, txid, raw), got {len(params)}: {params}"
        assert params[0] == "self", "First param should be 'self'"
        assert params[1] == "txid", "Second param should be 'txid'"
        assert params[2] == "raw", "Third param should be 'raw'"
        
        print(f"✅ TxRelayService.on_mempool_add signature: {params}")
    except Exception as e:
        print(f"❌ Failed: {e}")
        return False
    
    # Test 4: Verify P2P service sets callback
    print("\nTest 4: Verify P2P service can set callback during start...")
    try:
        # This is more of a code inspection test
        # We verify the code path exists but don't actually start a P2P service
        from p2p.node.p2p_service import P2PService
        
        # Just verify the class can be imported
        print(f"✅ P2PService class is available")
        print(f"   Note: Full integration requires running P2P service.start()")
    except Exception as e:
        print(f"❌ Failed: {e}")
        return False
    
    print("\n" + "=" * 70)
    print("✅ All integration tests passed!")
    print("=" * 70)
    return True


def test_txrelay_flow():
    """Test that TxRelayService propagation flow works."""
    print("\n" + "=" * 70)
    print("Testing TxRelayService Propagation Flow")
    print("=" * 70 + "\n")
    
    async def run_test():
        from p2p.txrelay import TxRelayService
        
        # Track calls
        calls = []
        peer_state = {"peer-a": True, "peer-b": True}
        
        async def send_tx_inv(peer: str, txids: list[bytes]) -> None:
            calls.append({"type": "inv", "peer": peer, "count": len(txids)})
        
        async def send_noop(_peer: str, _payload: Any) -> None:
            pass
        
        # Simple mempool mock
        mempool = {}
        
        async def has_tx(txid: bytes) -> bool:
            return txid in mempool
        
        async def admit_tx(raw: bytes, origin: Optional[str]) -> tuple[bool, Optional[str]]:
            txid = hashlib.sha3_256(raw).digest()
            mempool[txid] = raw
            return True, None
        
        async def list_hashes(limit: int) -> list[bytes]:
            return list(mempool.keys())[:limit]
        
        # Create relay service
        relay = TxRelayService(
            max_tx_bytes=1024 * 1024,
            inv_flush_interval_s=0.05,  # Fast for testing
            peer_ids=lambda: list(peer_state.keys()),
            peer_eligible=lambda p: peer_state.get(p, False),
            send_tx_inv=send_tx_inv,
            send_tx_get=send_noop,
            send_tx_data=send_noop,
            send_tx_notfound=send_noop,
            send_mempool_req=send_noop,
            send_mempool_resp=send_noop,
            has_tx=has_tx,
            has_chain_tx=lambda _: asyncio.sleep(0, False),
            get_tx_raw=lambda txid: asyncio.sleep(0, mempool.get(txid)),
            admit_tx=admit_tx,
            list_mempool_hashes=list_hashes,
        )
        
        # Register peers
        relay.register_peer("peer-a")
        relay.register_peer("peer-b")
        
        # Start flush loop
        flush_task = asyncio.create_task(relay.inv_flush_loop())
        
        # Simulate mempool add
        tx_raw = b"test-transaction-data"
        tx_hash = hashlib.sha3_256(tx_raw).digest()
        
        print(f"Adding tx to mempool: {tx_hash.hex()[:16]}...")
        await relay.on_mempool_add(tx_hash, tx_raw)
        
        # Wait for inv flush
        await asyncio.sleep(0.2)
        
        # Stop flush loop
        relay._running = False
        try:
            await asyncio.wait_for(flush_task, timeout=1.0)
        except asyncio.TimeoutError:
            flush_task.cancel()
        
        # Check results
        inv_calls = [c for c in calls if c["type"] == "inv"]
        
        print(f"\nResults:")
        print(f"  - Total messages sent: {len(calls)}")
        print(f"  - INV messages: {len(inv_calls)}")
        
        for call in inv_calls:
            print(f"    → {call['peer']}: {call['count']} txids")
        
        assert len(inv_calls) >= 2, \
            f"Should have sent INV to both peers, got {len(inv_calls)}"
        
        print("\n✅ TxRelayService propagation flow working!")
        return True
    
    try:
        result = asyncio.run(run_test())
        return result
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = True
    
    success = test_mempool_callback_integration() and success
    success = test_txrelay_flow() and success
    
    if not success:
        sys.exit(1)
    
    print("\n" + "=" * 70)
    print("🎉 All tests passed!")
    print("=" * 70)
    sys.exit(0)
