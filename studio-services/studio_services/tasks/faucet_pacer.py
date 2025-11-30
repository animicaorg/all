from __future__ import annotations

"""
Faucet pacer & retry helpers.

This module provides a lightweight, in-process pacing mechanism for the Faucet
service to guard against bursts and transient failures when sending drip
transactions. It does **not** run its own queue; instead, it exposes two
primitives that the faucet service (or router) can use:

- `get_pacer(app).acquire(address=...)` — await until a token is available,
  respecting a global token bucket and an optional per-address cooldown.

- `get_pacer(app).run_with_retry(coro_factory, ...)` — run an async operation
  (e.g., "send drip tx") with exponential backoff, jitter, and bounded attempts.

Design goals
------------
- Simple integration: call from `services.faucet` right before sending a drip.
- Global + per-address pacing: prevents hot-spot abuse and keeps a steady send rate.
- Structured logging for observability.
- No DB state: relies on time-based pacing; durable request tracking remains the
  responsibility of the HTTP layer and (optionally) the generic task queue.

Usage (inside services/faucet)
------------------------------
    pacer = get_pacer(app)
    await pacer.acquire(address=to_addr)

    async def _send():
        return await faucet_impl.send_drip(to_addr, amount)

    receipt = await pacer.run_with_retry(_send, op_name="faucet.drip", address=to_addr)

Configuration
-------------
Defaults are conservative. Override via `FaucetPacerConfig` when wiring your app
(e.g., in app factory) or set on `app.state.faucet_pacer = FaucetPacer(app, config=...)`.

Environment-based config is intentionally not handled here to keep concerns
separated; prefer centralizing env parsing in `config.py`.
"""

import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional

try:
    from ..logging import get_logger  # structured logger if available
except Exception:  # pragma: no cover
    get_logger = None  # type: ignore[assignment]


@dataclass(frozen=True)
class FaucetPacerConfig:
    # Token-bucket rate (tokens per second) and burst
    qps: float = 2.0  # average 2 drips/sec
    burst: int = 5  # allow short bursts up to 5
    # Per-address minimal interval (seconds). 0 disables per-address pacing.
    per_address_min_interval: float = 2.0
    # Retry/backoff
    max_attempts: int = 5
    base_backoff: float = 0.5  # seconds
    max_backoff: float = 8.0  # seconds
    jitter: float = 0.2  # +/- 20% jitter on the computed backoff


class _TokenBucket:
    """
    Simple token-bucket with monotonic-time refills.
    """

    def __init__(self, rate_per_sec: float, burst: int) -> None:
        self._rate = float(rate_per_sec)
        self._capacity = max(1.0, float(burst))
        self._tokens = self._capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def take(self) -> None:
        async with self._lock:
            now = time.monotonic()
            # Refill
            delta = max(0.0, now - self._last)
            self._last = now
            self._tokens = min(self._capacity, self._tokens + delta * self._rate)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            # Need to wait for deficit to fill to 1 token
            deficit = 1.0 - self._tokens
            wait_s = deficit / self._rate if self._rate > 0 else 0.0
        # Sleep outside the lock to avoid blocking other waiters
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        # After sleeping, try again (fast path will succeed)
        await self.take()


