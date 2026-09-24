"""
execution.runtime.staking — STAKE / UNSTAKE transaction handlers
===============================================================

STAKE moves spendable balance into a time-locked bond; UNSTAKE moves matured
bonds back. Staked coins are held as the *balance* of ``STAKE_SYSTEM_ADDR`` so
total supply is conserved in the account model and the pooled stake is auditable
as an ordinary balance, while the per-staker breakdown lives in that address's
storage (see ``core.staking``).

Both handlers are pure functions of (tx, state, block_env, tx_env): no wall
clock, no randomness, no I/O — the bond's unlock time comes from the *block*
timestamp, so replay on any node produces identical state.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, List, Mapping, Optional

from core.staking import (
    MAX_BONDS_PER_STAKER,
    SECONDS_PER_DAY,
    STAKE_SYSTEM_ADDR,
    Bond,
    StakeRecord,
    mark_bootstrap_exhausted,
    read_stake,
    write_stake,
)

from ..errors import ExecError
from ..types.status import TxStatus

if TYPE_CHECKING:
    from ..types.result import ApplyResult
    from .env import BlockEnv, TxEnv

log = logging.getLogger("execution.runtime.staking")

STAKE_GAS: int = 30_000
UNSTAKE_GAS: int = 30_000


class StakeError(ExecError):
    """Raised when a stake/unstake transaction fails validation."""


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        if isinstance(obj, Mapping) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _state_root(state: Any) -> Optional[bytes]:
    for name in ("compute_state_root", "state_root", "merkle_root"):
        fn = getattr(state, name, None)
        if callable(fn):
            try:
                root = fn()
                if isinstance(root, (bytes, bytearray)):
                    b = bytes(root)
                    return b if len(b) == 32 else b[:32].rjust(32, b"\x00")
            except Exception:
                pass
    return None


def _balance(state: Any, addr: bytes) -> int:
    for name in ("get_balance", "balance"):
        fn = getattr(state, name, None)
        if callable(fn):
            try:
                return int(fn(addr) or 0)
            except Exception:
                return 0
    return 0


def _add_balance(state: Any, addr: bytes, delta: int) -> None:
    fn = getattr(state, "add_balance", None)
    if callable(fn):
        fn(addr, int(delta))
        return
    setter = getattr(state, "set_balance", None)
    if not callable(setter):
        raise StakeError("state exposes neither add_balance nor set_balance")
    setter(addr, _balance(state, addr) + int(delta))


def _fail(state: Any, topic: bytes, msg: str, gas: int) -> "ApplyResult":
    from ..types.events import LogEvent
    from ..types.result import ApplyResult

    log.info("stake tx rejected: %s", msg)
    return ApplyResult(
        status=TxStatus.REVERT,
        gas_used=gas,
        logs=[LogEvent(address=STAKE_SYSTEM_ADDR[:20], topics=[topic], data=msg.encode())],
        state_root=_state_root(state),
        receipt=None,
    )


def _payload_fields(tx: Any) -> tuple[Optional[int], Optional[int], str]:
    """Pull (amount, duration_days, name) from a TxStake payload or a dict."""
    payload = _get(tx, "payload")
    if payload is None:
        unsigned = _get(tx, "unsigned")
        payload = _get(unsigned, "payload") if unsigned is not None else None
    body = _get(tx, "body")
    if payload is None and body is not None:
        # Wire envelope {"sig": ..., "body": {...}} — the signed body carries the
        # fields, exactly as transfers.py unwraps it.
        payload = _get(body, "payload") or body
    src = payload if payload is not None else tx

    amount = _get(src, "amount", "value")
    days = _get(src, "duration_days", "days", "durationDays")
    name = _get(src, "name", "stakerName", default="") or ""

    # Wire path: duration and label ride in `data` (the only place that survives
    # mempool normalisation AND is covered by the signature). The AMOUNT is
    # always the transaction's own `value`, never taken from this blob.
    if days is None or not name:
        raw_data = _get(src, "data", "input")
        if not raw_data and body is not None:
            raw_data = _get(body, "data", "input")
        try:
            from core.staking import decode_stake_data

            intent = decode_stake_data(raw_data)
        except Exception:
            intent = None
        if intent is not None:
            if days is None and intent.get("days"):
                days = intent["days"]
            if not name and intent.get("name"):
                name = intent["name"]

    try:
        amount_i = int(amount) if amount is not None else None
    except (TypeError, ValueError):
        amount_i = None
    try:
        days_i = int(days) if days is not None else None
    except (TypeError, ValueError):
        days_i = None
    return amount_i, days_i, str(name)


def _merge_bonds(bonds: List[Bond]) -> List[Bond]:
    """Coalesce bonds sharing an unlock time, then sort by unlock time."""
    by_unlock: dict[int, int] = {}
    for b in bonds:
        by_unlock[int(b.unlock_at)] = by_unlock.get(int(b.unlock_at), 0) + int(b.amount)
    return [Bond(amount=a, unlock_at=u) for u, a in sorted(by_unlock.items()) if a > 0]


def apply_stake(
    tx: Any,
    state: Any,
    block_env: "BlockEnv",
    tx_env: "TxEnv",
    *,
    params: Optional[Mapping[str, Any]] = None,
) -> "ApplyResult":
    """Bond `amount` of the sender's balance for `duration_days`."""
    from ..types.events import LogEvent
    from ..types.result import ApplyResult

    topic = b"stake.error"
    sender = bytes(_get(tx_env, "sender", default=b"\x00" * 32) or b"\x00" * 32)
    if sender == b"\x00" * 32:
        return _fail(state, topic, "stake requires an authenticated sender", STAKE_GAS)

    amount, days, name = _payload_fields(tx)
    if amount is None or days is None:
        return _fail(state, topic, "stake payload missing amount or duration", STAKE_GAS)
    if amount <= 0:
        return _fail(state, topic, "stake amount must be > 0", STAKE_GAS)
    if days <= 0 or days > 3650:
        return _fail(state, topic, "stake duration must be 1..3650 days", STAKE_GAS)

    available = _balance(state, sender)
    if available < amount:
        return _fail(
            state,
            topic,
            f"insufficient balance to stake: have {available}, need {amount}",
            STAKE_GAS,
        )

    now = int(_get(block_env, "timestamp", default=0) or 0)
    if now <= 0:
        return _fail(state, topic, "block timestamp unavailable", STAKE_GAS)
    unlock_at = now + days * SECONDS_PER_DAY

    rec = read_stake(state, sender)
    bonds = _merge_bonds(list(rec.bonds) + [Bond(amount=amount, unlock_at=unlock_at)])
    if len(bonds) > MAX_BONDS_PER_STAKER:
        return _fail(
            state,
            topic,
            f"too many bonds ({len(bonds)} > {MAX_BONDS_PER_STAKER}); "
            "withdraw a matured bond first",
            STAKE_GAS,
        )

    # Move the coins: out of spendable balance, into the pooled stake address.
    _add_balance(state, sender, -amount)
    _add_balance(state, STAKE_SYSTEM_ADDR, amount)
    write_stake(
        state,
        sender,
        StakeRecord(bonds=tuple(bonds), name=(name or rec.name)),
    )
    # First real bond ever: latch the bootstrap leader-set fallback off for good,
    # so a later mass-unbond can never hand block production back to the single
    # hardcoded bootstrap key.
    mark_bootstrap_exhausted(state)

    total = sum(b.amount for b in bonds)
    log.info(
        "stake: %s bonded %d nANM for %dd (unlock=%d, total=%d, name=%r)",
        sender.hex()[:16],
        amount,
        days,
        unlock_at,
        total,
        name or rec.name,
    )
    return ApplyResult(
        status=TxStatus.SUCCESS,
        gas_used=STAKE_GAS,
        logs=[
            LogEvent(
                address=STAKE_SYSTEM_ADDR[:20],
                topics=[b"stake.bonded", sender],
                data=f"{amount}:{unlock_at}:{total}".encode(),
            )
        ],
        state_root=_state_root(state),
        receipt=None,
    )


