"""Schedules — declare a function should fire on a cron/period.

A schedule is pure metadata attached to a function at deploy time. The *firing*
is done by a scheduler service, not the SDK: in local/dev a ``animica studio
serve`` loop ticks it; in production the existing Celery beat in the
compute-platform queue-service (or a node-side cron) invokes the function. The
SDK only records the cadence so ``aicf.fn.deploy`` can persist it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cron:
    """A standard 5-field cron expression, e.g. ``Cron("0 * * * *")`` (hourly)."""

    expr: str

    def to_spec(self) -> dict:
        return {"type": "cron", "expr": self.expr}


@dataclass(frozen=True)
class Period:
    """A fixed interval, e.g. ``Period(hours=6)``."""

    seconds: int = 0
    minutes: int = 0
    hours: int = 0
    days: int = 0

    @property
    def total_seconds(self) -> int:
        return self.seconds + self.minutes * 60 + self.hours * 3600 + self.days * 86400

    def to_spec(self) -> dict:
        return {"type": "period", "seconds": self.total_seconds}
