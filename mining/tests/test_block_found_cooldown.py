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
