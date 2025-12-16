#!/usr/bin/env python3
"""
Test script to verify P2P peer connectivity and seed dialing.
This script simulates starting a P2P service with network-specific seeds
and validates that the improved logging is working correctly.
"""
import asyncio
import logging
import os
import socket
import sys
from io import StringIO

# Setup logging to capture output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def _free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

async def test_p2p_service_with_seeds():
    """Test P2PService initialization and seed dialing with improved logging."""
    from p2p.node.service import P2PService
    
    # Test with mainnet seeds (chain_id=1)
    print("\n=== Test 1: P2PService with mainnet seeds (chain_id=1) ===")
    
    # Create a log capture
    log_stream = StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setLevel(logging.INFO)
    
    p2p_logger = logging.getLogger("animica.p2p.service")
    p2p_logger.addHandler(handler)
    p2p_logger.setLevel(logging.INFO)
    
    # Use network-specific seeds by setting chain_id
    os.environ["ANIMICA_P2P_CHAIN_ID"] = "1"
    
    from p2p import config as p2p_config
    cfg = p2p_config.load_config()
    
    print(f"Loaded seeds: {cfg.seeds}")
    
    # Create service with mainnet seeds
    port = _free_port()
    svc = P2PService(
        listen_addrs=[f"/ip4/127.0.0.1/tcp/{port}"],
        seeds=list(cfg.seeds),
        chain_id=1
    )
    
    try:
        await svc.start()
        
        # Give it a moment to attempt connections
        await asyncio.sleep(2.0)
        
        # Check logs
        log_output = log_stream.getvalue()
        print("\nCaptured logs:")
        print(log_output)
        
        # Verify expected log messages
        assert "Dialing seed:" in log_output or "Failed to dial" in log_output, \
            "Expected seed dialing logs not found"
        
        print("\n✓ Test 1 passed: Seed dialing logs are being generated")
        
    finally:
        await svc.stop()
        p2p_logger.removeHandler(handler)

async def test_p2p_service_no_seeds():
    """Test P2PService with no seeds configured."""
    from p2p.node.service import P2PService
    
    print("\n=== Test 2: P2PService with no seeds ===")
    
    # Create a log capture
    log_stream = StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setLevel(logging.INFO)
    
    p2p_logger = logging.getLogger("animica.p2p.service")
    p2p_logger.addHandler(handler)
    p2p_logger.setLevel(logging.INFO)
    
    # Create service with no seeds
    port = _free_port()
    svc = P2PService(
        listen_addrs=[f"/ip4/127.0.0.1/tcp/{port}"],
        seeds=[],
        chain_id=1337
    )
    
    try:
        await svc.start()
        await asyncio.sleep(0.5)
        
        # Check logs
        log_output = log_stream.getvalue()
        print("\nCaptured logs:")
        print(log_output)
        
        # Verify warning about no seeds
        assert "No seeds configured" in log_output, \
            "Expected warning about no seeds not found"
        
        print("\n✓ Test 2 passed: Warning about no seeds is logged")
        
    finally:
        await svc.stop()
        p2p_logger.removeHandler(handler)

async def test_config_with_chain_id():
    """Test that config properly loads network-specific seeds."""
    from p2p import config as p2p_config
    
    print("\n=== Test 3: Config loading with chain_id ===")
    
    # Test mainnet
    os.environ["ANIMICA_P2P_CHAIN_ID"] = "1"
    cfg = p2p_config.load_config()
    print(f"Mainnet seeds: {cfg.seeds}")
    assert any("mainnet.animica.org" in s or "144.126.133.21" in s for s in cfg.seeds), \
        "Mainnet seeds should include mainnet domain or fallback IP"
    print("✓ Mainnet seeds loaded correctly")
    
    # Test testnet
    os.environ["ANIMICA_P2P_CHAIN_ID"] = "2"
    cfg = p2p_config.load_config()
    print(f"Testnet seeds: {cfg.seeds}")
    assert any("testnet.animica.org" in s or "144.126.133.21" in s for s in cfg.seeds), \
        "Testnet seeds should include testnet domain or fallback IP"
    print("✓ Testnet seeds loaded correctly")
    
    # Test devnet
    os.environ["ANIMICA_P2P_CHAIN_ID"] = "1337"
    cfg = p2p_config.load_config()
    print(f"Devnet seeds: {cfg.seeds}")
    assert any("devnet.animica.org" in s or "144.126.133.21" in s for s in cfg.seeds), \
        "Devnet seeds should include devnet domain or fallback IP"
    print("✓ Devnet seeds loaded correctly")
    
    print("\n✓ Test 3 passed: All network-specific seeds loaded correctly")

async def main():
    """Run all tests."""
    print("=" * 70)
    print("P2P Peer Connectivity Test Suite")
    print("=" * 70)
    
    try:
        await test_config_with_chain_id()
        await test_p2p_service_no_seeds()
        await test_p2p_service_with_seeds()
        
        print("\n" + "=" * 70)
        print("ALL TESTS PASSED ✓")
        print("=" * 70)
        return 0
        
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
