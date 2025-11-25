"""
execution.runtime.executor — top-level orchestration for applying transactions and blocks.

Responsibilities
- apply_tx: one-transaction execution wrapper. Delegates to runtime.dispatcher.apply_tx
  (preferred) and falls back to a minimal dispatch if the dispatcher is not available.
- apply_block: sequentially applies a list/iterable of transactions to the evolving
  state for a given BlockEnv; aggregates per-tx ApplyResult and returns a BlockResult.

Design notes
- This module is deliberately light on policy and *does not* do signature checks or
  stateless validation; those are expected to run earlier (mempool or block import).
- Revert semantics are handled by the callee for each tx kind (transfer/deploy/call).
- Journaling: if a state journal is available (execution.state.journal), apply_block
  will open a single outer checkpoint and commit progressively, so partial effects of
  a REVERTing tx do not leak. If no journal is present, we still proceed deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from ..types.result import ApplyResult
from ..types.events import LogEvent

# Optional imports: dispatcher (preferred), journal (if present)
try:
    from .dispatcher import apply_tx as _dispatch_apply_tx  # type: ignore
except Exception:  # pragma: no cover - robust to partial trees
    _dispatch_apply_tx = None  # type: ignore[assignment]

try:
    # Journal API is intentionally duck-typed: must expose checkpoint(), revert(cp), commit(cp)
    from ..state.journal import Journal  # type: ignore
except Exception:  # pragma: no cover
    Journal = None  # type: ignore


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def _state_root(state: Any) -> bytes:
    """
    Best-effort way to obtain a 32-byte state root from a variety of state backends.
    """
    for name in ("compute_state_root", "state_root", "merkle_root"):
        fn = getattr(state, name, None)
        if callable(fn):
            try:
                root = fn()
                return _as_bytes32(root)
            except Exception:
                pass
        val = getattr(state, name, None)
        if isinstance(val, (bytes, bytearray, str)):
            return _as_bytes32(val)
    return b"\x00" * 32


def _as_bytes32(x: Any) -> bytes:
    if isinstance(x, (bytes, bytearray)):
        b = bytes(x)
        if len(b) == 32:
            return b
        return b[:32].rjust(32, b"\x00")
    if isinstance(x, str):
        s = x.strip()
        if s.startswith(("0x", "0X")):
            s = s[2:]
        if len(s) % 2:
            s = "0" + s
        try:
            b = bytes.fromhex(s)
            return b[:32].rjust(32, b"\x00")
        except ValueError:
            return b"\x00" * 32
    try:
        i = int(x)  # type: ignore[arg-type]
        if i < 0:
            i = 0
        l = max(1, (i.bit_length() + 7) // 8)
        b = i.to_bytes(l, "big")
        return b[-32:].rjust(32, b"\x00")
    except Exception:
        return b"\x00" * 32


# --------------------------------------------------------------------------------------
# Public result container for blocks
# --------------------------------------------------------------------------------------

@dataclass
class BlockResult:
    """
    Result of applying all txs in a block *in order*.
    """
    tx_results: List[ApplyResult]
    total_gas_used: int
    logs: List[LogEvent]
    state_root: bytes  # 32 bytes; best-effort view after last tx
    # Optional: per-tx receipts could be included inside ApplyResult.receipt if populated.


# --------------------------------------------------------------------------------------
# TX and Block application
# --------------------------------------------------------------------------------------

def apply_tx(
    tx: Any,
    state: Any,
    block_env: Any,
    *,
    params: Optional[Any] = None,
    tx_env: Optional[Any] = None,
) -> ApplyResult:
    """
    Apply a single transaction to the provided mutable state under the given BlockEnv.

    Args:
        tx: A decoded transaction object (dataclass or dict-like). The concrete shape
            is defined in core/types/tx.py and/or the runtime dispatcher.
        state: Mutable state handle (execution.state.* backend).
        block_env: Block execution environment (see execution.runtime.env.BlockEnv).
        params: Optional Chain/Execution params bundle for gas tables & limits.
        tx_env: Optional pre-constructed TxEnv (execution.runtime.env.TxEnv). If not
            supplied, the dispatcher is expected to derive one as needed.

    Returns:
        ApplyResult for the transaction.
    """
    # Preferred path: delegate to dispatcher (module-local import above)
    if _dispatch_apply_tx is not None:
        return _dispatch_apply_tx(tx, state, block_env, params=params, tx_env=tx_env)  # type: ignore[misc]

    # Fallback: very small built-in dispatcher based on tx.kind
    kind = getattr(tx, "kind", None) or (isinstance(tx, dict) and tx.get("kind"))
    if isinstance(kind, (bytes, bytearray)):
        kind = bytes(kind).decode(errors="ignore")
    if isinstance(kind, int):
        # 0=transfer, 1=deploy, 2=call (convention; may differ)
        kind = {0: "transfer", 1: "deploy", 2: "call"}.get(kind, "transfer")

    if kind == "transfer":
        from .transfers import apply_transfer  # lazy import; raises if missing
        return apply_transfer(tx, state, block_env, tx_env=tx_env, params=params)
    elif kind == "deploy":
        from .contracts import apply_deploy
        return apply_deploy(tx, state, block_env, tx_env=tx_env, params=params)
    elif kind == "call":
        from .contracts import apply_call
        return apply_call(tx, state, block_env, tx_env=tx_env, params=params)
    else:
        # Unknown kind — treat as REVERT with zero gas (defensive)
        from ..types.status import TxStatus
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=0,
            logs=[],
            state_root=_state_root(state),
            receipt=None,
        )


def apply_block(
    txs: Iterable[Any],
    state: Any,
    block_env: Any,
    *,
    params: Optional[Any] = None,
) -> BlockResult:
    """
    Apply a sequence of transactions *in order* to the given mutable state.

    - If a Journal is available, we open an outer checkpoint before the first tx and
      commit after each tx; a throwing tx is expected to return REVERT/OOG and not raise.
    - If no Journal is available, we still proceed sequentially; callers should ensure
      that per-tx handlers properly scope their own mutations.

    Returns:
        BlockResult containing per-tx results, cumulative gas used, concatenated logs,
        and the final state root (best-effort).
    """
    use_journal = Journal is not None and hasattr(state, "__class__")
    jh = None
    if use_journal:
        try:
            jh = Journal(state)  # type: ignore[call-arg]
        except Exception:
            jh = None
            use_journal = False

    tx_results: List[ApplyResult] = []
    all_logs: List[LogEvent] = []
    total_gas = 0

    # If journaling is available, create a top-level checkpoint encompassing the block
    outer_cp = None
    if use_journal and jh is not None:
        try:
            outer_cp = jh.checkpoint()
        except Exception:
            outer_cp = None
            use_journal = False

    for tx in txs:
        inner_cp = None
        if use_journal and jh is not None:
            try:
                inner_cp = jh.checkpoint()
            except Exception:
                inner_cp = None

        try:
            res = apply_tx(tx, state, block_env, params=params)
        except Exception as exc:
            # Defensive: transform unexpected exceptions into a REVERT result
            from ..types.status import TxStatus
            res = ApplyResult(
                status=TxStatus.REVERT,
                gas_used=0,
                logs=[LogEvent(address=b"\x00" * 20, topics=[b"executor.error"], data=str(exc).encode())],
                state_root=_state_root(state),
                receipt=None,
            )

        tx_results.append(res)
        total_gas += int(getattr(res, "gas_used", 0) or 0)
        logs = getattr(res, "logs", []) or []
        if isinstance(logs, list):
            all_logs.extend(logs)

        # Revert partial state if the tx failed with OOG (hard) or if callee indicated rollback.
        status_name = getattr(getattr(res, "status", None), "name", None) or str(getattr(res, "status", ""))
        failed = str(status_name).upper() in ("OOG", "REVERT", "FAILED")
        if use_journal and jh is not None and inner_cp is not None:
            try:
                if failed:
                    jh.revert(inner_cp)
                else:
                    jh.commit(inner_cp)
            except Exception:
                # If journal operations fail, continue without crashing; state may be partially applied.
                pass

    # Commit the outer checkpoint (if any)
    if use_journal and jh is not None and outer_cp is not None:
        try:
            jh.commit(outer_cp)
        except Exception:
            # Safe to ignore at this level; state has been advanced per-tx already
            pass

    root = _state_root(state)
    return BlockResult(
        tx_results=tx_results,
        total_gas_used=total_gas,
        logs=all_logs,
        state_root=root,
    )


__all__ = ["apply_tx", "apply_block", "BlockResult"]
