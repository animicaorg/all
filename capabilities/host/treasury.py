"""
capabilities.host.treasury
==========================

Deterministic, metered debit/credit hooks for a "treasury" balance domain.

Purpose
-------
Host-side helpers that other capability providers (e.g., AI/Quantum enqueue,
blob pinning) can call to *record* economic effects (debits/credits) that will
later be settled by the execution layer. These helpers are deterministic, impose
caps, and expose simple metrics. They **do not** move on-ledger balances by
themselves; they only record charge intents tied to the current tx so the
execution/runtime can consume them.

Interface
---------
Two syscalls are registered into the ProviderRegistry:

- TREASURY_DEBIT(ctx, *, amount: int, reason: str = "generic") -> dict
- TREASURY_CREDIT(ctx, *, amount: int, reason: str = "generic") -> dict

Where:
  - `amount` is a non-negative integer of the chain's base unit.
  - `reason` is a short, ASCII tag for accounting/auditing (e.g., "ai.enqueue").

Determinism & Scoping
---------------------
A per-process, per-block/tx *ledger* collects entries keyed by (chain_id, height,
tx_hash). Ordering is the order of invocation (which must be deterministic given
a deterministic VM/runtime). On re-execution within the same block application,
calls append to the same bucket.

Caps
----
Limits can be overridden in capabilities/config.py:

    TREASURY_MAX_DEBIT_PER_TX   = 10_000_000
    TREASURY_MAX_CREDIT_PER_TX  = 10_000_000
    TREASURY_MAX_REASON_LEN     = 64

Metrics
-------
Increments Prometheus counters if capabilities.metrics is available:
  - capabilities_treasury_debit_total{reason=...}
  - capabilities_treasury_credit_total{reason=...}
  - capabilities_treasury_debit_amount_sum
  - capabilities_treasury_credit_amount_sum

Consumption
-----------
Downstream (e.g., in capabilities/runtime/state_cache.py or an execution adapter)
you can read the current tx bucket via `peek_tx_ledger(ctx)` to fold these notes
into the final ApplyResult / receipts or settlement logic.

This module is intentionally stand-alone and safe to import early.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .provider import (ProviderRegistry, SyscallContext,  # type: ignore
                       get_registry)

# Try to import named registry keys (backward compatible fallbacks)
try:  # pragma: no cover
    from .provider import TREASURY_DEBIT as _KEY_DEBIT  # type: ignore
except Exception:  # pragma: no cover
    _KEY_DEBIT = "TREASURY_DEBIT"

try:  # pragma: no cover
    from .provider import TREASURY_CREDIT as _KEY_CREDIT  # type: ignore
except Exception:  # pragma: no cover
    _KEY_CREDIT = "TREASURY_CREDIT"

log = logging.getLogger("capabilities.host.treasury")

# ----------------------------
# Config & limits
# ----------------------------

_DEF_MAX_DEBIT_PER_TX = 10_000_000
_DEF_MAX_CREDIT_PER_TX = 10_000_000
_DEF_MAX_REASON_LEN = 64


def _limits() -> Tuple[int, int, int]:
    """
    Resolve (max_debit, max_credit, max_reason_len) from capabilities.config if present.
    """
    try:  # pragma: no cover
        from .. import config as _cfg  # type: ignore

        md = int(getattr(_cfg, "TREASURY_MAX_DEBIT_PER_TX", _DEF_MAX_DEBIT_PER_TX))
        mc = int(getattr(_cfg, "TREASURY_MAX_CREDIT_PER_TX", _DEF_MAX_CREDIT_PER_TX))
        rl = int(getattr(_cfg, "TREASURY_MAX_REASON_LEN", _DEF_MAX_REASON_LEN))
        return (
            md if md > 0 else _DEF_MAX_DEBIT_PER_TX,
            mc if mc > 0 else _DEF_MAX_CREDIT_PER_TX,
            rl if 8 <= rl <= 256 else _DEF_MAX_REASON_LEN,
        )
    except Exception:
        return (_DEF_MAX_DEBIT_PER_TX, _DEF_MAX_CREDIT_PER_TX, _DEF_MAX_REASON_LEN)


# ----------------------------
# Metrics (optional)
# ----------------------------


class _NullCounter:
    def labels(self, **_kw: Any) -> "_NullCounter":  # type: ignore
        return self

    def inc(self, *_a: Any, **_k: Any) -> None:  # type: ignore
        pass

    def observe(self, *_a: Any, **_k: Any) -> None:  # type: ignore
        pass


try:  # pragma: no cover
    from .. import metrics as _metrics  # type: ignore

    _DEBIT_TOTAL = getattr(_metrics, "treasury_debit_total", _NullCounter())
    _CREDIT_TOTAL = getattr(_metrics, "treasury_credit_total", _NullCounter())
    _DEBIT_AMOUNT = getattr(_metrics, "treasury_debit_amount_sum", _NullCounter())
    _CREDIT_AMOUNT = getattr(_metrics, "treasury_credit_amount_sum", _NullCounter())
except Exception:  # pragma: no cover
    _DEBIT_TOTAL = _NullCounter()
    _CREDIT_TOTAL = _NullCounter()
    _DEBIT_AMOUNT = _NullCounter()
    _CREDIT_AMOUNT = _NullCounter()


# ----------------------------
# In-memory deterministic ledger
# ----------------------------


@dataclass(frozen=True)
class _Key:
    chain_id: int
    height: int
    tx_hash: bytes


@dataclass
class TreasuryNote:
    op: str  # "debit" | "credit"
    amount: int  # non-negative
    reason: str  # short ASCII tag
    index: int  # deterministic order within tx (0-based)


# key -> notes list
_LEDGER: Dict[_Key, List[TreasuryNote]] = {}
# key -> running totals (debit_sum, credit_sum)
_TOTALS: Dict[_Key, Tuple[int, int]] = {}


def _ctx_key(ctx: SyscallContext) -> _Key:
    # Fall back to zeros if some fields are missing
    chain = int(getattr(ctx, "chain_id", 0) or 0)
    height = int(getattr(ctx, "height", 0) or 0)
    txh = getattr(ctx, "tx_hash", b"")
    if isinstance(txh, str) and txh.startswith("0x"):
        try:
            txh = bytes.fromhex(txh[2:])
        except Exception:
            txh = txh.encode("utf-8")
    if not isinstance(txh, (bytes, bytearray)):
        txh = b""
    return _Key(chain, height, bytes(txh))


def _append_note(
    ctx: SyscallContext, op: str, amount: int, reason: str
) -> TreasuryNote:
    key = _ctx_key(ctx)
    notes = _LEDGER.setdefault(key, [])
    note = TreasuryNote(op=op, amount=amount, reason=reason, index=len(notes))
    notes.append(note)

    dsum, csum = _TOTALS.get(key, (0, 0))
    if op == "debit":
        dsum += amount
    else:
        csum += amount
    _TOTALS[key] = (dsum, csum)
    return note


def peek_tx_ledger(ctx: SyscallContext) -> List[TreasuryNote]:
    """Return a copy of the notes collected for the current tx."""
    key = _ctx_key(ctx)
    return list(_LEDGER.get(key, []))


def reset_tx_ledger(ctx: SyscallContext) -> None:
    """
    Clear notes for the current tx. Intended for tests or after folding the notes
    into persistent state/receipts.
    """
    key = _ctx_key(ctx)
    _LEDGER.pop(key, None)
    _TOTALS.pop(key, None)


# ----------------------------
# Validation
# ----------------------------


def _validate_amount(amount: int) -> int:
    if not isinstance(amount, int):
        raise TypeError("amount must be int")
    if amount < 0:
        raise ValueError("amount must be non-negative")
    return amount


def _validate_reason(reason: Optional[str], max_len: int) -> str:
    r = (reason or "generic").strip()
    if not r:
        r = "generic"
    if len(r) > max_len:
        r = r[:max_len]
    # keep ASCII-ish; replace spaces with underscores
    try:
        r.encode("ascii")
    except Exception:
        r = r.encode("utf-8", "ignore").decode("ascii", "ignore") or "generic"
    return r.replace(" ", "_")


def _check_caps(
    ctx: SyscallContext, *, add_debit: int = 0, add_credit: int = 0
) -> None:
    maxd, maxc, _ = _limits()
    key = _ctx_key(ctx)
    dsum, csum = _TOTALS.get(key, (0, 0))
    if add_debit and dsum + add_debit > maxd:
        raise ValueError(
            f"treasury debit cap exceeded for tx: {dsum + add_debit} > {maxd}"
        )
    if add_credit and csum + add_credit > maxc:
        raise ValueError(
            f"treasury credit cap exceeded for tx: {csum + add_credit} > {maxc}"
        )


# ----------------------------
# Provider entrypoints
# ----------------------------


def _treasury_debit(
    ctx: SyscallContext, *, amount: int, reason: str = "generic"
) -> Dict[str, Any]:
    """
    Record a *debit* against the caller/tx for later settlement.
    Returns a small dict which can be surfaced to the VM if needed.
    """
    maxd, _maxc, rlen = _limits()
    amt = _validate_amount(amount)
    r = _validate_reason(reason, rlen)
    _check_caps(ctx, add_debit=amt)

    note = _append_note(ctx, "debit", amt, r)

    # metrics
    try:  # pragma: no cover
        _DEBIT_TOTAL.labels(reason=r).inc()
        _DEBIT_AMOUNT.inc(amt)
    except Exception:
        pass

    return {
        "ok": True,
        "op": "debit",
        "amount": amt,
        "reason": r,
        "cap": maxd,
        "index": note.index,
    }


def _treasury_credit(
    ctx: SyscallContext, *, amount: int, reason: str = "generic"
) -> Dict[str, Any]:
    """
    Record a *credit* (e.g., payout/fee split) for later settlement.
    """
    _maxd, maxc, rlen = _limits()
    amt = _validate_amount(amount)
    r = _validate_reason(reason, rlen)
    _check_caps(ctx, add_credit=amt)

    note = _append_note(ctx, "credit", amt, r)

    # metrics
    try:  # pragma: no cover
        _CREDIT_TOTAL.labels(reason=r).inc()
        _CREDIT_AMOUNT.inc(amt)
    except Exception:
        pass

    return {
        "ok": True,
        "op": "credit",
        "amount": amt,
        "reason": r,
        "cap": maxc,
        "index": note.index,
    }


# Flag deterministic for the registry/router
_treasury_debit._deterministic = True  # type: ignore[attr-defined]
_treasury_credit._deterministic = True  # type: ignore[attr-defined]


def register(registry: ProviderRegistry) -> None:
    """
    Register both debit and credit handlers.
    """
    registry.register(_KEY_DEBIT, _treasury_debit)  # type: ignore[arg-type]
    registry.register(_KEY_CREDIT, _treasury_credit)  # type: ignore[arg-type]


# Auto-register on import (idempotent)
try:  # pragma: no cover
    register(get_registry())
except Exception as _e:  # pragma: no cover
    log.debug("treasury provider auto-register skipped", extra={"reason": repr(_e)})


__all__ = [
    "TreasuryNote",
    "peek_tx_ledger",
    "reset_tx_ledger",
    "register",
]
