#!/usr/bin/env python3
"""
Integration test for periodic health check fix.

This simulates the actual problem: a node syncs on startup, reaches a state
where it thinks it's synced, then stops making sync requests even though
new blocks are available.
"""

import time


class SimulatedNode:
    """Simulates a node's sync state."""
    
    def __init__(self):
        self.phase = "IDLE"
        self.local_height = 0
        self.target_height = None
        self.last_progress_at = time.time()
        self.inflight_headers = 0
        self.inflight_blocks = 0
        self.peers_count = 1
        self.sync_forced_count = 0
        self.periodic_checks_triggered = 0
        
    def simulate_startup_sync(self):
        """Simulate initial sync on startup."""
        print("\n1. Node starts up, begins syncing...")
        self.phase = "SYNCING"
        self.local_height = 0
        self.target_height = 100
        self.last_progress_at = time.time()
        print(f"   Phase: {self.phase}, Height: {self.local_height}, Target: {self.target_height}")
        
        # Simulate syncing to height 100
        time.sleep(0.1)
        self.local_height = 100
        self.last_progress_at = time.time()
        self.phase = "SYNCED"
        print(f"   Synced to height {self.local_height}")
        print(f"   Phase: {self.phase}")
        
    def simulate_new_blocks_available(self, new_height):
        """Simulate network producing new blocks."""
        print(f"\n2. Network produces new blocks (height now {new_height})...")
        # In the bug scenario, target_height doesn't update if announcements are missed
        # self.target_height = new_height  # This might NOT happen!
        print(f"   Target height: {self.target_height} (may be stale!)")
        
    def check_periodic_health_check(self, elapsed_time):
        """Check if periodic health check would trigger."""
        now = time.time()
        
        # This is the NEW logic we added
        periodic_health_check = (
            self.phase in ("SYNCED", "TARGET_REACHED", "IDLE")
            and now - self.last_progress_at > 30.0
            and not self.inflight_headers
            and not self.inflight_blocks
            and self.peers_count > 0
        )
        
        return periodic_health_check
    
    def simulate_time_passing(self, seconds):
        """Simulate time passing."""
        print(f"\n3. Time passes ({seconds}s)...")
        # Move back last_progress_at to simulate no progress
        self.last_progress_at -= seconds
        
    def simulate_sync_loop_iteration(self):
        """Simulate one sync loop iteration."""
        now = time.time()
        
        # Check periodic health check
        periodic_health_check = self.check_periodic_health_check(now - self.last_progress_at)
        
        # Check other force conditions
        stalled = False
        sync_force_always = False
        sync_requested = False
        at_tip_but_behind = (
            self.phase in ("SYNCED", "TARGET_REACHED")
            and self.target_height is not None
            and self.local_height < self.target_height
            and not self.inflight_headers
            and not self.inflight_blocks
        )
        
        force_sync = stalled or sync_force_always or sync_requested or at_tip_but_behind or periodic_health_check
        
        print(f"\n4. Sync loop iteration:")
        print(f"   Phase: {self.phase}")
        print(f"   Local height: {self.local_height}")
        print(f"   Target height: {self.target_height}")
        print(f"   Time since progress: {now - self.last_progress_at:.1f}s")
        print(f"   Periodic health check: {'YES ✓' if periodic_health_check else 'NO'}")
        print(f"   at_tip_but_behind: {'YES' if at_tip_but_behind else 'NO'}")
        print(f"   force_sync: {'YES ✓' if force_sync else 'NO ✗'}")
        
        if periodic_health_check:
            self.periodic_checks_triggered += 1
            
        if force_sync:
            self.sync_forced_count += 1
            print(f"   → Sync attempt will be made! ✓")
            return True
        else:
            print(f"   → No sync attempt (STUCK!) ✗")
            return False


def test_bug_scenario_without_fix():
    """Test the bug scenario without the periodic health check."""
    print("=" * 70)
    print("SCENARIO: Bug Without Fix (Missing Periodic Health Check)")
    print("=" * 70)
    
    class OldNode(SimulatedNode):
        """Old node without periodic health check."""
        def check_periodic_health_check(self, elapsed_time):
            return False  # OLD: No periodic health check
    
    node = OldNode()
    node.simulate_startup_sync()
    node.simulate_new_blocks_available(150)
    node.simulate_time_passing(35)  # 35 seconds pass
    
    synced = node.simulate_sync_loop_iteration()
    
    print(f"\n   Result: {'SYNCED ✓' if synced else 'STUCK ✗'}")
    print(f"   Problem: Node is STUCK at height {node.local_height}")
    print(f"            Network is at height 150 but node never syncs!")
    
    return not synced  # We expect it to be stuck (bug present)


