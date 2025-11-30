from __future__ import annotations

"""
AICF slashing & clawback rules.

This module defines deterministic, policy-driven penalties for provider
misbehavior or SLA breaches. It computes (a) an immediate slash from stake
and (b) a scheduled clawback of recent earnings over a number of epochs.

Design goals
------------
- Pure & deterministic: No IO, all integer math, stable ordering.
- Policy-driven: Rules are configured per reason code (string keys).
- Severity-aware: Penalties scale with a 0..1 severity factor.
- Guard rails: Penalties are clipped to available stake/earnings.
- Schedules: Clawback is emitted as epoch-indexed tranches.

Terminology
-----------
- *Immediate slash*: seized instantly from the provider's bonded stake.
- *Clawback*: scheduled deductions from current/next-epochs earnings.

Inputs
------
- reason_code: str, e.g. "fraud_proof", "invalid_attestation", "deadline_miss".
  (Keep aligned with aicf.registry.penalties reason codes if you use that module.)
- severity: float in [0,1], or basis points (int) if you prefer integers.
- stake_balance: current bonded stake (int, smallest unit).
- recent_earnings: sum of provider's earnings over a lookback window (int).
  This bounds the maximum clawback the scheduler will attempt to recover.
- epoch_idx: the current accounting epoch index (int).

Outputs
-------
- SlashPlan: immediate amount, total clawback amount, and a list of
  ClawbackTranche(epoch, amount) entries.

This module does not mutate any external state.
"""


from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

BPS_DEN = 10_000  # basis points denominator (100.00%)

# ------------------------------- Data types --------------------------------- #


@dataclass(frozen=True)
class ClawbackRule:
    """
    Policy for a given reason code.

    All ratios are in basis points (bps) to keep integer math deterministic.
    Example: 2.5% -> 250 bps.
    """

    immediate_bps: int  # % of stake to slash immediately (scaled by severity)
    clawback_bps: int  # % of recent_earnings to claw back (scaled by severity)
    schedule_epochs: int  # how many future epochs to spread clawback over
    max_immediate_abs: Optional[int] = None  # absolute cap on immediate amount
    max_clawback_abs: Optional[int] = None  # absolute cap on total clawback


RuleTable = Mapping[str, ClawbackRule]


@dataclass(frozen=True)
class ClawbackTranche:
    epoch_idx: int  # target epoch for the deduction
    amount: int  # amount to recover in that epoch


@dataclass(frozen=True)
class SlashPlan:
    reason_code: str
    severity_bps: int
    immediate_slash: int
    clawback_total: int
    schedule: List[ClawbackTranche]


# --------------------------------- Defaults --------------------------------- #


def default_rule_table() -> Dict[str, ClawbackRule]:
    """
    A conservative default rule table. Projects should tune these.

    Notes:
    - 'fraud_proof' is treated as the most severe category.
    - SLA breaches scale with severity (e.g., degree of QoS shortfall).
    """
    return {
        # Deliberate fraud / forged outputs: hard slash.
        "fraud_proof": ClawbackRule(
            immediate_bps=10_000,  # up to 100% of stake (× severity)
            clawback_bps=10_000,  # up to 100% of recent earnings
            schedule_epochs=4,
        ),
        # Attestation invalid or traps failed: strong penalty.
        "invalid_attestation": ClawbackRule(
            immediate_bps=5_000,  # up to 50% of stake
            clawback_bps=5_000,  # up to 50% of recent earnings
            schedule_epochs=3,
        ),
        # Availability / liveness issues (offline, lease lost).
        "unavailable": ClawbackRule(
            immediate_bps=500,  # up to 5% of stake
            clawback_bps=2_000,  # up to 20% of recent earnings
            schedule_epochs=2,
        ),
        # Missed deadlines or QoS below threshold.
        "deadline_miss": ClawbackRule(
            immediate_bps=0,  # no stake slash by default
            clawback_bps=3_000,  # up to 30% of recent earnings
            schedule_epochs=1,
        ),
        # Duplicate / conflicting submissions (sloppy but not malicious).
        "double_submit": ClawbackRule(
            immediate_bps=1_000,  # up to 10% of stake
            clawback_bps=1_000,  # up to 10% of recent earnings
            schedule_epochs=2,
        ),
        # Safety net: minimal penalty when a reason is unclassified.
        "__default__": ClawbackRule(
            immediate_bps=0,
            clawback_bps=500,  # up to 5% of recent earnings
            schedule_epochs=1,
        ),
    }


# ------------------------------ Helper methods ------------------------------ #


def _to_bps(severity: float | int) -> int:
    """
    Convert severity to basis points. If already int, assume it's bps.
    """
    if isinstance(severity, int):
        if severity < 0:
            return 0
        return min(severity, BPS_DEN)
    # float path
    if severity <= 0.0:
        return 0
    if severity >= 1.0:
        return BPS_DEN
    # Avoid float drift: round half up to nearest bps
    return int(round(severity * BPS_DEN))


def _mul_clip(amount: int, bps: int, severity_bps: int) -> int:
    """
    Compute amount * (bps/10_000) * (severity_bps/10_000) with integer math.
    """
    if amount <= 0 or bps <= 0 or severity_bps <= 0:
        return 0
    # (amount * bps * severity_bps) / (BPS_DEN^2), ordered to reduce overflow risk.
    num = amount * bps
    num = num * severity_bps
    den = BPS_DEN * BPS_DEN
    return num // den


def _clip_cap(value: int, cap: Optional[int]) -> int:
    if cap is None:
        return value
    return min(value, max(cap, 0))


