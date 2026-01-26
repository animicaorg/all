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