class FaucetPacer:
    """
    Global pacer bound to a FastAPI app instance.
    - Enforces a global token bucket (qps/burst).
    - Enforces an optional per-address minimal interval.
    - Provides a helper to run ops with retries/backoff.
    """

    def __init__(self, app, *, config: FaucetPacerConfig | None = None) -> None:
        self.app = app
        self.config = config or FaucetPacerConfig()
        self.bucket = _TokenBucket(self.config.qps, self.config.burst)
        self._addr_last_ts: Dict[str, float] = {}
        self._addr_lock = asyncio.Lock()
        # Logger
        if get_logger:
            self.log = get_logger(__name__).bind(role="faucet_pacer")  # type: ignore[attr-defined]
        else:  # pragma: no cover
            self.log = logging.getLogger(__name__)

    async def acquire(self, *, address: Optional[str] = None) -> None:
        """
        Await pacing quota: global token bucket + optional per-address cooldown.
        """
        # Global pacing
        await self.bucket.take()

        # Per-address minimal interval
        min_iv = self.config.per_address_min_interval
        if address and min_iv > 0:
            await self._enforce_per_address_interval(address, min_iv)

    async def _enforce_per_address_interval(
        self, address: str, min_interval: float
    ) -> None:
        now = time.monotonic()
        async with self._addr_lock:
            last = self._addr_last_ts.get(address)
            if last is None:
                self._addr_last_ts[address] = now
                return
            elapsed = now - last
            if elapsed >= min_interval:
                self._addr_last_ts[address] = now
                return
            wait_s = max(0.0, min_interval - elapsed)
            # Update last to "when we will actually send", to keep spacing
            self._addr_last_ts[address] = last + min_interval
        if wait_s > 0:
            await asyncio.sleep(wait_s)

    async def run_with_retry(
        self,
        op_coro_factory: Callable[[], Awaitable[object]],
        *,
        op_name: str,
        address: Optional[str] = None,
        max_attempts: Optional[int] = None,
    ):
        """
        Run an operation with exponential backoff & jitter on failure.

        Parameters
        ----------
        op_coro_factory : Callable[[], Awaitable[T]]
            A zero-arg coroutine factory that performs the drip (or another paced op).
        op_name : str
            Name for logging (e.g., "faucet.drip").
        address : Optional[str]
            Address associated with the operation for logging/metrics.
        max_attempts : Optional[int]
            Overrides config.max_attempts if provided.

        Returns
        -------
        The result of the last successful call.

        Raises
        ------
        The last exception after exhausting attempts.
        """
        attempts = int(
            max_attempts if max_attempts is not None else self.config.max_attempts
        )
        assert attempts >= 1

        for i in range(1, attempts + 1):
            try:
                if i > 1:
                    # Re-acquire pacing before each retry to avoid burst retries
                    await self.acquire(address=address)
                result = await op_coro_factory()
                if address:
                    self.log.info(
                        "pacer.success",
                        op=op_name,
                        address=address,
                        attempt=i,
                    )
                else:
                    self.log.info("pacer.success", op=op_name, attempt=i)
                return result
            except asyncio.CancelledError:
                self.log.warning(
                    "pacer.cancelled", op=op_name, attempt=i, address=address
                )
                raise
            except Exception as e:  # noqa: BLE001
                if i >= attempts:
                    self.log.exception(
                        "pacer.failed",
                        op=op_name,
                        address=address,
                        attempt=i,
                        error=f"{type(e).__name__}: {e}",
                    )
                    raise
                delay = self._compute_backoff(i)
                self.log.warning(
                    "pacer.retry",
                    op=op_name,
                    address=address,
                    attempt=i,
                    sleep_s=round(delay, 3),
                    error=f"{type(e).__name__}: {e}",
                )
                await asyncio.sleep(delay)
        # Unreachable, loop either returns or raises
        raise RuntimeError("pacer retry loop invariant broken")

    def _compute_backoff(self, attempt: int) -> float:
        """
        Exponential backoff with decorrelated jitter.
        """
        base = self.config.base_backoff
        cap = self.config.max_backoff
        # classic exponential growth
        raw = base * (2 ** (attempt - 1))
        raw = min(raw, cap)
        # apply symmetric jitter (e.g., 20% => uniform in [0.8, 1.2] * raw)
        jitter = self.config.jitter
        factor = 1.0 + random.uniform(-jitter, jitter) if jitter > 0 else 1.0
        return max(0.0, raw * factor)


# --- Accessor --------------------------------------------------------------


def get_pacer(app) -> FaucetPacer:
    """
    Get or create the FaucetPacer bound to the FastAPI app.
    """
    state = getattr(app, "state", None)
    if state is None:
        # Fallback to a process-global pacer (rare; mostly for tests)
        # but try to keep one per app if possible.
        if not hasattr(get_pacer, "_singleton"):
            setattr(get_pacer, "_singleton", FaucetPacer(app))
        return getattr(get_pacer, "_singleton")  # type: ignore[no-any-return]

    pacer: Optional[FaucetPacer] = getattr(state, "faucet_pacer", None)
    if pacer is None:
        pacer = FaucetPacer(app)
        setattr(state, "faucet_pacer", pacer)
    return pacer
