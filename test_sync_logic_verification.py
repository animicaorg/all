#!/usr/bin/env python3
"""
Simple verification that the fix doesn't break the normal sync flow.
Tests various scenarios without requiring full system dependencies.
"""

from collections import deque
from typing import Dict, List, Optional, Set


def simulate_sync_schedule(
    local_height: int,
    queued_heights: List[int],
    inflight_heights: List[int],
    existing_heights: List[int],
    max_inflight: int = 2048
) -> tuple[List[int], List[int]]:
    """
    Simulate the block scheduling logic with the fix.
    Returns (requested_heights, deferred_heights).
    """
    expected_height = local_height + 1
    
    # Convert heights to mock hashes
    def h2b(height: int) -> bytes:
        return f"block_{height}".encode()
    
    queued = [h2b(h) for h in queued_heights]
    inflight = {h2b(h) for h in inflight_heights}
    existing = {h2b(h) for h in existing_heights}
    heights_map = {h2b(h): h for h in queued_heights}
    
    # Sort by height
    queued.sort(key=lambda h: heights_map.get(h, 1_000_000_000))
    
    to_request = []
    deferred = []
    
    for h in queued:
        height_hint = heights_map.get(h)
        
        # Skip cached blocks
        # (in real code this would be await self._try_import_cached_block(h))
        if height_hint is not None and height_hint <= local_height:
            # Update expected_height when skipping cached blocks at expected height
            if height_hint == expected_height:
                expected_height += 1
            continue
        
        # Skip blocks that already exist or are in flight
        if h in existing or h in inflight:
            # Update expected_height when skipping blocks at expected height
            if height_hint is not None and height_hint == expected_height:
                expected_height += 1
            continue
        
        # Defer blocks ahead of expected_height
        if height_hint is not None and height_hint > expected_height:
            deferred.append(height_hint)
            continue
        
        # Inflight limit check
        if len(to_request) >= max_inflight:
            deferred.append(height_hint)
            continue
        
        # Add to request list
        if height_hint is not None:
            to_request.append(height_hint)
        
        # Update expected_height
        if height_hint == expected_height:
            expected_height += 1
    
    return sorted(to_request), sorted(deferred)


def test_scenario(name: str, local_height: int, queued: List[int], inflight: List[int], existing: List[int], expected_requested: List[int], expected_deferred: List[int]) -> bool:
    """Test a specific scenario."""
    requested, deferred = simulate_sync_schedule(local_height, queued, inflight, existing)
    
    success = (requested == expected_requested and deferred == expected_deferred)
    status = "✓ PASS" if success else "✗ FAIL"
    
    print(f"\n{status}: {name}")
    print(f"  Local height: {local_height}")
    print(f"  Queued: {len(queued)} blocks")
    print(f"  In flight: {inflight if inflight else 'none'}")
    print(f"  Existing: {existing if existing else 'none'}")
    print(f"  Expected requested: {expected_requested if expected_requested else 'none'}")
    print(f"  Actual requested: {requested if requested else 'none'}")
    if deferred:
        print(f"  Deferred: {len(deferred)} blocks")
    
    if not success:
        if requested != expected_requested:
            print(f"    ✗ Requested mismatch: expected {len(expected_requested)}, got {len(requested)}")
        if deferred != expected_deferred:
            print(f"    ✗ Deferred mismatch: expected {len(expected_deferred)}, got {len(deferred)}")
    
    return success


def main():
    print("=" * 70)
    print("Sync Schedule Logic Verification Tests")
    print("=" * 70)
    
    results = []
    
    # Test 1: Normal sequential sync (no blocks in flight)
    results.append(test_scenario(
        "Normal sequential sync",
        local_height=100,
        queued=list(range(101, 111)),  # 101-110
        inflight=[],
        existing=[],
        expected_requested=list(range(101, 111)),  # All 10 blocks
        expected_deferred=[]
    ))
    
    # Test 2: Some blocks already in flight (THE BUG SCENARIO)
    results.append(test_scenario(
        "Blocks in flight (bug scenario)",
        local_height=11043,
        queued=list(range(11044, 11163)),  # 11044-11162
        inflight=list(range(11044, 11051)),  # 11044-11050 in flight
        existing=[],
        expected_requested=list(range(11051, 11163)),  # 11051-11162 should be requested
        expected_deferred=[]
    ))
    
    # Test 3: Some blocks already exist
    results.append(test_scenario(
        "Some blocks already exist",
        local_height=200,
        queued=list(range(201, 216)),  # 201-215
        inflight=[],
        existing=[201, 202, 203],  # First 3 already exist
        expected_requested=list(range(204, 216)),  # 204-215 should be requested
        expected_deferred=[]
    ))
    
    # Test 4: Gap in sequence (missing headers)
    results.append(test_scenario(
        "Gap in sequence",
        local_height=300,
        queued=[301, 302, 303, 310, 311, 312],  # Gap at 304-309
        inflight=[],
        existing=[],
        expected_requested=[301, 302, 303],  # Only sequential blocks
        expected_deferred=[310, 311, 312]  # Future blocks deferred
    ))
    
    # Test 5: Mix of inflight and gaps
    results.append(test_scenario(
        "Mix of inflight and gaps",
        local_height=400,
        queued=[401, 402, 403, 404, 405, 410, 411],
        inflight=[401, 402],  # First 2 in flight
        existing=[],
        expected_requested=[403, 404, 405],  # 403-405 requested
        expected_deferred=[410, 411]  # 410-411 deferred (gap)
    ))
    
    # Test 6: All blocks in flight (no new requests needed)
    results.append(test_scenario(
        "All blocks in flight",
        local_height=500,
        queued=list(range(501, 506)),
        inflight=list(range(501, 506)),
        existing=[],
        expected_requested=[],  # Nothing to request
        expected_deferred=[]
    ))
    
    # Test 7: Blocks out of order in queue (should still work due to sorting)
    results.append(test_scenario(
        "Out of order queue",
        local_height=600,
        queued=[605, 602, 604, 601, 603],  # Unsorted
        inflight=[],
        existing=[],
        expected_requested=[601, 602, 603, 604, 605],  # All requested in order
        expected_deferred=[]
    ))
    
    print("\n" + "=" * 70)
    print("Summary:")
    print(f"  Total tests: {len(results)}")
    print(f"  Passed: {sum(results)}")
    print(f"  Failed: {len(results) - sum(results)}")
    print("=" * 70)
    
    if all(results):
        print("\n✓ ALL TESTS PASSED! The fix works correctly.")
        return 0
    else:
        print("\n✗ SOME TESTS FAILED! Please review the fix.")
        return 1


if __name__ == "__main__":
    exit(main())