def _even_schedule(total: int, start_epoch: int, epochs: int) -> List[ClawbackTranche]:
    """
    Split `total` into `epochs` tranches, earliest-first, distributing any
    remainder to the earliest tranche to keep sum exact and deterministic.
    """
    if total <= 0 or epochs <= 0:
        return []
    base = total // epochs
    rem = total - base * epochs
    tranches: List[ClawbackTranche] = []
    for i in range(epochs):
        amt = base + (rem if i == 0 else 0)
        tranches.append(ClawbackTranche(epoch_idx=start_epoch + i + 1, amount=amt))
        rem = 0  # only the first tranche gets the remainder
    return tranches


# ------------------------------- Core logic --------------------------------- #


def compute_slash_plan(
    *,
    reason_code: str,
    severity: float | int,
    stake_balance: int,
    recent_earnings: int,
    epoch_idx: int,
    rules: Optional[RuleTable] = None,
    max_immediate_abs: Optional[int] = None,
    max_clawback_abs: Optional[int] = None,
) -> SlashPlan:
    """
    Compute a SlashPlan for a provider.

    Args:
        reason_code: policy key; falls back to "__default__" if missing.
        severity: float in [0,1] or int in [0,10_000] bps.
        stake_balance: current bonded stake (>=0).
        recent_earnings: bound for clawback (>=0), e.g., last N epochs' payouts.
        epoch_idx: current epoch index; tranches start at epoch_idx+1.
        rules: optional custom rule table; defaults to `default_rule_table()`.
        max_immediate_abs: optional absolute cap applied after policy caps.
        max_clawback_abs: optional absolute cap applied after policy caps.

    Returns:
        SlashPlan with immediate_slash, clawback_total, and schedule.
    """
    if rules is None:
        rules = default_rule_table()

    rule = rules.get(reason_code) or rules.get("__default__")
    if rule is None:
        # Shouldn't happen, but keep a tiny safe default
        rule = ClawbackRule(immediate_bps=0, clawback_bps=500, schedule_epochs=1)

    severity_bps = _to_bps(severity)

    # Immediate slash from stake
    raw_immediate = _mul_clip(stake_balance, rule.immediate_bps, severity_bps)
    raw_immediate = min(raw_immediate, stake_balance)  # cannot exceed stake
    raw_immediate = _clip_cap(raw_immediate, rule.max_immediate_abs)
    if max_immediate_abs is not None:
        raw_immediate = _clip_cap(raw_immediate, max_immediate_abs)

    # Clawback from recent earnings
    raw_clawback = _mul_clip(recent_earnings, rule.clawback_bps, severity_bps)
    raw_clawback = min(raw_clawback, recent_earnings)  # cannot exceed bound
    raw_clawback = _clip_cap(raw_clawback, rule.max_clawback_abs)
    if max_clawback_abs is not None:
        raw_clawback = _clip_cap(raw_clawback, max_clawback_abs)

    schedule = _even_schedule(
        raw_clawback, start_epoch=epoch_idx, epochs=max(rule.schedule_epochs, 1)
    )

    return SlashPlan(
        reason_code=reason_code,
        severity_bps=severity_bps,
        immediate_slash=raw_immediate,
        clawback_total=raw_clawback,
        schedule=schedule,
    )


# ---------------------------- SLA → severity helper ------------------------- #


def severity_from_sla(
    *,
    traps_ratio: Optional[float] = None,  # 0..1 (fraction of trap tests passed)
    qos_score: Optional[float] = None,  # 0..1 (1=best)
    latency_p99_ms: Optional[int] = None,  # absolute ms; compare to SLO
    availability: Optional[float] = None,  # 0..1 uptime over window
    slo_latency_ms: int = 2_000,  # target P99
) -> Tuple[str, float]:
    """
    Heuristic mapping from SLA metrics to (reason_code, severity).

    This is a conservative baseline suitable for devnets. Production policies
    should tune thresholds and mapping to fit economic assumptions.

    Returns:
        (reason_code, severity in 0..1)

    Rules (first match wins):
      - traps_ratio < 0.98  -> ("invalid_attestation", 1 - traps_ratio)
      - qos_score < 0.80    -> ("deadline_miss", 0.80 - qos_score)
      - latency_p99 > SLO   -> ("deadline_miss", min(1, (p99/SLO - 1)))
      - availability < 0.95 -> ("unavailable", 0.95 - availability)
      - else                -> ("__default__", 0.0)
    """
    if traps_ratio is not None and traps_ratio < 0.98:
        return "invalid_attestation", max(0.0, min(1.0, 1.0 - traps_ratio))

    if qos_score is not None and qos_score < 0.80:
        return "deadline_miss", max(0.0, min(1.0, 0.80 - qos_score))

    if latency_p99_ms is not None and latency_p99_ms > slo_latency_ms:
        over = (latency_p99_ms / float(slo_latency_ms)) - 1.0
        return "deadline_miss", max(0.0, min(1.0, over))

    if availability is not None and availability < 0.95:
        return "unavailable", max(0.0, min(1.0, 0.95 - availability))

    return "__default__", 0.0


# --------------------------------- Utilities -------------------------------- #


def summarize_plan(plan: SlashPlan) -> str:
    """
    Compact single-line summary appropriate for logs / audits.
    """
    parts = [
        f"reason={plan.reason_code}",
        f"severity_bps={plan.severity_bps}",
        f"immediate={plan.immediate_slash}",
        f"clawback={plan.clawback_total}",
        "schedule=["
        + ",".join(f"({t.epoch_idx},{t.amount})" for t in plan.schedule)
        + "]",
    ]
    return "SlashPlan{" + " ".join(parts) + "}"


__all__ = [
    "BPS_DEN",
    "ClawbackRule",
    "RuleTable",
    "ClawbackTranche",
    "SlashPlan",
    "default_rule_table",
    "compute_slash_plan",
    "severity_from_sla",
    "summarize_plan",
]
