"""
execution.scheduler.optimistic — optimistic-parallel prototype with conflict detection.

Goal
----
Speed up block application by speculatively executing multiple transactions in
parallel on *isolated* state views, then deterministically merging non-conflicting
results back into the canonical state.

Safety-first design:
- If the running state implementation does NOT expose a safe snapshot/fork+diff API,
  this scheduler transparently falls back to the canonical SerialScheduler.
- Determinism is preserved by committing accepted results strictly in input order.
- Conflict detection uses read/write locksets when available; absent locksets,
  proposals are conservatively re-run serially.

Expected (optional) host APIs
-----------------------------
State MAY provide any of the following surfaces; the scheduler will auto-detect:

1) Snapshot/merge:
   - fork() -> StateView
   - or clone() / copy() -> StateView
   - or snapshot_view() -> StateView
   The forked view SHOULD be isolated and support diff() or diff_since(base)
   and the canonical state SHOULD support apply_diff(diff).

   Minimal methods this module tries:
     view.diff() -> DiffObject
     state.apply_diff(DiffObject) -> None

2) Access tracking (for locksets):
   - execution.state.access_tracker exposes a context that records reads/writes; OR
   - the forked view exposes get_lockset() -> (reads, writes)

If none of the above exist, we serialize.

Notes
-----
This is a *prototype*. It is engineered to be correct and deterministic first.
Throughput gains depend on the underlying state's ability to fork cheaply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence, Tuple, List, Set
import concurrent.futures as _futures

try:
    from .serial import SerialScheduler, BlockApplyReport  # type: ignore
except Exception:  # pragma: no cover - minimal shells for isolated use
    from dataclasses import dataclass as _dc  # type: ignore

    class SerialScheduler:  # type: ignore
        def __init__(self, **_: Any) -> None: ...
        def apply_block(self, **kwargs: Any) -> Any:
            raise RuntimeError("SerialScheduler not available")

    @_dc
    class BlockApplyReport:  # type: ignore
        results: list
        gas_used_total: int
        success_count: int
        failure_count: int
        post_state_root: Optional[bytes]

try:
    from ..types.result import ApplyResult  # type: ignore
except Exception:  # pragma: no cover
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


# ------------------------------ Lockset model -------------------------------

Key = Tuple[bytes, Optional[bytes]]
# Convention: Key = (address, storage_key_or_None). For balance/nonce/code-level
# writes, storage_key_or_None can be None.

@dataclass(frozen=True)
class LockSet:
    reads: Set[Key]
    writes: Set[Key]

    @staticmethod
    def empty() -> "LockSet":
        return LockSet(reads=set(), writes=set())

    def conflicts_with(self, other: "LockSet") -> bool:
        # R/W conflicts in either direction, and W/W conflicts.
        if self.writes & other.writes:
            return True
        if self.writes & other.reads:
            return True
        if self.reads & other.writes:
            return True
        return False

    def union(self, other: "LockSet") -> "LockSet":
        return LockSet(reads=self.reads | other.reads, writes=self.writes | other.writes)


# ------------------------------ Proposals -----------------------------------

@dataclass
class Proposal:
    idx: int
    tx: Any
    result: Optional[ApplyResult]
    lockset: Optional[LockSet]
    diff: Any  # opaque diff object
    ok: bool
    error: Optional[BaseException] = None


# --------------------------- Helper predicates ------------------------------

def _is_success(res: ApplyResult) -> bool:
    try:
        return res.status == TxStatus.SUCCESS  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        return bool(getattr(res, "status", 1) == 1)


# ---------------------------- Dynamic imports -------------------------------

def _apply_tx_fn():
    from ..runtime.executor import apply_tx  # type: ignore
    return apply_tx


# Try to import access tracker if present
def _get_access_tracker():
    try:
        from ..state.access_tracker import track_accesses  # type: ignore
        return track_accesses
    except Exception:
        return None


# ---------------------------- State capabilities ----------------------------

def _fork_state(state: Any) -> Optional[Any]:
    for name in ("fork", "clone", "copy", "snapshot_view"):
        fn = getattr(state, name, None)
        if callable(fn):
            try:
                view = fn()
                return view
            except Exception:
                continue
    return None


def _get_diff(view: Any) -> Optional[Any]:
    for name in ("diff", "diff_since"):
        fn = getattr(view, name, None)
        if callable(fn):
            try:
                # Prefer zero-arg diff(); if diff_since requires a base, the view
                # is expected to capture its own base at construction.
                if fn.__code__.co_argcount == 1:  # type: ignore[attr-defined]
                    return fn()
                else:
                    return fn(None)  # type: ignore[misc]
            except Exception:
                continue
    return None


def _apply_diff(state: Any, diff: Any) -> bool:
    fn = getattr(state, "apply_diff", None)
    if not callable(fn):
        return False
    try:
        fn(diff)
        return True
    except Exception:
        return False


def _extract_lockset_from_view(view: Any) -> Optional[LockSet]:
    # Preferred method: view.get_lockset() -> (reads, writes)
    fn = getattr(view, "get_lockset", None)
    if callable(fn):
        try:
            reads, writes = fn()
            return LockSet(reads=set(reads), writes=set(writes))
        except Exception:
            pass
    return None


def _extract_lockset_from_receipt(res: ApplyResult) -> Optional[LockSet]:
    # If ApplyResult.receipt contains an accessList: [(addr, [keys...]), ...]
    try:
        rc = res.receipt or {}
        acc = rc.get("accessList")
        if not acc:
            return None
        reads: Set[Key] = set()
        writes: Set[Key] = set()
        # Conservative default: treat listed slots as writes (most impactful)
        for ent in acc:
            addr = ent["address"] if isinstance(ent, dict) else ent[0]
            slots = ent["storageKeys"] if isinstance(ent, dict) else ent[1]
            a = addr if isinstance(addr, (bytes, bytearray)) else bytes.fromhex(addr[2:]) if isinstance(addr, str) and addr.startswith("0x") else bytes(addr)
            if not slots:
                writes.add((a, None))
            else:
                for s in slots:
                    if isinstance(s, (bytes, bytearray)):
                        sk = bytes(s)
                    elif isinstance(s, str) and s.startswith("0x"):
                        sk = bytes.fromhex(s[2:])
                    else:
                        sk = bytes(s)
                    writes.add((a, sk))
        return LockSet(reads=reads, writes=writes)
    except Exception:
        return None


def _extract_lockset_with_tracker(view: Any, track_accesses, fn, **kwargs) -> Tuple[Optional[ApplyResult], Optional[LockSet], Optional[BaseException]]:
    try:
        with track_accesses(view) as tr:  # type: ignore[misc]
            res = fn(**kwargs)
        rds, wrs = tr.reads_writes()  # type: ignore[attr-defined]
        return res, LockSet(reads=set(rds), writes=set(wrs)), None
    except BaseException as e:
        return None, None, e


# ---------------------------- OptimisticScheduler ---------------------------

class OptimisticScheduler:
    """
    Optimistic-parallel prototype.

    Parameters
    ----------
    max_workers : int
        Size of thread pool for speculative executions. Defaults to min(8, cpu_count()).
    wave_size : int
        Number of transactions to speculate per wave. Defaults to 32.
    stop_on_error : bool
        If True, abort scheduling after the first failed transaction is *committed*
        (failures during speculation are just marked and retried serially).
    logger : logging.Logger | Callable[[str], None] | None
        Optional logger for debug/warning messages.
    """

    def __init__(
        self,
        *,
        max_workers: Optional[int] = None,
        wave_size: int = 32,
        stop_on_error: bool = False,
        logger: Optional[Any] = None,
    ) -> None:
        import os
        self._workers = max_workers or min(8, max(2, os.cpu_count() or 4))
        self._wave = max(1, wave_size)
        self._stop_on_error = stop_on_error
        self._log = logger
        self._apply_tx = _apply_tx_fn()
        self._track_accesses = _get_access_tracker()

    # ----------------------------------------------------------------------

    def apply_block(
        self,
        *,
        txs: Sequence[Any],
        state: Any,
        block_env: Any,
        gas_table: Any,
        params: Optional[Any] = None,
    ) -> BlockApplyReport:
        # If we cannot fork AND we cannot diff/apply, fall back to serial safely.
        if not self._can_speculate_on(state):
            self._warn("[optimistic] state does not support fork+diff; falling back to serial")
            return SerialScheduler(stop_on_error=self._stop_on_error, logger=self._log).apply_block(
                txs=txs, state=state, block_env=block_env, gas_table=gas_table, params=params
            )

        # Results are accumulated per original index.
        final_results: List[Optional[ApplyResult]] = [None] * len(txs)
        gas_total = 0
        succ = 0
        fail = 0
        last_root: Optional[bytes] = None

        # Accumulated lockset of everything we've already committed.
        committed_ls = LockSet.empty()

        # Transactions that must be retried serially (no lockset or merge unsupported).
        retry_serial: List[Tuple[int, Any]] = []

        # Process in deterministic waves
        for start in range(0, len(txs), self._wave):
            end = min(len(txs), start + self._wave)
            wave_items = list(enumerate(txs[start:end], start))
            proposals = self._speculate_wave(wave_items, state, block_env, gas_table, params)

            # Deterministically accept non-conflicting proposals in input order
            accept: List[Proposal] = []
            wave_ls = LockSet.empty()

            for prop in proposals:
                if not prop.ok or not prop.result:
                    # Failed speculation: push for serial retry
                    retry_serial.append((prop.idx, prop.tx))
                    continue
                # Derive lockset if missing (try result receipt)
                ls = prop.lockset or _extract_lockset_from_receipt(prop.result)
                if ls is None:
                    retry_serial.append((prop.idx, prop.tx))
                    continue
                # Check conflicts against already-accepted in wave and committed set
                if ls.conflicts_with(wave_ls) or ls.conflicts_with(committed_ls):
                    # Defer to later (next wave or serial); keep order by retrying serially
                    retry_serial.append((prop.idx, prop.tx))
                    continue
                accept.append(Proposal(prop.idx, prop.tx, prop.result, ls, prop.diff, True))

                # Extend wave lockset for subsequent checks
                wave_ls = wave_ls.union(ls)

            # Commit accepted proposals in input order
            for prop in accept:
                if prop.diff is not None:
                    if not _apply_diff(state, prop.diff):
                        # Merge path unavailable; fall back to serial
                        retry_serial.append((prop.idx, prop.tx))
                        continue
                # Successful merge: record result
                res = prop.result  # type: ignore[assignment]
                final_results[prop.idx] = res
                committed_ls = committed_ls.union(prop.lockset or LockSet.empty())  # type: ignore[arg-type]
                if _is_success(res):  # type: ignore[arg-type]
                    succ += 1
                    gas_total += int(res.gasUsed or 0)  # type: ignore[union-attr]
                    last_root = res.stateRoot or last_root  # type: ignore[union-attr]
                else:
                    fail += 1
                    if self._stop_on_error:
                        self._warn(f"[optimistic] stop_on_error=True; halting after tx#{prop.idx}")
                        # Fill still-empty slots with a benign failure result
                        for i, r in enumerate(final_results):
                            if r is None:
                                final_results[i] = ApplyResult(status=TxStatus.REVERT, gasUsed=0, logs=[])  # type: ignore[attr-defined]
                        return BlockApplyReport(
                            results=[r for r in final_results if r is not None],
                            gas_used_total=gas_total,
                            success_count=succ,
                            failure_count=fail,
                            post_state_root=last_root,
                        )

        # Retry any remaining txs serially in original order
        if retry_serial:
            self._dbg(f"[optimistic] serial fallback for {len(retry_serial)} txs")
            # Sort by original index to preserve determinism
            retry_serial.sort(key=lambda t: t[0])
            serial = SerialScheduler(stop_on_error=self._stop_on_error, logger=self._log)
            # Build a small adapter: run only these txs (in order) and patch into final_results
            sub_txs = [tx for _, tx in retry_serial]
            report = serial.apply_block(txs=sub_txs, state=state, block_env=block_env, gas_table=gas_table, params=params)
            # Stitch back results
            for (idx, _), res in zip(retry_serial, report.results):
                final_results[idx] = res
            # Update aggregates
            gas_total += report.gas_used_total
            succ += report.success_count
            fail += report.failure_count
            last_root = report.post_state_root or last_root

        # Finalize result list (should be fully populated)
        results_out: List[ApplyResult] = []
        for i, r in enumerate(final_results):
            if r is None:
                # Should not happen; be conservative
                r = ApplyResult(status=TxStatus.REVERT, gasUsed=0, logs=[])  # type: ignore[attr-defined]
                fail += 1
            results_out.append(r)

        return BlockApplyReport(
            results=results_out,
            gas_used_total=gas_total,
            success_count=succ,
            failure_count=fail,
            post_state_root=last_root,
        )

    # ----------------------------------------------------------------------

    def _speculate_wave(
        self,
        wave_items: List[Tuple[int, Any]],
        state: Any,
        block_env: Any,
        gas_table: Any,
        params: Optional[Any],
    ) -> List[Proposal]:
        """
        Run a wave of speculative executions on forked state views.
        Returns proposals in the same order as wave_items.
        """
        proposals: List[Proposal] = [Proposal(idx=i, tx=tx, result=None, lockset=None, diff=None, ok=False) for i, tx in wave_items]

        def job(i: int, tx: Any) -> Proposal:
            view = _fork_state(state)
            if view is None:
                return Proposal(idx=i, tx=tx, result=None, lockset=None, diff=None, ok=False)

            # Prefer access-tracker if available to capture a precise lockset.
            track_accesses = self._track_accesses
            apply_tx = self._apply_tx

            if track_accesses is not None:
                res, ls, err = _extract_lockset_with_tracker(
                    view,
                    track_accesses,
                    apply_tx,
                    tx=tx,
                    state=view,
                    block_env=block_env,
                    gas_table=gas_table,
                    params=params,
                )
                if err is not None:
                    return Proposal(idx=i, tx=tx, result=None, lockset=None, diff=None, ok=False, error=err)
                diff = _get_diff(view)
                return Proposal(idx=i, tx=tx, result=res, lockset=ls, diff=diff, ok=res is not None)

            # Else run normally and try to infer lockset from the view or receipt
            try:
                res = apply_tx(tx=tx, state=view, block_env=block_env, gas_table=gas_table, params=params)
            except BaseException as e:
                return Proposal(idx=i, tx=tx, result=None, lockset=None, diff=None, ok=False, error=e)

            ls = _extract_lockset_from_view(view) or _extract_lockset_from_receipt(res)
            diff = _get_diff(view)
            return Proposal(idx=i, tx=tx, result=res, lockset=ls, diff=diff, ok=True)

        # Thread pool with deterministic join order
        with _futures.ThreadPoolExecutor(max_workers=self._workers) as ex:
            futs = [ex.submit(job, i, tx) for i, tx in wave_items]
            # Collect in submission order to preserve determinism
            collected = [fut.result() for fut in futs]

        # Assign back preserving original indexes
        idx_to_pos = {i: pos for pos, (i, _) in enumerate(wave_items)}
        for prop in collected:
            proposals[idx_to_pos[prop.idx]] = prop
        return proposals

    # ----------------------------------------------------------------------

    def _can_speculate_on(self, state: Any) -> bool:
        # We need both fork() and apply_diff() (via diffs from fork views),
        # or at least fork() and view.diff() to attempt merges.
        view = _fork_state(state)
        if view is None:
            return False
        if _get_diff(view) is None:
            return False
        fn = getattr(state, "apply_diff", None)
        return callable(fn)

    # ----------------------------- Logging ----------------------------------

    def _emit(self, level: str, msg: str) -> None:
        if not self._log:
            return
        fn = getattr(self._log, level, None)
        if callable(fn):
            try:
                fn(msg)
                return
            except Exception:  # pragma: no cover
                pass
        try:
            self._log(msg)  # type: ignore[call-arg]
        except Exception:
            pass

    def _dbg(self, msg: str) -> None:
        self._emit("debug", msg)

    def _warn(self, msg: str) -> None:
        self._emit("warning", msg)


__all__ = ["OptimisticScheduler", "LockSet", "Proposal"]
