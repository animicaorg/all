"""
execution.scheduler.serial — canonical serial executor (deterministic).

Applies transactions one-by-one in the given order with per-tx checkpoints so
failed transactions (REVERT/OOG/etc.) do not leak state mutations. This is the
canonical, spec-compliant scheduler used by tests and production unless an
optimistic/parallel scheduler is explicitly selected.

The scheduler delegates actual execution to `execution.runtime.executor.apply_tx`
and only handles ordering, checkpoint/commit/revert, and result aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

# --- Light type imports with fallbacks to avoid import-time cycles -------------------

try:
    from ..types.result import ApplyResult  # type: ignore
except Exception:  # pragma: no cover - fallback typing shell for isolated tests
    @dataclass
    class ApplyResult:  # type: ignore
        status: int
        gasUsed: int
        logs: list
        stateRoot: Optional[bytes] = None
        receipt: Optional[dict] = None

try:
    from ..types.status import TxStatus  # type: ignore
except Exception:  # pragma: no cover
    class TxStatus:  # type: ignore
        SUCCESS = 1
        REVERT = 2
        OOG = 3


# ------------------------------------------------------------------------------------
# Aggregated report
# ------------------------------------------------------------------------------------

@dataclass
class BlockApplyReport:
    """Summary returned by SerialScheduler.apply_block."""
    results: List[ApplyResult]
    gas_used_total: int
    success_count: int
    failure_count: int
    # If the runtime populates ApplyResult.stateRoot on each success,
    # we expose the last observed state root for convenience.
    post_state_root: Optional[bytes]


# ------------------------------------------------------------------------------------
# Utility helpers
# ------------------------------------------------------------------------------------

def _default_apply_tx() -> Callable[..., ApplyResult]:
    """
    Import on demand to avoid import-time cycles.
    """
    from ..runtime.executor import apply_tx  # type: ignore
    return apply_tx


def _has_journal_api(state: Any) -> bool:
    return all(hasattr(state, name) for name in ("checkpoint", "commit", "revert"))


def _is_success(status: int) -> bool:
    try:
        return status == TxStatus.SUCCESS  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        return status == 1


# ------------------------------------------------------------------------------------
# SerialScheduler
# ------------------------------------------------------------------------------------

class SerialScheduler:
    """
    Deterministic, single-threaded scheduler.

    Parameters
    ----------
    apply_tx_fn : Callable
        Optional override for the executor function. Defaults to
        `execution.runtime.executor.apply_tx`.
    stop_on_error : bool
        If True, stop at first failed transaction and return the partial report.
        Defaults to False.
    logger : Callable[[str], None] or logging.Logger (duck-typed)
        Optional logger; if provided, `debug/info/warning` attributes will be used
        when present, else it will be called as a function with the message.

    Notes
    -----
    * Uses per-transaction checkpoint/commit/revert if the provided `state`
      object implements `checkpoint()/commit()/revert(cp_id)`; otherwise assumes
      the underlying executor (`apply_tx`) encapsulates its own journaling.
    * Input order defines execution order. Stable and deterministic by design.
    """

    def __init__(
        self,
        *,
        apply_tx_fn: Optional[Callable[..., ApplyResult]] = None,
        stop_on_error: bool = False,
        logger: Optional[Any] = None,
    ) -> None:
        self._apply_tx = apply_tx_fn or _default_apply_tx()
        self._stop_on_error = stop_on_error
        self._log = logger

    # --------------------------------- API ----------------------------------

    def apply_block(
        self,
        *,
        txs: Sequence[Any],
        state: Any,
        block_env: Any,
        gas_table: Any,
        params: Optional[Any] = None,
    ) -> BlockApplyReport:
        """
        Apply a sequence of transactions serially.

        Parameters
        ----------
        txs : Sequence[Any]
            Transactions to apply in the exact order given.
        state : Any
            Mutable state object. If it exposes a journaling API
            (checkpoint/commit/revert), it will be used per transaction.
        block_env : Any
            Execution block context (timestamps, coinbase, base fee, etc.).
        gas_table : Any
            Gas cost table resolved for this block/network.
        params : Optional[Any]
            Optional chain/execution params passed through to apply_tx.

        Returns
        -------
        BlockApplyReport
        """
        results: List[ApplyResult] = []
        gas_total = 0
        succ = 0
        fail = 0
        last_root: Optional[bytes] = None

        use_journal = _has_journal_api(state)

        for idx, tx in enumerate(txs):
            cp_id = None
            if use_journal:
                cp_id = state.checkpoint()  # type: ignore[attr-defined]
                self._dbg(f"[serial] checkpoint opened for tx#{idx}")

            try:
                res: ApplyResult = self._apply_tx(
                    tx=tx,
                    state=state,
                    block_env=block_env,
                    gas_table=gas_table,
                    params=params,
                )
            except Exception as exc:
                # Any unexpected exception is treated as a hard failure for this tx.
                # Revert checkpoint if we opened one.
                if use_journal and cp_id is not None:
                    state.revert(cp_id)  # type: ignore[attr-defined]
                    self._warn(f"[serial] tx#{idx} raised {type(exc).__name__}; reverted checkpoint")
                results.append(ApplyResult(status=TxStatus.REVERT, gasUsed=0, logs=[]))  # type: ignore[attr-defined]
                fail += 1
                if self._stop_on_error:
                    self._warn(f"[serial] stop_on_error=True; halting at tx#{idx}")
                    break
                continue

            # Commit/revert based on status
            if _is_success(res.status):
                succ += 1
                gas_total += int(res.gasUsed or 0)
                last_root = res.stateRoot or last_root
                if use_journal and cp_id is not None:
                    state.commit(cp_id)  # type: ignore[attr-defined]
                    self._dbg(f"[serial] tx#{idx} SUCCESS — committed checkpoint")
            else:
                fail += 1
                if use_journal and cp_id is not None:
                    state.revert(cp_id)  # type: ignore[attr-defined]
                    self._dbg(f"[serial] tx#{idx} FAILED — reverted checkpoint")
                if self._stop_on_error:
                    results.append(res)
                    self._warn(f"[serial] stop_on_error=True; halting at tx#{idx}")
                    break

            results.append(res)

        return BlockApplyReport(
            results=results,
            gas_used_total=gas_total,
            success_count=succ,
            failure_count=fail,
            post_state_root=last_root,
        )

    def apply_stream(
        self,
        *,
        txs: Iterable[Any],
        state: Any,
        block_env: Any,
        gas_table: Any,
        params: Optional[Any] = None,
    ) -> Iterable[Tuple[int, ApplyResult]]:
        """
        Generator version: yields (index, ApplyResult) as transactions are applied.

        Honor per-tx checkpoints when available. Does not aggregate a summary;
        callers can fold over the stream if they need totals.
        """
        use_journal = _has_journal_api(state)
        for idx, tx in enumerate(txs):
            cp_id = None
            if use_journal:
                cp_id = state.checkpoint()  # type: ignore[attr-defined]
            try:
                res: ApplyResult = self._apply_tx(
                    tx=tx,
                    state=state,
                    block_env=block_env,
                    gas_table=gas_table,
                    params=params,
                )
            except Exception:
                if use_journal and cp_id is not None:
                    state.revert(cp_id)  # type: ignore[attr-defined]
                yield idx, ApplyResult(status=TxStatus.REVERT, gasUsed=0, logs=[])  # type: ignore[attr-defined]
                if self._stop_on_error:
                    return
                continue

            if _is_success(res.status):
                if use_journal and cp_id is not None:
                    state.commit(cp_id)  # type: ignore[attr-defined]
            else:
                if use_journal and cp_id is not None:
                    state.revert(cp_id)  # type: ignore[attr-defined]
                if self._stop_on_error:
                    yield idx, res
                    return
            yield idx, res

    # --------------------------------------------------------------------------------
    # Logging helpers (duck-typed logger or simple callable)
    # --------------------------------------------------------------------------------

    def _emit(self, level: str, msg: str) -> None:
        if not self._log:
            return
        # If it looks like a standard logger, use attribute; else call it.
        fn = getattr(self._log, level, None)
        if callable(fn):
            try:
                fn(msg)
                return
            except Exception:  # pragma: no cover - defensive
                pass
        try:
            self._log(msg)  # type: ignore[call-arg]
        except Exception:
            pass

    def _dbg(self, msg: str) -> None:
        self._emit("debug", msg)

    def _warn(self, msg: str) -> None:
        self._emit("warning", msg)


__all__ = ["SerialScheduler", "BlockApplyReport"]
