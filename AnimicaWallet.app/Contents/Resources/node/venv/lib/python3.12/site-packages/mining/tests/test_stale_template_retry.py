"""
Test that mining continues with stale templates and doesn't stop after missing a block.

This test validates the fix for the issue where mining would stop completely
after missing 1 block due to the template feeder not re-yielding stale templates.
"""
import asyncio
import time

import pytest


class MockTemplateProvider:
    """Mock template provider that returns the same template."""

    def __init__(self, template_id="test_template_1"):
        self.template_id = template_id
        self.call_count = 0

    async def current_template(self):
        self.call_count += 1
        return {
            "jobId": self.template_id,
            "templateId": self.template_id,
            "workSource": "solo_template",
            "signBytes": "0x" + "00" * 64,
            "thetaMicro": 693147,
            "shareTarget": 1.0,
            "header": {"number": 1},
            "hints": {"mixSeed": "0x" + "11" * 32},
        }

    async def refresh(self):
        return await self.current_template()


@pytest.mark.asyncio
async def test_template_feeder_continues_with_stale_template():
    """
    Test that TemplateFeeder re-yields stale templates to keep scanner active.
    
    When the blockchain head doesn't change (e.g., after missing a block),
    the template provider keeps returning the same template. The feeder should
    re-yield this template after stale_after_sec to prevent mining from stopping.
    """
    from mining.orchestrator import TemplateFeeder

    provider = MockTemplateProvider()
    
    # Use very short intervals for testing
    feeder = TemplateFeeder(
        provider=provider,
        interval_sec=0.1,
        ws_hub=None,
        stale_after_sec=0.3,  # Re-yield after 0.3 seconds
        cooldown=None,
    )

    templates_received = []
    
    async def consume_templates():
        """Consume templates from the feeder."""
        async for tpl in feeder:
            templates_received.append({
                "jobId": tpl.get("jobId"),
                "timestamp": time.time(),
            })
            # Stop after receiving 3 templates
            if len(templates_received) >= 3:
                feeder.stop()
                break

    # Start consuming templates
    try:
        # Run with a timeout to prevent hanging
        await asyncio.wait_for(consume_templates(), timeout=2.0)
    except asyncio.TimeoutError:
        feeder.stop()
        pytest.fail("Template consumption timed out")

    # Verify we received templates
    assert len(templates_received) >= 2, (
        "Should receive at least 2 templates (initial + re-yield after stale_after_sec)"
    )
    
    # Verify the templates have the same jobId (stale template re-yielded)
    job_ids = [t["jobId"] for t in templates_received]
    assert all(jid == job_ids[0] for jid in job_ids), (
        "All templates should have the same jobId since we're re-yielding stale templates"
    )
    
    # Verify provider was called multiple times (polling continues)
    assert provider.call_count >= 2, (
        "Provider should be called multiple times to check for new templates"
    )
    
    # Verify timing: second template should arrive approximately stale_after_sec after first
    if len(templates_received) >= 2:
        time_diff = templates_received[1]["timestamp"] - templates_received[0]["timestamp"]
        # Allow some tolerance for timing (0.2 to 0.5 seconds)
        assert 0.2 <= time_diff <= 0.5, (
            f"Re-yield should happen after stale_after_sec (0.3s), got {time_diff:.2f}s"
        )


@pytest.mark.asyncio
async def test_template_feeder_updates_when_head_changes():
    """
    Test that TemplateFeeder yields new templates when the head changes.
    
    This is the normal case where the blockchain advances and we get fresh templates.
    """
    from mining.orchestrator import TemplateFeeder

    class ChangingProvider(MockTemplateProvider):
        def __init__(self):
            super().__init__()
            self.template_num = 1

        async def current_template(self):
            self.call_count += 1
            # Change template after 2 calls
            if self.call_count >= 2:
                self.template_num = 2
            return {
                "jobId": f"test_template_{self.template_num}",
                "templateId": f"test_template_{self.template_num}",
                "workSource": "solo_template",
                "signBytes": "0x" + "00" * 64,
                "thetaMicro": 693147,
                "shareTarget": 1.0,
                "header": {"number": self.template_num},
                "hints": {"mixSeed": "0x" + "11" * 32},
            }

    provider = ChangingProvider()
    feeder = TemplateFeeder(
        provider=provider,
        interval_sec=0.1,
        ws_hub=None,
        stale_after_sec=0.5,
        cooldown=None,
    )

    templates_received = []
    
    async def consume_templates():
        """Consume templates from the feeder."""
        async for tpl in feeder:
            templates_received.append({
                "jobId": tpl.get("jobId"),
                "number": tpl.get("header", {}).get("number"),
            })
            # Stop after receiving 2 different templates
            if len(templates_received) >= 2:
                feeder.stop()
                break

    try:
        await asyncio.wait_for(consume_templates(), timeout=2.0)
    except asyncio.TimeoutError:
        feeder.stop()
        pytest.fail("Template consumption timed out")

    # Verify we received 2 templates with different jobIds
    assert len(templates_received) >= 2, "Should receive at least 2 templates"
    
    # Verify the templates have different jobIds (head changed)
    job_ids = [t["jobId"] for t in templates_received]
    assert len(set(job_ids)) >= 2, (
        "Should receive templates with different jobIds when head changes"
    )


@pytest.mark.asyncio
async def test_scanner_continues_with_same_template():
    """
    Test that the hash scanner continues mining when it receives the same template again.
    
    When the feeder re-yields a stale template, the scanner should recognize it
    (by jobId) and continue mining without resetting its nonce position.
    """
    from mining.hash_search import scan_forever

    templates_yielded = 0
    same_template = {
        "jobId": "unchanging_job",
        "templateId": "unchanging_job",
        "workSource": "solo_template",
        "signBytes": "0x" + "aa" * 64,
        "thetaMicro": 693147 * 2,  # Higher threshold for faster finding
        "shareTarget": 1.0,
        "header": {"number": 1},
        "hints": {"mixSeed": "0x" + "bb" * 32},
    }

    class TemplateIterator:
        def __aiter__(self):
            return self

        async def __anext__(self):
            nonlocal templates_yielded
            if templates_yielded >= 3:
                # Let scanner run for a bit, then stop
                await asyncio.sleep(0.2)
                raise StopAsyncIteration
            templates_yielded += 1
            await asyncio.sleep(0.05)
            return same_template

    out_queue = asyncio.Queue()
    stop_evt = asyncio.Event()

    async def run_scanner():
        try:
            await scan_forever(
                template_iter=TemplateIterator(),
                out_queue=out_queue,
                stop_evt=stop_evt,
                device="cpu",
                threads=1,
                batch_size=10_000,
            )
        except Exception:
            pass

    scanner_task = asyncio.create_task(run_scanner())

    # Let scanner run for a moment
    await asyncio.sleep(0.5)
    stop_evt.set()

    try:
        await asyncio.wait_for(scanner_task, timeout=1.0)
    except asyncio.TimeoutError:
        pass

    # Verify scanner received all 3 template yields
    assert templates_yielded == 3, (
        f"Expected 3 template yields, got {templates_yielded}"
    )


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
