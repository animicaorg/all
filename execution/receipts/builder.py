"""
execution.receipts.builder — build a Receipt from an ApplyResult.

This module provides a single entrypoint:

    build_receipt(result: ApplyResult) -> Receipt

It lifts the essential fields (status, gas_used, logs) from an ApplyResult and,
when the target Receipt schema includes optional fields like `logs_bloom` or
`logs_root`, computes and fills them deterministically using helpers from
`execution.receipts.logs_hash`.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from execution.types.events import LogEvent
from execution.types.receipt import Receipt
from execution.types.result import ApplyResult

from .logs_hash import compute_logs_bloom, compute_logs_root


def _get_attr(obj: Any, *names: str, default: Any = None) -> Any:
    """Return the first present attribute in `names`, else `default`."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _ensure_logs(seq: Iterable[Any]) -> List[LogEvent]:
    """
    Ensure a list of LogEvent. We accept LogEvent instances and pass-through.
    If tuples/dicts ever appear upstream, adapt here (intentionally strict now).
    """
    out: List[LogEvent] = []
    for it in seq:
        if isinstance(it, LogEvent):
            out.append(it)
        else:
            raise TypeError(f"Unsupported log entry type: {type(it)!r}")
    return out


def build_receipt(result: ApplyResult) -> Receipt:
    """
    Build a `Receipt` from an `ApplyResult`.

    Required fields (always populated):
      - status
      - gas_used
      - logs

    Optional fields (populated iff present in Receipt dataclass):
      - logs_bloom  (bitset over topics & addresses)
      - logs_root   (Merkle/compact root over logs for indexing)

    Returns:
      Receipt instance ready for persistence and/or encoding.
    """
    # Read essentials (Handle possible camelCase from other layers)
    status = _get_attr(result, "status")
    gas_used = int(_get_attr(result, "gas_used", "gasUsed", default=0))
    logs_raw = _get_attr(result, "logs", default=()) or ()
    logs = _ensure_logs(logs_raw)

    # Prepare kwargs honoring the actual Receipt schema (minimal vs extended)
    fields: Dict[str, Any] = getattr(Receipt, "__dataclass_fields__", {})
    kwargs: Dict[str, Any] = {}

    if "status" in fields:
        kwargs["status"] = status
    if "gas_used" in fields:
        kwargs["gas_used"] = gas_used
    elif "gasUsed" in fields:  # tolerate camelCase schema
        kwargs["gasUsed"] = gas_used
    if "logs" in fields:
        kwargs["logs"] = logs

    # Optional enrichments
    if "logs_bloom" in fields:
        kwargs["logs_bloom"] = compute_logs_bloom(logs)
    if "logs_root" in fields:
        kwargs["logs_root"] = compute_logs_root(logs)

    return Receipt(**kwargs)  # type: ignore[arg-type]


__all__ = ["build_receipt"]