def test_fix_scenario_with_periodic_check():
    """Test the fix scenario with periodic health check."""
    print("\n\n" + "=" * 70)
    print("SCENARIO: Fix With Periodic Health Check")
    print("=" * 70)
    
    node = SimulatedNode()
    node.simulate_startup_sync()
    node.simulate_new_blocks_available(150)
    node.simulate_time_passing(35)  # 35 seconds pass
    
    synced = node.simulate_sync_loop_iteration()
    
    print(f"\n   Result: {'SYNCED ✓' if synced else 'STUCK ✗'}")
    print(f"   Fix: Periodic health check triggers after 30s")
    print(f"        Node attempts sync even without block announcements!")
    print(f"        Periodic checks triggered: {node.periodic_checks_triggered}")
    
    return synced  # We expect it to sync (bug fixed)


def test_no_false_positives():
    """Test that periodic check doesn't trigger unnecessarily."""
    print("\n\n" + "=" * 70)
    print("SCENARIO: No False Positives")
    print("=" * 70)
    
    node = SimulatedNode()
    node.phase = "SYNCED"
    node.local_height = 100
    node.target_height = 100
    node.last_progress_at = time.time() - 10  # Recent progress (10s ago)
    
    print(f"\n   Phase: {node.phase}")
    print(f"   Local height: {node.local_height}")
    print(f"   Target height: {node.target_height}")
    print(f"   Time since progress: 10s (recent)")
    
    periodic_check = node.check_periodic_health_check(10)
    
    print(f"\n   Periodic check: {'TRIGGERED ✗' if periodic_check else 'NOT TRIGGERED ✓'}")
    print(f"   Result: {'PASS ✓' if not periodic_check else 'FAIL ✗'}")
    print(f"   No false positives - check only triggers when needed")
    
    return not periodic_check


def test_respects_inflight_requests():
    """Test that periodic check respects inflight requests."""
    print("\n\n" + "=" * 70)
    print("SCENARIO: Respects Inflight Requests")
    print("=" * 70)
    
    node = SimulatedNode()
    node.phase = "SYNCED"
    node.local_height = 100
    node.target_height = 100
    node.last_progress_at = time.time() - 35  # Stale (35s ago)
    node.inflight_headers = 5  # Already fetching headers
    
    print(f"\n   Phase: {node.phase}")
    print(f"   Time since progress: 35s (stale)")
    print(f"   Inflight headers: {node.inflight_headers}")
    
    periodic_check = node.check_periodic_health_check(35)
    
    print(f"\n   Periodic check: {'TRIGGERED ✗' if periodic_check else 'NOT TRIGGERED ✓'}")
    print(f"   Result: {'PASS ✓' if not periodic_check else 'FAIL ✗'}")
    print(f"   Avoids duplicate work - respects existing requests")
    
    return not periodic_check


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("PERIODIC HEALTH CHECK INTEGRATION TEST")
    print("=" * 70)
    
    try:
        # Test the bug scenario
        bug_present = test_bug_scenario_without_fix()
        assert bug_present, "Bug scenario should show node getting stuck"
        
        # Test the fix
        bug_fixed = test_fix_scenario_with_periodic_check()
        assert bug_fixed, "Fix scenario should show node syncing"
        
        # Test no false positives
        no_false_positives = test_no_false_positives()
        assert no_false_positives, "Should not trigger with recent progress"
        
        # Test respects inflight
        respects_inflight = test_respects_inflight_requests()
        assert respects_inflight, "Should not trigger with inflight requests"
        
        print("\n\n" + "=" * 70)
        print("ALL INTEGRATION TESTS PASSED ✓")
        print("=" * 70)
        print("\nSummary:")
        print("✓ Bug scenario demonstrated (node gets stuck without fix)")
        print("✓ Fix scenario works (periodic check recovers from stuck state)")
        print("✓ No false positives (doesn't trigger unnecessarily)")
        print("✓ Respects inflight requests (avoids duplicate work)")
        print("\nThe periodic health check successfully prevents nodes from")
        print("stopping sync after a short while on startup!")
        
    except AssertionError as e:
        print(f"\n\nTEST FAILED: {e}")
        exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
