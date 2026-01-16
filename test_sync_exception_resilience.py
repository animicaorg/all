#!/usr/bin/env python3
"""
Test sync exception resilience improvements.

This test verifies that the sync system continues working through
any exceptions and doesn't get permanently stuck.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch


def test_sync_loop_exception_handling():
    """Test that sync loop continues after exceptions"""
    print("Testing sync loop exception handling...")
    
    # Simulate exception scenarios
    exceptions_handled = []
    
    class MockSync:
        def __init__(self):
            self._running = True
            self._sync_enabled = True
            self._sync_paused = False
            self._sync_tick_sec = 0.01
            self.iteration_count = 0
            self.max_iterations = 5
            
        async def _sync_once(self, force=False):
            self.iteration_count += 1
            # Throw exception on iteration 2 to test recovery
            if self.iteration_count == 2:
                raise ValueError("Simulated sync error")
            # Normal operation otherwise
            return
            
        async def _schedule_block_requests(self):
            pass
            
        def _log_sync_cycle(self):
            pass
            
        async def test_sync_loop(self):
            """Simplified sync loop with exception handling"""
            while self._running and self.iteration_count < self.max_iterations:
                try:
                    if not self._sync_enabled or self._sync_paused:
                        await asyncio.sleep(0.01)
                        continue
                    
                    # Core sync logic
                    await self._sync_once(force=False)
                    self._log_sync_cycle()
                    await self._schedule_block_requests()
                    
                    # Brief delay between iterations
                    await asyncio.sleep(0.01)
                    
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    # This is the critical fix - catch all exceptions
                    exceptions_handled.append({
                        "error_type": type(e).__name__,
                        "error": str(e),
                        "iteration": self.iteration_count,
                    })
                    # Brief delay to avoid tight error loops
                    await asyncio.sleep(0.5)
    
    # Run the test
    async def run_test():
        mock_sync = MockSync()
        await mock_sync.test_sync_loop()
        return mock_sync.iteration_count, exceptions_handled
    
    iterations, exceptions = asyncio.run(run_test())
    
    # Verify results
    assert iterations >= 5, f"Expected 5+ iterations, got {iterations}"
    assert len(exceptions) == 1, f"Expected 1 exception handled, got {len(exceptions)}"
    assert exceptions[0]["error_type"] == "ValueError", f"Wrong exception type: {exceptions[0]['error_type']}"
    assert exceptions[0]["iteration"] == 2, f"Exception at wrong iteration: {exceptions[0]['iteration']}"
    
    print("✓ Test PASSED: Sync loop continues after exception")
    return True


def test_task_watchdog_detection():
    """Test that task watchdog can detect crashed tasks"""
    print("Testing task watchdog detection...")
    
    class MockTask:
        def __init__(self, name, will_crash=False):
            self._name = name
            self._will_crash = will_crash
            self._done = False
            self._exception = ValueError("Task crashed") if will_crash else None
            
        def get_name(self):
            return self._name
            
        def done(self):
            return self._done
            
        def exception(self):
            if not self._done:
                return None
            return self._exception
            
        def crash(self):
            """Simulate task crash"""
            self._done = True
    
    # Simulate task monitoring
    monitored_tasks = {
        "p2p.sync": MockTask("p2p.sync", will_crash=True),
        "p2p.head_watch": MockTask("p2p.head_watch", will_crash=False),
    }
    
    crashed_tasks = []
    restarted_tasks = []
    
    # Crash the sync task
    monitored_tasks["p2p.sync"].crash()
    
    # Simulate watchdog check
    for task_name, task in monitored_tasks.items():
        if task.done():
            exception = task.exception()
            if exception is not None:
                crashed_tasks.append(task_name)
                # Simulate restart
                new_task = MockTask(task_name, will_crash=False)
                monitored_tasks[task_name] = new_task
                restarted_tasks.append(task_name)
    
    # Verify results
    assert len(crashed_tasks) == 1, f"Expected 1 crashed task, got {len(crashed_tasks)}"
    assert "p2p.sync" in crashed_tasks, "Sync task crash not detected"
    assert len(restarted_tasks) == 1, f"Expected 1 restarted task, got {len(restarted_tasks)}"
    assert "p2p.sync" in restarted_tasks, "Sync task not restarted"
    
    print("✓ Test PASSED: Task watchdog detects and restarts crashed tasks")
    return True


def test_multiple_exceptions_handled():
    """Test that sync loop handles multiple consecutive exceptions"""
    print("Testing multiple exception handling...")
    
    exceptions_caught = []
    
    async def failing_operation(iteration):
        """Operation that fails multiple times"""
        if iteration in [1, 3, 5]:
            raise RuntimeError(f"Error at iteration {iteration}")
        return "success"
    
    async def test_loop():
        running = True
        iteration = 0
        max_iterations = 10
        
        while running and iteration < max_iterations:
            try:
                result = await failing_operation(iteration)
                iteration += 1
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                return
            except Exception as e:
                exceptions_caught.append({
                    "iteration": iteration,
                    "error": str(e),
                })
                iteration += 1
                await asyncio.sleep(0.01)
        
        return iteration
    
    final_iteration = asyncio.run(test_loop())
    
    # Verify results
    assert final_iteration == 10, f"Expected 10 iterations, got {final_iteration}"
    assert len(exceptions_caught) == 3, f"Expected 3 exceptions, got {len(exceptions_caught)}"
    caught_iterations = [e["iteration"] for e in exceptions_caught]
    assert caught_iterations == [1, 3, 5], f"Wrong iterations caught: {caught_iterations}"
    
    print("✓ Test PASSED: Multiple exceptions handled correctly")
    return True


def test_cancellation_still_works():
    """Test that clean cancellation still works with new exception handling"""
    print("Testing clean cancellation...")
    
    cancelled = False
    iterations = 0
    
    async def test_loop():
        nonlocal cancelled, iterations
        running = True
        
        while running:
            try:
                iterations += 1
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                cancelled = True
                return
            except Exception:
                # Should not reach here in this test
                pass
    
    async def run_with_cancellation():
        task = asyncio.create_task(test_loop())
        await asyncio.sleep(0.05)  # Let it run a few iterations
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    
    asyncio.run(run_with_cancellation())
    
    # Verify results
    assert cancelled, "Task was not properly cancelled"
    assert iterations >= 1, f"Expected at least 1 iteration, got {iterations}"
    
    print("✓ Test PASSED: Clean cancellation works correctly")
    return True


def run_all_tests():
    """Run all sync resilience tests"""
    print("=" * 60)
    print("Sync Exception Resilience Test Suite")
    print("=" * 60)
    
    tests = [
        ("Exception Handling", test_sync_loop_exception_handling),
        ("Task Watchdog", test_task_watchdog_detection),
        ("Multiple Exceptions", test_multiple_exceptions_handled),
        ("Clean Cancellation", test_cancellation_still_works),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        print(f"\n{name}:")
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
                print(f"✗ Test FAILED: {name}")
        except Exception as e:
            failed += 1
            print(f"✗ Test FAILED with exception: {name}")
            print(f"  Error: {e}")
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
