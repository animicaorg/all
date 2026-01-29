"""
execution.scheduler.merge — merge non-conflicting results; revert conflicted.

This module takes speculative execution outputs (with captured locksets) from an
optimistic scheduler and *deterministically* merges them back into the shared
state by **re-executing** the admissible subset in canonical order. Items whose
locksets conflict with already-accepted items are marked conflicted and *not*
applied. Any item that previously errored is also skipped.

Why re-execute?
---------------
Speculative executions are typically performed against forked snapshots. Rather
than trying to stitch low-level diffs, we re-run the same callables on the
authoritative state. The tx-level executor is responsible for journaling and
commit/rollback (see execution.state.journal / runtime.executor).

Usage (typical)
---------------
    from execution.scheduler.lockset import LockSet
    from execution.scheduler.merge import MergeItem, merge_speculative

    # Build MergeItem entries in canonical block order (idx ascending)
    items = [
        MergeItem(
            idx=i,
            lockset=tx_lockset,
            reexec=lambda fn=apply_tx, **kw: fn(**kw),   # freeze callable + kwargs
            reexec_kwargs=dict(
                tx=tx,
                state=state_view,
                block_env=block_env,
                gas_table=gas_table,
                params=params,
            ),
            tx_id=tx.hash_hex,   # optional for logs
            speculative_error=None,   # or the exception if your worker saw one
        )
        for i, (tx, tx_lockset) in enumerate(bundle)
    ]

    report = merge_speculative(items)

    for r in report.applied:
        print("applied", r.idx, r.tx_id, r.result.status)

    for r in report.skipped_conflict:
        print("conflict", r.idx, r.tx_id)

    for r in report.failed_reexec:
        print("reexec-failed", r.idx, r.tx_id, r.error)

Contracts
---------
• We never mutate the shared state for items that are conflicted or fail on
  re-execution (the per-tx executor must journal & rollback on error).
• Items are considered for application strictly in ascending `idx` order.
• Lockset conflicts are computed against the *accepted* prefix only.

"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .lockset import LockSet, merge_locksets

log = logging.getLogger(__name__)


# ------------------------------ Data Models ---------------------------------


@dataclass(frozen=True)
class MergeItem:
    """
    One speculative result to consider for merge.

    Fields:
        idx: Canonical order index (e.g., position in block candidate).
        lockset: Captured read/write set from speculative execution.
        reexec: Callable that *applies* this item to the authoritative state.
                It must return a tx ApplyResult-like object (opaque to merger).
        reexec_kwargs: Keyword args to pass to `reexec` when called.
        tx_id: Optional human identifier (hash hex / debug string).
        speculative_error: If set, this item is treated as failed and skipped.
    """

    idx: int
    lockset: LockSet
    reexec: Callable[..., Any]
    reexec_kwargs: Dict[str, Any] = field(default_factory=dict)
    tx_id: Optional[str] = None
    speculative_error: Optional[BaseException] = None


class MergeStatus(Enum):
    APPLIED = "applied"
    CONFLICT = "conflict"
    SPECULATIVE_ERROR = "speculative_error"
    REEXEC_ERROR = "reexec_error"


@dataclass
class MergeRecord:
    idx: int
    tx_id: Optional[str]
    status: MergeStatus
    result: Optional[Any] = None
    error: Optional[BaseException] = None


@dataclass
class MergeReport:
    """Summary of the merge pass."""

    applied: List[MergeRecord] = field(default_factory=list)
    skipped_conflict: List[MergeRecord] = field(default_factory=list)
    skipped_speculative_error: List[MergeRecord] = field(default_factory=list)
    failed_reexec: List[MergeRecord] = field(default_factory=list)

    @property
    def applied_count(self) -> int:
        return len(self.applied)

    @property
    def conflicted_count(self) -> int:
        return len(self.skipped_conflict)

    @property
    def failed_count(self) -> int:
        return len(self.failed_reexec) + len(self.skipped_speculative_error)

    def all_records(self) -> Iterable[MergeRecord]:
        yield from self.applied
        yield from self.skipped_conflict
        yield from self.skipped_speculative_error
        yield from self.failed_reexec


# ------------------------------ Core Logic ----------------------------------


def merge_speculative(items: Sequence[MergeItem]) -> MergeReport:
    """
    Merge speculative results into the shared state:

    1) Sort by idx (ascending).
    2) Walk the list; if item has `speculative_error`, mark as failed and skip.
    3) If item lockset conflicts with the union of previously *applied* locksets,
       mark as conflicted and skip.
    4) Otherwise, re-execute the item by calling `reexec(**reexec_kwargs)`.
       - On success: append to applied and union its lockset.
       - On error: record failure (REEXEC_ERROR) and do not union its lockset.

    Returns a MergeReport with per-item records. The authoritative state is
    mutated only via successful re-executions.

    Note: This function does *not* aggregate receipts or gas; that is left to
    the caller (inspect `record.result` for each applied item).
    """
    # Defensive: ensure deterministic iteration
    ordered = sorted(items, key=lambda it: it.idx)

    report = MergeReport()
    accepted_union = LockSet.empty()

    for it in ordered:
        tx_label = it.tx_id or f"tx#{it.idx}"
        if it.speculative_error is not None:
            log.debug(
                "merge: skip speculative-error idx=%s tx=%s err=%r",
                it.idx,
                tx_label,
                it.speculative_error,
            )
            report.skipped_speculative_error.append(
                MergeRecord(
                    idx=it.idx,
                    tx_id=it.tx_id,
                    status=MergeStatus.SPECULATIVE_ERROR,
                    error=it.speculative_error,
                )
            )
            continue

        # Conflict check against already-accepted union
        if it.lockset.conflicts_with(accepted_union):
            log.debug("merge: conflict idx=%s tx=%s (not applied)", it.idx, tx_label)
            report.skipped_conflict.append(
                MergeRecord(idx=it.idx, tx_id=it.tx_id, status=MergeStatus.CONFLICT)
            )
            continue

        # Re-execution on authoritative state
        try:
            res = it.reexec(**it.reexec_kwargs)
        except BaseException as e:
            log.warning("merge: reexec failed idx=%s tx=%s err=%r", it.idx, tx_label, e)
            report.failed_reexec.append(
                MergeRecord(
                    idx=it.idx, tx_id=it.tx_id, status=MergeStatus.REEXEC_ERROR, error=e
                )
            )
            # Do not union the lockset on failure.
            continue

        # Success: accept & union
        accepted_union = accepted_union.union(it.lockset)
        report.applied.append(
            MergeRecord(
                idx=it.idx, tx_id=it.tx_id, status=MergeStatus.APPLIED, result=res
            )
        )
        log.debug("merge: applied idx=%s tx=%s", it.idx, tx_label)

    return report


# ------------------------------ Helper Utils --------------------------------


def make_reexec(fn: Callable[..., Any], /, **kwargs: Any) -> Callable[[], Any]:
    """
    Freeze a callable + kwargs for later re-execution, avoiding late-binding
    pitfalls in loops:

        reexec = make_reexec(apply_tx, tx=tx, state=state, ...)
        result = reexec()

    This is a convenience helper; `MergeItem` can also carry (fn, kwargs)
    separately via fields `reexec` and `reexec_kwargs`.
    """

    def _runner() -> Any:
        return fn(**kwargs)

    return _runner


def summarize(report: MergeReport) -> str:
    """
    Human-friendly one-liner summary for logs.
    """
    return (
        f"merge: applied={report.applied_count} "
        f"conflicts={report.conflicted_count} "
        f"failed={report.failed_count}"
    )


__all__ = [
    "MergeItem",
    "MergeStatus",
    "MergeRecord",
    "MergeReport",
    "merge_speculative",
    "make_reexec",
    "summarize",
]