def apply_unstake(
    tx: Any,
    state: Any,
    block_env: "BlockEnv",
    tx_env: "TxEnv",
    *,
    params: Optional[Mapping[str, Any]] = None,
) -> "ApplyResult":
    """Return up to `amount` of MATURED stake to the sender's balance."""
    from ..types.events import LogEvent
    from ..types.result import ApplyResult

    topic = b"unstake.error"
    sender = bytes(_get(tx_env, "sender", default=b"\x00" * 32) or b"\x00" * 32)
    if sender == b"\x00" * 32:
        return _fail(state, topic, "unstake requires an authenticated sender", UNSTAKE_GAS)

    amount, _days, _name = _payload_fields(tx)
    if amount is None or amount <= 0:
        return _fail(state, topic, "unstake amount must be > 0", UNSTAKE_GAS)

    now = int(_get(block_env, "timestamp", default=0) or 0)
    if now <= 0:
        return _fail(state, topic, "block timestamp unavailable", UNSTAKE_GAS)

    rec = read_stake(state, sender)
    matured = rec.matured(now)
    if matured < amount:
        return _fail(
            state,
            topic,
            f"insufficient matured stake: {matured} available, {amount} requested",
            UNSTAKE_GAS,
        )

    # Drain matured bonds earliest-unlock-first; locked bonds are never touched.
    remaining = int(amount)
    kept: List[Bond] = []
    for b in sorted(rec.bonds, key=lambda x: int(x.unlock_at)):
        if remaining > 0 and int(b.unlock_at) <= now:
            take = min(remaining, int(b.amount))
            remaining -= take
            leftover = int(b.amount) - take
            if leftover > 0:
                kept.append(Bond(amount=leftover, unlock_at=int(b.unlock_at)))
        else:
            kept.append(b)
    if remaining != 0:  # pragma: no cover - guarded by the matured check above
        return _fail(state, topic, "internal: matured accounting mismatch", UNSTAKE_GAS)

    _add_balance(state, STAKE_SYSTEM_ADDR, -amount)
    _add_balance(state, sender, amount)
    write_stake(
        state, sender, StakeRecord(bonds=tuple(_merge_bonds(kept)), name=rec.name)
    )

    total = sum(b.amount for b in kept)
    log.info(
        "unstake: %s withdrew %d nANM (remaining stake=%d)",
        sender.hex()[:16],
        amount,
        total,
    )
    return ApplyResult(
        status=TxStatus.SUCCESS,
        gas_used=UNSTAKE_GAS,
        logs=[
            LogEvent(
                address=STAKE_SYSTEM_ADDR[:20],
                topics=[b"stake.withdrawn", sender],
                data=f"{amount}:{total}".encode(),
            )
        ],
        state_root=_state_root(state),
        receipt=None,
    )


__all__ = ["apply_stake", "apply_unstake", "StakeError", "STAKE_GAS", "UNSTAKE_GAS"]
