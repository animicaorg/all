from __future__ import annotations

import asyncio
import logging
import os
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
    
    The cooldown can be configured via ANIMICA_MINING_BLOCK_COOLDOWN_SEC environment
    variable. Setting it to 0 disables the cooldown entirely, allowing continuous mining.
    
    Default behavior (60s cooldown) is preserved for backwards compatibility when the
    environment variable is not set.
    """

    def __init__(self, cooldown_sec: float = 60.0) -> None:
        # Allow override via environment variable for smooth mining across nodes
        # Setting to 0 disables cooldown, allowing continuous mining
        self._cooldown_sec = max(0.0, float(cooldown_sec))
        self._lock = threading.Lock()
        self._state = CooldownState()

    def notify_block_accepted(
        self, *, height: Optional[int] = None, block_hash: Optional[str] = None
    ) -> None:
        now = time.monotonic()
        with self._lock:
            # If cooldown is disabled (0), don't pause mining
            if self._cooldown_sec <= 0.0:
                self._state.until = now  # No cooldown
                if height is not None:
                    self._state.height = int(height)
                if block_hash is not None:
                    self._state.block_hash = str(block_hash)
                log.debug(
                    "Block found, cooldown disabled - continuing mining immediately",
                    extra={"height": height, "hash": block_hash},
                )
                return
            
            # Fixed: Reset cooldown instead of accumulating to prevent mining from stopping
            # Previously: until = max(self._state.until, now + self._cooldown_sec)
            # This caused cooldown to accumulate across multiple blocks, eventually stopping mining
            until = now + self._cooldown_sec
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
    """
    Get or create the singleton BlockFoundCooldown instance.
    
    The cooldown duration can be configured via the ANIMICA_MINING_BLOCK_COOLDOWN_SEC
    environment variable:
    - Default (not set): 60 seconds (legacy behavior, backwards compatible)
    - Set to 0: Disables cooldown entirely for smooth continuous mining
    - Set to N: Uses N seconds cooldown after each block found
    
    This allows smooth mining behavior across multiple nodes without the "turn-based"
    feeling caused by long cooldowns.
    """
    global _DEFAULT_COOLDOWN
    if _DEFAULT_COOLDOWN is None:
        # Read from environment, default to 60 for backwards compatibility
        try:
            cooldown_sec = float(os.getenv("ANIMICA_MINING_BLOCK_COOLDOWN_SEC", "60.0"))
        except (ValueError, TypeError):
            log.warning(
                "Invalid ANIMICA_MINING_BLOCK_COOLDOWN_SEC value, using default 60.0 seconds"
            )
            cooldown_sec = 60.0
        
        # Ensure non-negative
        cooldown_sec = max(0.0, cooldown_sec)
        
        _DEFAULT_COOLDOWN = BlockFoundCooldown(cooldown_sec=cooldown_sec)
        if cooldown_sec <= 0.0:
            log.info("Block found cooldown is DISABLED - mining will be continuous")
        else:
            log.info(
                "Block found cooldown configured to %.1f seconds", cooldown_sec
            )
    return _DEFAULT_COOLDOWN
