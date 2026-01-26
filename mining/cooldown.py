from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("mining.cooldown")


@dataclass
class CooldownState:
    until: float = 0.0
    height: Optional[int] = None
    block_hash: Optional[str] = None


class BlockFoundCooldown:
    """
    Thread-safe cooldown gate for block-finding workers.

    notify_block_accepted() arms a cooldown window; await_if_cooling_down() waits
    for it to elapse (or stop signal).
    """

    def __init__(self, cooldown_sec: float = 60.0) -> None:
        self._cooldown_sec = max(1.0, float(cooldown_sec))
        self._lock = threading.Lock()
        self._state = CooldownState()

    def notify_block_accepted(
        self, *, height: Optional[int] = None, block_hash: Optional[str] = None
    ) -> None:
        now = time.monotonic()
        with self._lock:
            until = max(self._state.until, now + self._cooldown_sec)
            self._state.until = until
            if height is not None:
                self._state.height = int(height)
            if block_hash is not None:
                self._state.block_hash = str(block_hash)
        log.info(
            "Block found cooldown armed",
            extra={
                "until_s": round(until - now, 3),
                "height": height,
                "hash": block_hash,
            },
        )

    def remaining(self) -> float:
        with self._lock:
            remaining = self._state.until - time.monotonic()
        return max(0.0, remaining)

    def is_cooling_down(self) -> bool:
        return self.remaining() > 0.0

    async def await_if_cooling_down(
        self, stop_evt: Optional[asyncio.Event] = None
    ) -> None:
        remaining = self.remaining()
        if remaining <= 0.0:
            return
        try:
            if stop_evt is None:
                await asyncio.sleep(remaining)
                return
            await asyncio.wait_for(stop_evt.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            return

    def sleep_if_cooling_down(
        self, stop_evt: Optional[threading.Event] = None
    ) -> None:
        remaining = self.remaining()
        if remaining <= 0.0:
            return
        if stop_evt is None:
            time.sleep(remaining)
            return
        stop_evt.wait(timeout=remaining)


_DEFAULT_COOLDOWN: Optional[BlockFoundCooldown] = None


def get_block_found_cooldown() -> BlockFoundCooldown:
    global _DEFAULT_COOLDOWN
    if _DEFAULT_COOLDOWN is None:
        _DEFAULT_COOLDOWN = BlockFoundCooldown()
    return _DEFAULT_COOLDOWN
