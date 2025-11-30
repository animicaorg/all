from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Dict

from aicf.queue.jobkind import JobKind

from ..jobkind import JobKind


class QuotaError(Exception): ...


@dataclass
class QuotaConfig:
    ai_units_per_epoch: int = 10**9
    quantum_units_per_epoch: int = 10**9
    max_concurrent: int = 1


@dataclass
class Usage:
    epoch: int
    ai_used: int = 0
    ai_reserved: int = 0
    quantum_used: int = 0
    quantum_reserved: int = 0
    concurrent: int = 0


@dataclass(frozen=True)
class Reservation:
    rid: int
    provider: str
    kind: JobKind
    epoch: int
    units: int


class QuotaTracker:
    def __init__(
        self,
        *,
        default_concurrent: int = 1,
        default_ai_units: int = 10**9,
        default_quantum_units: int = 10**9,
    ) -> None:
        self._lock = Lock()
        self._cfg: Dict[str, QuotaConfig] = {}
        self._u: Dict[tuple[str, int], Usage] = {}
        self._rid = 0
        self._default = QuotaConfig(
            default_ai_units, default_quantum_units, default_concurrent
        )

    def set_config(self, provider: str, cfg: QuotaConfig) -> None:
        with self._lock:
            self._cfg[provider] = cfg

    def get_config(self, provider: str) -> QuotaConfig:
        return self._cfg.get(provider, self._default)

    def _usage(self, provider: str, epoch: int) -> Usage:
        key = (provider, epoch)
        u = self._u.get(key)
        if u is None:
            u = Usage(epoch)
            self._u[key] = u
        return u

    def _next_id(self) -> int:
        self._rid += 1
        return self._rid

    def available(self, provider: str, epoch: int) -> dict:
        cfg = self.get_config(provider)
        u = self._usage(provider, epoch)
        return {
            "ai": max(0, cfg.ai_units_per_epoch - (u.ai_used + u.ai_reserved)),
            "quantum": max(
                0, cfg.quantum_units_per_epoch - (u.quantum_used + u.quantum_reserved)
            ),
            "concurrent": max(0, cfg.max_concurrent - u.concurrent),
        }

    def reserve(
        self, provider: str, kind: JobKind, epoch: int, units: int
    ) -> Reservation:
        if units <= 0:
            raise QuotaError("units must be > 0")
        cfg = self.get_config(provider)
        u = self._usage(provider, epoch)
        if u.concurrent >= cfg.max_concurrent:
            raise QuotaError("concurrent_exhausted")
        if kind is JobKind.AI:
            rem = cfg.ai_units_per_epoch - (u.ai_used + u.ai_reserved)
            if rem < units:
                raise QuotaError("ai_units_exhausted")
            u.ai_reserved += units
        elif kind is JobKind.QUANTUM:
            rem = cfg.quantum_units_per_epoch - (u.quantum_used + u.quantum_reserved)
            if rem < units:
                raise QuotaError("quantum_units_exhausted")
            u.quantum_reserved += units
        else:
            raise QuotaError("unknown_kind")
        u.concurrent += 1
        return Reservation(self._next_id(), provider, kind, epoch, units)

    def release(self, res: Reservation) -> None:
        u = self._usage(res.provider, res.epoch)
        u.concurrent = max(0, u.concurrent - 1)
        if res.kind is JobKind.AI:
            u.ai_reserved = max(0, u.ai_reserved - res.units)
        else:
            u.quantum_reserved = max(0, u.quantum_reserved - res.units)

    def commit(self, res: Reservation) -> None:
        u = self._usage(res.provider, res.epoch)
        u.concurrent = max(0, u.concurrent - 1)
        if res.kind is JobKind.AI:
            moved = min(res.units, u.ai_reserved)
            u.ai_reserved -= moved
            u.ai_used += moved
        else:
            moved = min(res.units, u.quantum_reserved)
            u.quantum_reserved -= moved
            u.quantum_used += moved

    def adjust_committed(self, res: Reservation, delta: int) -> None:
        u = self._usage(res.provider, res.epoch)
        if res.kind is JobKind.AI:
            u.ai_used = max(0, u.ai_used + delta)
        else:
            u.quantum_used = max(0, u.quantum_used + delta)
