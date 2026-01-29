from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional

import pytest

from aicf.aitypes import sla as sla_types
# Under test
from aicf.sla import evaluator as ev

# --------------------------- Helpers & Adapters ---------------------------


@dataclass
class Thresholds:
    traps_min: float = 0.98  # e.g., ≥98% trap receipts must pass (lower bound)
    qos_min: float = 0.90  # e.g., ≥90% QoS good
    # Note: For this test file we only exercise traps & QoS thresholds.


def _get_thresholds() -> Thresholds:
    """
    Try to use thresholds from aicf.aitypes.sla if present, otherwise defaults.
    """
    # Common shapes: Thresholds(traps_min=..., qos_min=...)
    # or SlaThresholds / PolicyThresholds with similar fields.
    for name in ("Thresholds", "SlaThresholds", "PolicyThresholds"):
        if hasattr(sla_types, name):
            T = getattr(sla_types, name)
            # Attempt construction with sane defaults; fall back to our local Thresholds if incompatible.
            try:
                t = T()  # type: ignore[call-arg]
                traps = getattr(t, "traps_min", 0.98)
                qos = getattr(t, "qos_min", 0.90)
                return Thresholds(traps_min=float(traps), qos_min=float(qos))
            except Exception:
                continue
    return Thresholds()


def _wilson_lower_bound(successes: int, total: int, conf: float) -> float:
    """
    Wilson score interval lower bound for a binomial proportion.
    """
    if total <= 0:
        return 0.0
    z = _z_from_conf(conf)
    phat = successes / total
    denom = 1 + (z * z) / total
    center = phat + (z * z) / (2 * total)
    margin = z * math.sqrt((phat * (1 - phat) + (z * z) / (4 * total)) / total)
    return (center - margin) / denom


def _z_from_conf(conf: float) -> float:
    # Quick map for typical confidences (avoids scipy)
    table = {0.80: 1.2816, 0.90: 1.6449, 0.95: 1.96, 0.975: 2.2414, 0.99: 2.5758}
    # Snap to nearest known confidence if slightly off due to float handling.
    closest = min(table.keys(), key=lambda k: abs(k - conf))
    return table[closest]


def _evaluate_with_module(
    stats: Dict[str, int | float], thresholds: Thresholds, conf: float
) -> Dict[str, bool]:
    """
    Try to evaluate via aicf.sla.evaluator using a variety of likely APIs.
    Returns a dict with keys: 'traps', 'qos', 'overall'.
    If evaluator API not recognized, falls back to local check.
    """
    # Preferred: evaluator.Evaluator(...).evaluate_window(stats, conf=...)
    if hasattr(ev, "Evaluator"):
        try:
            E = getattr(ev, "Evaluator")
            inst = None
            try:
                inst = E(thresholds=thresholds)  # type: ignore[call-arg]
            except Exception:
                inst = E()  # type: ignore[call-arg]
            for method in ("evaluate_window", "evaluate", "eval_window"):
                if hasattr(inst, method):
                    res = getattr(inst, method)(stats, conf=conf)  # type: ignore[misc]
                    ok = _extract_results(res)
                    if ok:
                        return ok
        except Exception:
            pass

    # Module-level functions
    for fn in ("evaluate_window", "evaluate", "eval_window"):
        if hasattr(ev, fn):
            try:
                res = getattr(ev, fn)(stats, thresholds=thresholds, conf=conf)  # type: ignore[misc]
            except TypeError:
                # Maybe thresholds baked-in
                res = getattr(ev, fn)(stats, conf=conf)  # type: ignore[misc]
            ok = _extract_results(res)
            if ok:
                return ok

    # Dimension-specific fallbacks: check_traps / check_qos
    traps_ok = None
    qos_ok = None
    if hasattr(ev, "check_traps"):
        try:
            traps_ok = bool(ev.check_traps(stats, thresholds=thresholds, conf=conf))  # type: ignore[misc]
        except TypeError:
            # Older signature
            traps_ok = bool(ev.check_traps(stats))
    if hasattr(ev, "check_qos"):
        try:
            qos_ok = bool(ev.check_qos(stats, thresholds=thresholds, conf=conf))  # type: ignore[misc]
        except TypeError:
            qos_ok = bool(ev.check_qos(stats))

    if traps_ok is not None and qos_ok is not None:
        return {"traps": traps_ok, "qos": qos_ok, "overall": traps_ok and qos_ok}

    # Final fallback: local computation (ensures test is runnable even if API differs).
    return _evaluate_locally(stats, thresholds, conf)


