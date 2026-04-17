from __future__ import annotations

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.payouts import PoolPayoutScheduler


class StubMetrics:
    def __init__(self, budget: int) -> None:
        self._budget = int(budget)
        self.window_calls: list[float] = []
        self.due_calls: list[dict[str, int]] = []

    def mined_reward_in_window(self, *, window_seconds: float) -> int:
        self.window_calls.append(float(window_seconds))
        return self._budget

    def payout_due_addresses(
        self,
        *,
        min_amount: int,
        limit: int = 50,
        max_total_amount: int | None = None,
    ) -> list[dict[str, object]]:
        self.due_calls.append(
            {
                "min_amount": int(min_amount),
                "limit": int(limit),
                "max_total_amount": int(max_total_amount or 0),
            }
        )
        return []


def _config() -> PoolConfig:
    return PoolConfig(
        db_url="",
        payout_interval_seconds=60,
        payout_min_amount=10,
        payout_wallet="anim1poolwallet",
        pool_address="anim1poolwallet",
    )


def test_process_once_skips_due_lookup_when_window_budget_is_zero():
    metrics = StubMetrics(budget=0)
    scheduler = PoolPayoutScheduler(config=_config(), metrics=metrics)

    sent = scheduler._process_once()  # noqa: SLF001

    assert sent == 0
    assert metrics.window_calls == [60.0]
    assert metrics.due_calls == []


def test_process_once_limits_due_lookup_by_window_budget():
    metrics = StubMetrics(budget=250)
    scheduler = PoolPayoutScheduler(config=_config(), metrics=metrics)

    sent = scheduler._process_once()  # noqa: SLF001

    assert sent == 0
    assert metrics.window_calls == [60.0]
    assert metrics.due_calls == [
        {
            "min_amount": 10,
            "limit": 100,
            "max_total_amount": 250,
        }
    ]
