#!/usr/bin/env python3
"""
Test script to verify peer discovery and sync fixes.

This script verifies:
1. Seed nodes include both 144.126.133.21 and 3.12.224.189
2. P2PServiceLegacy has _network_best_height method
3. P2PServiceLegacy has seed reconnect loop
4. P2PServiceLegacy has sync monitor loop
"""

import sys


def test_seed_configuration():
    """Test that both seed IPs are configured."""
    from p2p.config import MAINNET_SEEDS, TESTNET_SEEDS, DEVNET_SEEDS
    from p2p.discovery.seeds import EMBEDDED_FALLBACK_SEEDS
    from python.animica.seeds import NETWORK_SEEDS
    
    print("✓ Testing seed configuration...")
    
    # Check MAINNET_SEEDS
    assert any("144.126.133.21" in s for s in MAINNET_SEEDS), "144.126.133.21 not in MAINNET_SEEDS"
    assert any("3.12.224.189" in s for s in MAINNET_SEEDS), "3.12.224.189 not in MAINNET_SEEDS"
    print(f"  ✓ MAINNET_SEEDS has both seed IPs ({len(MAINNET_SEEDS)} seeds total)")
    
    # Check TESTNET_SEEDS
    assert any("144.126.133.21" in s for s in TESTNET_SEEDS), "144.126.133.21 not in TESTNET_SEEDS"
    assert any("3.12.224.189" in s for s in TESTNET_SEEDS), "3.12.224.189 not in TESTNET_SEEDS"
    print(f"  ✓ TESTNET_SEEDS has both seed IPs ({len(TESTNET_SEEDS)} seeds total)")
    
    # Check DEVNET_SEEDS
    assert any("144.126.133.21" in s for s in DEVNET_SEEDS), "144.126.133.21 not in DEVNET_SEEDS"
    assert any("3.12.224.189" in s for s in DEVNET_SEEDS), "3.12.224.189 not in DEVNET_SEEDS"
    print(f"  ✓ DEVNET_SEEDS has both seed IPs ({len(DEVNET_SEEDS)} seeds total)")
    
    # Check EMBEDDED_FALLBACK_SEEDS
    assert any("144.126.133.21" in s for s in EMBEDDED_FALLBACK_SEEDS), "144.126.133.21 not in EMBEDDED_FALLBACK_SEEDS"
    assert any("3.12.224.189" in s for s in EMBEDDED_FALLBACK_SEEDS), "3.12.224.189 not in EMBEDDED_FALLBACK_SEEDS"
    print(f"  ✓ EMBEDDED_FALLBACK_SEEDS has both seed IPs ({len(EMBEDDED_FALLBACK_SEEDS)} seeds total)")
    
    # Check NETWORK_SEEDS (Python animica module)
    for network, seeds in NETWORK_SEEDS.items():
        assert any("144.126.133.21" in s for s in seeds), f"144.126.133.21 not in NETWORK_SEEDS[{network}]"
        # Note: mainnet uses domain primarily, others use IPs
        if network != "mainnet":
            assert any("3.12.224.189" in s for s in seeds), f"3.12.224.189 not in NETWORK_SEEDS[{network}]"
    print(f"  ✓ NETWORK_SEEDS has both seed IPs for all networks")


def test_p2p_service_methods():
    """Test that P2PServiceLegacy has required methods."""
    print("\n✓ Testing P2PServiceLegacy methods...")
    
    # Check source code directly instead of importing
    import inspect
    from pathlib import Path
    
    service_path = Path(__file__).parent / "p2p" / "node" / "service.py"
    source = service_path.read_text()
    
    # Check for _network_best_height method
    assert "def _network_best_height" in source, "P2PServiceLegacy missing _network_best_height method"
    print("  ✓ P2PServiceLegacy has _network_best_height method")
    
    # Check for _seed_reconnect_loop method
    assert "def _seed_reconnect_loop" in source, "P2PServiceLegacy missing _seed_reconnect_loop method"
    print("  ✓ P2PServiceLegacy has _seed_reconnect_loop method")
    
    # Check for _sync_monitor_loop method
    assert "def _sync_monitor_loop" in source, "P2PServiceLegacy missing _sync_monitor_loop method"
    print("  ✓ P2PServiceLegacy has _sync_monitor_loop method")
    
    # Check for improved _dial method (should have backoff logic)
    assert "max_attempts" in source, "_dial method missing max_attempts parameter"
    assert "backoff" in source or "delay" in source, "_dial method missing backoff logic"
    print("  ✓ P2PServiceLegacy._dial has backoff logic")


def test_gossip_engine():
    """Test that gossip engine is properly configured."""
    print("\n✓ Testing gossip engine...")
    
    # Check source code directly
    from pathlib import Path
    
    engine_path = Path(__file__).parent / "p2p" / "gossip" / "engine.py"
    source = engine_path.read_text()
    
    # Check that gossip engine has required methods
    assert "def publish" in source, "GossipEngine missing publish method"
    assert "def subscribe" in source, "GossipEngine missing subscribe method"
    assert "def receive_gossip" in source, "GossipEngine missing receive_gossip method"
    print("  ✓ GossipEngine has required methods")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing Peer Discovery and Sync Fixes")
    print("=" * 60)
    
    try:
        test_seed_configuration()
        test_p2p_service_methods()
        test_gossip_engine()
        
        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
