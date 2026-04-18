from __future__ import annotations

import asyncio
import logging

from .engine import BridgeEngine
from .enums import BridgeStatus

log = logging.getLogger(__name__)


class BridgeWorker:
    def __init__(self, engine: BridgeEngine, poll_interval_seconds: float):
        self.engine = engine
        self.poll_interval_seconds = poll_interval_seconds
        self._running = False

    async def run_forever(self) -> None:
        self._running = True
        while self._running:
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001
                log.exception("bridge worker tick failed: %s", exc)
            await asyncio.sleep(self.poll_interval_seconds)

    def stop(self) -> None:
        self._running = False

    def run_once(self) -> None:
        expired = self.engine.expire_open_orders()
        if expired:
            log.info("expired %s bridge orders", expired)

        orders = self.engine.repo.orders_for_worker(
            statuses=[
                BridgeStatus.AWAITING_DEPOSIT,
                BridgeStatus.DEPOSIT_SEEN,
                BridgeStatus.CONFIRMING,
                BridgeStatus.CONFIRMED,
                BridgeStatus.READY_TO_SETTLE,
                BridgeStatus.SETTLEMENT_SUBMITTED,
                BridgeStatus.SETTLEMENT_CONFIRMED,
            ],
            limit=500,
        )
        for order in orders:
            self.engine.poll_order_progress(order)