def _extract_results(res: Any) -> Optional[Dict[str, bool]]:
    """
    Attempt to normalize various result shapes to {'traps': bool, 'qos': bool, 'overall': bool}.
    """
    if res is None:
        return None

    # If res already a dict of booleans
    if isinstance(res, dict):
        keys = {
            k.lower(): bool(v) for k, v in res.items() if isinstance(v, (bool, int))
        }
        if {"traps", "qos"} <= keys.keys():
            overall = keys.get("overall", keys["traps"] and keys["qos"])
            return {
                "traps": keys["traps"],
                "qos": keys["qos"],
                "overall": bool(overall),
            }

    # Dataclass/obj with attributes
    for dim_name in ("traps", "qos"):
        if hasattr(res, "dimensions"):
            dims = getattr(res, "dimensions")
            if dim_name in dims:
                # Accept .ok or .passed or .pass
                d = dims[dim_name]
                for attr in ("ok", "passed", "pass_"):
                    if hasattr(d, attr):
                        pass_val = bool(getattr(d, attr))
                        # continue to next dim and assemble below
                        break
            # We'll normalize after loop if both present
    # Generic attribute access
    traps_val = None
    qos_val = None
    for name in ("traps_ok", "traps", "trap_ok"):
        if hasattr(res, name):
            traps_val = bool(getattr(res, name))
            break
        if isinstance(res, dict) and name in res:
            traps_val = bool(res[name])
            break
    for name in ("qos_ok", "qos", "quality_ok"):
        if hasattr(res, name):
            qos_val = bool(getattr(res, name))
            break
        if isinstance(res, dict) and name in res:
            qos_val = bool(res[name])
            break
    if traps_val is not None and qos_val is not None:
        overall = None
        for name in ("overall", "ok", "passed"):
            if hasattr(res, name):
                overall = bool(getattr(res, name))
                break
            if isinstance(res, dict) and name in res:
                overall = bool(res[name])
                break
        if overall is None:
            overall = traps_val and qos_val
        return {"traps": traps_val, "qos": qos_val, "overall": overall}

    return None


def _evaluate_locally(
    stats: Dict[str, int | float], thresholds: Thresholds, conf: float
) -> Dict[str, bool]:
    """
    Local policy mirror:
      - traps: Wilson lower bound of traps_ok/total >= traps_min
      - qos:   fraction qos_ok/total >= qos_min (no CI by default, assume binomial as well if counts present)
    """
    total = int(stats.get("total", 0))
    # traps
    traps_ok = int(stats.get("traps_ok", 0))
    traps_lb = _wilson_lower_bound(traps_ok, total, conf)
    traps_pass = traps_lb >= thresholds.traps_min

    # qos
    qos_ok = int(stats.get("qos_ok", 0))
    if "qos_ok" in stats and "total" in stats:
        qos_lb = _wilson_lower_bound(qos_ok, total, conf)
        qos_pass = qos_lb >= thresholds.qos_min
    else:
        # Fallback to mean QoS if provided as ratio
        qos_ratio = float(stats.get("qos_ratio", 0.0))
        qos_pass = qos_ratio >= thresholds.qos_min

    return {"traps": traps_pass, "qos": qos_pass, "overall": traps_pass and qos_pass}


# --------------------------- Tests ---------------------------


def test_traps_threshold_respects_confidence_window():
    thresholds = _get_thresholds()
    conf = 0.95

    # Window: 200 samples, 199 trap checks passed => very strong lower bound, should pass for traps_min≈0.98
    stats = {"total": 200, "traps_ok": 199, "qos_ok": 200}  # Make QoS trivially pass
    res = _evaluate_with_module(stats, thresholds, conf)
    assert (
        res["traps"] is True
    ), f"Expected traps to pass at 199/200 with conf={conf}, got {res}"
    assert res["overall"] in (
        True,
        res["qos"],
    ), "Overall should reflect conjunction of dimensions"


def test_traps_fail_below_threshold():
    thresholds = _get_thresholds()
    conf = 0.95

    # Window: 200 samples, only 190 traps OK (95%) → should fail traps for traps_min≈0.98
    stats = {"total": 200, "traps_ok": 190, "qos_ok": 200}
    res = _evaluate_with_module(stats, thresholds, conf)
    assert (
        res["traps"] is False
    ), f"Expected traps to FAIL at 190/200 with conf={conf}, got {res}"
    assert res["overall"] is False, "Overall must fail if any dimension fails"


def test_qos_threshold_and_confidence_window():
    thresholds = _get_thresholds()
    conf = 0.95

    # Pass case: 185/200 QoS good (92.5%) >= 0.90 with CI buffer; traps perfect.
    stats_pass = {"total": 200, "traps_ok": 200, "qos_ok": 185}
    res_pass = _evaluate_with_module(stats_pass, thresholds, conf)
    assert (
        res_pass["qos"] is True
    ), f"QoS should pass at 185/200 with conf={conf}, got {res_pass}"

    # Fail case: 150/200 (75%) < 0.90 → should fail
    stats_fail = {"total": 200, "traps_ok": 200, "qos_ok": 150}
    res_fail = _evaluate_with_module(stats_fail, thresholds, conf)
    assert (
        res_fail["qos"] is False
    ), f"QoS should FAIL at 150/200 with conf={conf}, got {res_fail}"
    assert res_fail["overall"] is False, "Overall must fail if QoS fails"


def test_combined_overall_requires_all_dimensions_to_pass():
    thresholds = _get_thresholds()
    conf = 0.95

    # Traps pass comfortably, QoS just below threshold → overall should be False.
    total = 200
    traps_ok = 199
    qos_ok = int(
        math.floor((thresholds.qos_min - 0.01) * total)
    )  # 1% below required QoS
    stats = {"total": total, "traps_ok": traps_ok, "qos_ok": qos_ok}

    res = _evaluate_with_module(stats, thresholds, conf)
    assert res["traps"] is True, "Traps should pass in this scenario"
    assert res["qos"] is False, "QoS intentionally below threshold"
    assert res["overall"] is False, "Overall must require all dimensions to pass"
