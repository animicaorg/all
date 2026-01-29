#!/usr/bin/env python3
"""
Manual verification script for ineligible peer block discard fix.

This script helps verify that blocks from peers with handshake_pending status
are properly discarded and the sync recovers by using trusted force peers.

Usage:
    python3 verify_ineligible_peer_fix.py --rpc http://127.0.0.1:8545/rpc
"""

import argparse
import asyncio
import json
import sys
import time
from typing import Any, Dict

try:
    import httpx
except ImportError:
    print("Error: httpx not found. Install with: pip install httpx")
    sys.exit(1)


async def rpc_call(
    rpc_url: str, method: str, params: list[Any] | None = None
) -> Any:
    """Make a JSON-RPC call."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(rpc_url, json=payload)
        data = response.json()
        
        if "error" in data:
            raise RuntimeError(f"RPC error: {data['error']}")
        
        return data.get("result")


async def check_sync_status(rpc_url: str) -> Dict[str, Any]:
    """Get sync status from the node."""
    return await rpc_call(rpc_url, "sync.getStatus", [{"refresh": True}])


async def trigger_force_sync(rpc_url: str) -> Dict[str, Any]:
    """Trigger a force sync."""
    return await rpc_call(rpc_url, "sync.force", [])


def print_sync_diagnostics(status: Dict[str, Any]) -> None:
    """Print sync diagnostics."""
    print("\n=== Sync Status ===")
    print(f"Phase: {status.get('phase')}")
    print(f"Height: {status.get('head_height')}")
    print(f"Best Header Height: {status.get('best_header_height')}")
    print(f"Network Best Height: {status.get('network_best_height')}")
    print(f"In-flight Blocks: {status.get('in_flight_blocks')}")
    print(f"Queued Blocks: {status.get('queued_blocks_count')}")
    
    print("\n=== Peer Status ===")
    eligible_headers = status.get('eligible_peers_for_headers', [])
    ineligible_headers = status.get('ineligible_peers_for_headers', {})
    eligible_blocks = status.get('eligible_peers_for_blocks', [])
    ineligible_blocks = status.get('ineligible_peers_for_blocks', {})
    
    print(f"Eligible peers for headers: {len(eligible_headers)}")
    for peer in eligible_headers:
        print(f"  ✓ {peer}")
    
    print(f"\nIneligible peers for headers: {len(ineligible_headers)}")
    for peer, reason in ineligible_headers.items():
        print(f"  ✗ {peer}: {reason}")
    
    print(f"\nEligible peers for blocks: {len(eligible_blocks)}")
    for peer in eligible_blocks:
        print(f"  ✓ {peer}")
    
    print(f"\nIneligible peers for blocks: {len(ineligible_blocks)}")
    for peer, reason in ineligible_blocks.items():
        print(f"  ✗ {peer}: {reason}")
    
    print("\n=== Stall Status ===")
    stall_reason = status.get('stall_reason')
    stall_elapsed = status.get('stall_elapsed_s', 0)
    last_block_error_peer = status.get('last_block_error_peer')
    last_block_error = status.get('last_block_error')
    
    if stall_reason:
        print(f"Stalled: {stall_reason} (elapsed: {stall_elapsed:.1f}s)")
    else:
        print("Not stalled")
    
    if last_block_error:
        print(f"Last block error: {last_block_error}")
        if last_block_error_peer:
            print(f"Error peer: {last_block_error_peer}")
    
    # Check if force peer is in eligible list
    force_peer = "144.126.133.21:30333"
    if force_peer in eligible_headers or force_peer in eligible_blocks:
        print(f"\n✅ Force peer {force_peer} is ELIGIBLE")
    elif force_peer in ineligible_headers or force_peer in ineligible_blocks:
        reason = ineligible_headers.get(force_peer) or ineligible_blocks.get(force_peer)
        print(f"\n⚠️  Force peer {force_peer} is INELIGIBLE: {reason}")
    else:
        print(f"\n⚠️  Force peer {force_peer} is NOT CONNECTED")


async def monitor_sync_recovery(rpc_url: str, duration: int = 60) -> None:
    """Monitor sync recovery over time."""
    print(f"\nMonitoring sync recovery for {duration} seconds...")
    print("(Checking every 5 seconds)\n")
    
    start_time = time.time()
    last_height = None
    progress_made = False
    
    while time.time() - start_time < duration:
        try:
            status = await check_sync_status(rpc_url)
            height = status.get('head_height', 0)
            phase = status.get('phase')
            ineligible_count = len(status.get('ineligible_peers_for_blocks', {}))
            
            # Check for progress
            if last_height is not None and height > last_height:
                progress_made = True
                print(f"✅ Progress! Height: {last_height} -> {height} ({phase})")
            else:
                indicator = "📊" if progress_made else "⏳"
                print(f"{indicator} Height: {height} | Phase: {phase} | Ineligible peers: {ineligible_count}")
            
            last_height = height
            
            # Check if handshake_pending peers exist
            ineligible_peers = status.get('ineligible_peers_for_blocks', {})
            handshake_pending = [
                peer for peer, reason in ineligible_peers.items()
                if 'handshake' in reason.lower()
            ]
            
            if handshake_pending:
                print(f"   ⚠️  {len(handshake_pending)} peer(s) with handshake_pending")
            
        except Exception as e:
            print(f"Error checking status: {e}")
        
        await asyncio.sleep(5)
    
    print("\n" + "="*60)
    if progress_made:
        print("✅ RECOVERY SUCCESSFUL: Height increased during monitoring")
    else:
        print("⚠️  NO PROGRESS: Height did not increase")
        print("   This may indicate a sync stall that needs investigation")


async def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Verify ineligible peer block discard fix"
    )
    parser.add_argument(
        "--rpc",
        default="http://127.0.0.1:8545/rpc",
        help="RPC URL (default: http://127.0.0.1:8545/rpc)"
    )
    parser.add_argument(
        "--force-sync",
        action="store_true",
        help="Trigger force sync before monitoring"
    )
    parser.add_argument(
        "--monitor",
        type=int,
        default=60,
        help="Monitoring duration in seconds (default: 60)"
    )
    
    args = parser.parse_args()
    
    print("="*60)
    print("Verifying Ineligible Peer Block Discard Fix")
    print("="*60)
    print(f"RPC URL: {args.rpc}")
    
    try:
        # Get initial status
        print("\n📊 Fetching initial sync status...")
        status = await check_sync_status(args.rpc)
        print_sync_diagnostics(status)
        
        # Trigger force sync if requested
        if args.force_sync:
            print("\n🔄 Triggering force sync...")
            result = await trigger_force_sync(args.rpc)
            print(f"Force sync result: {json.dumps(result, indent=2)}")
        
        # Monitor recovery
        await monitor_sync_recovery(args.rpc, args.monitor)
        
        # Get final status
        print("\n📊 Fetching final sync status...")
        status = await check_sync_status(args.rpc)
        print_sync_diagnostics(status)
        
        print("\n" + "="*60)
        print("Verification complete!")
        print("="*60)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
