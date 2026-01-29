from __future__ import annotations

import time

import pytest

from mining.cooldown import BlockFoundCooldown


@pytest.mark.asyncio
async def test_cooldown_waits_after_block_accept() -> None:
    cooldown = BlockFoundCooldown(cooldown_sec=0.2)
    cooldown.notify_block_accepted(height=10, block_hash="0xabc")
    start = time.monotonic()
    await cooldown.await_if_cooling_down()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.18


@pytest.mark.asyncio
async def test_cooldown_no_wait_when_idle() -> None:
    cooldown = BlockFoundCooldown(cooldown_sec=0.2)
    start = time.monotonic()
    await cooldown.await_if_cooling_down()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_cooldown_disabled_when_zero() -> None:
    """Test that cooldown is disabled when set to 0."""
    cooldown = BlockFoundCooldown(cooldown_sec=0.0)
    cooldown.notify_block_accepted(height=10, block_hash="0xabc")
    
    # Should not wait at all
    start = time.monotonic()
    await cooldown.await_if_cooling_down()
    elapsed = time.monotonic() - start
    
    # Should be nearly instant (no cooldown)
    assert elapsed < 0.05
    assert not cooldown.is_cooling_down()
    assert cooldown.remaining() == 0.0


@pytest.mark.asyncio
async def test_cooldown_allows_continuous_mining() -> None:
    """Test that with cooldown disabled, multiple blocks can be mined continuously."""
    cooldown = BlockFoundCooldown(cooldown_sec=0.0)
    
    # Simulate finding multiple blocks in quick succession
    for i in range(5):
        cooldown.notify_block_accepted(height=i, block_hash=f"0x{i:064x}")
        # Should never be cooling down
        assert not cooldown.is_cooling_down()
        await cooldown.await_if_cooling_down()
    
    # Total time should be negligible
    start = time.monotonic()
    for i in range(10):
        cooldown.notify_block_accepted(height=i, block_hash=f"0x{i:064x}")
        await cooldown.await_if_cooling_down()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1  # Should be very fast


@pytest.mark.asyncio
async def test_cooldown_negative_value_treated_as_zero() -> None:
    """Test that negative cooldown values are treated as 0 (disabled)."""
    cooldown = BlockFoundCooldown(cooldown_sec=-10.0)
    
    # Should be treated as 0 (disabled)
    cooldown.notify_block_accepted(height=100, block_hash="0xnegative")
    
    # Should not wait at all
    start = time.monotonic()
    await cooldown.await_if_cooling_down()
    elapsed = time.monotonic() - start
    
    # Should be nearly instant (no cooldown)
    assert elapsed < 0.05
    assert not cooldown.is_cooling_down()
    assert cooldown.remaining() == 0.0


@pytest.mark.asyncio
async def test_cooldown_resets_not_accumulates() -> None:
    """
    Test that cooldown resets to a fixed duration on each block, not accumulates.
    
    This is the regression test for the bug where cooldown would accumulate,
    causing mining to stop after 9-21 blocks as cooldown extended indefinitely.
    
    With the fix, each block should reset cooldown to the configured duration
    (e.g., 0.3s), not extend it further. After multiple rapid blocks, cooldown
    should still be at most the configured duration from the last block.
    """
    cooldown = BlockFoundCooldown(cooldown_sec=0.3)
    
    # Simulate finding multiple blocks in rapid succession
    # (simulates low difficulty scenario where blocks are found quickly)
    for i in range(10):
        cooldown.notify_block_accepted(height=i, block_hash=f"0x{i:064x}")
        # Small delay to simulate block processing time (but less than cooldown)
        time.sleep(0.05)
    
    # After 10 blocks with 0.3s cooldown each, the remaining cooldown should be
    # at most ~0.3s (from the last block), NOT 3.0s (10 * 0.3s accumulated)
    remaining = cooldown.remaining()
    assert remaining <= 0.35, f"Cooldown should reset, not accumulate. Got {remaining}s remaining"
    assert remaining >= 0.15, f"Cooldown should still be active after last block. Got {remaining}s"
    
    # Wait out the cooldown - should be quick (< 0.5s), not minutes
    start = time.monotonic()
    await cooldown.await_if_cooling_down()
    elapsed = time.monotonic() - start
    
    # Should complete in less than 0.5s (one cooldown period + buffer),
    # NOT multiple seconds (which would indicate accumulation)
    assert elapsed < 0.5, f"Cooldown should not accumulate. Waited {elapsed}s for 10 blocks"
    
    # After waiting, cooldown should be clear
    assert not cooldown.is_cooling_down()
    assert cooldown.remaining() == 0.0
