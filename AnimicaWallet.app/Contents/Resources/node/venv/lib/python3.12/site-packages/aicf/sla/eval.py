from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Thresholds:
    traps_min: float = 0.98
    qos_min: float = 0.90


def _ratio(ok: int, total: int) -> float:
    return 0.0 if total <= 0 else ok / float(total)


def evaluate(stats: dict, thresholds: Thresholds, conf: float = 0.95) -> dict:
    traps = _ratio(int(stats.get("traps_ok", 0)), int(stats.get("total", 0)))
    qos = _ratio(int(stats.get("qos_ok", 0)), int(stats.get("total", 0)))
    res_traps = traps + 1e-12 >= thresholds.traps_min
    res_qos = qos + 1e-12 >= thresholds.qos_min
    return {"traps": res_traps, "qos": res_qos, "overall": (res_traps and res_qos)}
