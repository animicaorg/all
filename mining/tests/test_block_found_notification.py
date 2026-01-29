"""
Tests for block found notification feature that immediately refreshes templates
when a block is found, allowing miners to switch to the next block without delay.
"""
import asyncio
import time
from typing import Any, Dict, Optional

import pytest

from mining.orchestrator import (
    TemplateFeeder,
    register_template_feeder,
    unregister_template_feeder,
    notify_all_template_feeders_block_found,
)


class MockTemplateProvider:
    """Mock template provider that returns different templates on each call."""

    def __init__(self):
        self.call_count = 0
        self.templates = []

    def add_template(self, job_id: str, parent_hash: str = "0xaabb"):
        """Add a template to be returned on next call."""
        self.templates.append({
            "jobId": job_id,
            "parent": {"hash": parent_hash},
            "workSource": "solo_template",
            "header": {"height": len(self.templates) + 1},
        })

    async def current_template(self) -> Optional[Dict[str, Any]]:
        """Return the current template."""
        if self.call_count < len(self.templates):
            tpl = self.templates[self.call_count]
            self.call_count += 1
            return tpl
        return None

    async def refresh(self) -> Optional[Dict[str, Any]]:
        """Refresh and return a new template."""
        return await self.current_template()


@pytest.mark.asyncio
async def test_template_feeder_block_found_notification():
    """
    Test that notify_block_found() triggers immediate template refresh.
    """
    provider = MockTemplateProvider()
    provider.add_template("job-1", "0xaaaa")
    provider.add_template("job-2", "0xbbbb")
    provider.add_template("job-3", "0xcccc")

    feeder = TemplateFeeder(
        provider=provider,
        interval_sec=10.0,  # Long interval to ensure notification is what triggers refresh
        ws_hub=None,
        stale_after_sec=100.0,
        cooldown=None,
    )

    templates = []
    stop_evt = asyncio.Event()

    async def collect_templates():
        """Collect templates from the feeder."""
        async for tpl in feeder:
            templates.append(tpl)
            if len(templates) >= 2:
                stop_evt.set()
                break

    # Start collecting templates in the background
    collect_task = asyncio.create_task(collect_templates())

    # Wait for first template
    await asyncio.sleep(0.1)
    assert len(templates) == 1
    assert templates[0]["jobId"] == "job-1"

    # Trigger block found notification - should immediately get next template
    feeder.notify_block_found()

    # Wait for second template (should arrive quickly without waiting for interval)
    try:
        await asyncio.wait_for(stop_evt.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pytest.fail("Block found notification did not trigger immediate template refresh")

    assert len(templates) == 2
    assert templates[1]["jobId"] == "job-2"

    # Clean up
    feeder.stop()
    collect_task.cancel()
    try:
        await collect_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_template_feeder_without_notification():
    """
    Test that without notification, template feeder waits for interval.
    """
    provider = MockTemplateProvider()
    provider.add_template("job-1", "0xaaaa")
    provider.add_template("job-2", "0xbbbb")

    feeder = TemplateFeeder(
        provider=provider,
        interval_sec=0.5,  # Half second interval
        ws_hub=None,
        stale_after_sec=100.0,
        cooldown=None,
    )

    templates = []
    stop_evt = asyncio.Event()

    async def collect_templates():
        """Collect templates from the feeder."""
        async for tpl in feeder:
            templates.append(tpl)
            if len(templates) >= 2:
                stop_evt.set()
                break

    # Start collecting templates in the background
    collect_task = asyncio.create_task(collect_templates())

    # Wait for first template
    await asyncio.sleep(0.1)
    assert len(templates) == 1
    assert templates[0]["jobId"] == "job-1"

    # Without notification, should wait for interval
    start_time = time.monotonic()
    try:
        await asyncio.wait_for(stop_evt.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pytest.fail("Template not received within timeout")
    elapsed = time.monotonic() - start_time

    # Should have waited approximately the interval time
    assert elapsed >= 0.4, f"Template arrived too quickly ({elapsed}s), expected ~0.5s"
    assert len(templates) == 2

    # Clean up
    feeder.stop()
    collect_task.cancel()
    try:
        await collect_task
    except asyncio.CancelledError:
        pass


def test_global_registry():
    """
    Test global template feeder registry for notifications.
    """
    # Create mock feeders
    class MockFeeder:
        def __init__(self):
            self.notified = False

        def notify_block_found(self):
            self.notified = True

    feeder1 = MockFeeder()
    feeder2 = MockFeeder()

    # Register feeders
    register_template_feeder(feeder1)
    register_template_feeder(feeder2)

    # Notify all
    notify_all_template_feeders_block_found()

    # Both should be notified
    assert feeder1.notified
    assert feeder2.notified

    # Unregister and test again
    feeder1.notified = False
    feeder2.notified = False
    unregister_template_feeder(feeder1)

    notify_all_template_feeders_block_found()

    # Only feeder2 should be notified
    assert not feeder1.notified
    assert feeder2.notified

    # Clean up
    unregister_template_feeder(feeder2)


def test_global_registry_handles_exceptions():
    """
    Test that global registry handles exceptions gracefully.
    """

    class BrokenFeeder:
        def notify_block_found(self):
            raise RuntimeError("Intentional test error")

    class GoodFeeder:
        def __init__(self):
            self.notified = False

        def notify_block_found(self):
            self.notified = True

    broken = BrokenFeeder()
    good = GoodFeeder()

    register_template_feeder(broken)
    register_template_feeder(good)

    # Should not raise despite broken feeder
    notify_all_template_feeders_block_found()

    # Good feeder should still be notified
    assert good.notified

    # Clean up
    unregister_template_feeder(broken)
    unregister_template_feeder(good)


def test_register_none_is_safe():
    """
    Test that registering None doesn't cause issues (backwards compatibility).
    """
    # Should not raise
    register_template_feeder(None)
    unregister_template_feeder(None)
    notify_all_template_feeders_block_found()


@pytest.mark.asyncio
async def test_template_feeder_stops_cleanly():
    """
    Test that template feeder stops cleanly when stop() is called.
    """
    provider = MockTemplateProvider()
    provider.add_template("job-1", "0xaaaa")

    feeder = TemplateFeeder(
        provider=provider,
        interval_sec=1.0,
        ws_hub=None,
        stale_after_sec=100.0,
        cooldown=None,
    )

    templates = []

    async def collect_templates():
        """Collect templates from the feeder."""
        async for tpl in feeder:
            templates.append(tpl)

    # Start collecting
    collect_task = asyncio.create_task(collect_templates())

    # Wait for first template
    await asyncio.sleep(0.1)
    assert len(templates) == 1

    # Stop the feeder
    feeder.stop()

    # Task should complete without hanging
    try:
        await asyncio.wait_for(collect_task, timeout=1.0)
    except asyncio.TimeoutError:
        pytest.fail("Template feeder did not stop cleanly")


@pytest.mark.asyncio
async def test_multiple_block_found_notifications():
    """
    Test that multiple rapid block found notifications are handled correctly.
    """
    provider = MockTemplateProvider()
    for i in range(10):
        provider.add_template(f"job-{i}", f"0x{i:04x}")

    feeder = TemplateFeeder(
        provider=provider,
        interval_sec=10.0,  # Long interval
        ws_hub=None,
        stale_after_sec=100.0,
        cooldown=None,
    )

    templates = []
    stop_evt = asyncio.Event()

    async def collect_templates():
        """Collect templates from the feeder."""
        async for tpl in feeder:
            templates.append(tpl)
            if len(templates) >= 5:
                stop_evt.set()
                break

    # Start collecting
    collect_task = asyncio.create_task(collect_templates())

    # Wait for first template
    await asyncio.sleep(0.1)
    assert len(templates) == 1

    # Send multiple rapid notifications
    for _ in range(4):
        feeder.notify_block_found()
        await asyncio.sleep(0.05)

    # Should get 5 templates total
    try:
        await asyncio.wait_for(stop_evt.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail(f"Only received {len(templates)} templates, expected 5")

    assert len(templates) == 5

    # Clean up
    feeder.stop()
    collect_task.cancel()
    try:
        await collect_task
    except asyncio.CancelledError:
        pass
