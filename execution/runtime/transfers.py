"""
execution.runtime.transfers — deterministic transfer execution

Implements the simplest on-chain action: move funds from sender → recipient,
charge intrinsic gas, split base/tip fees, bump sender nonce, and (optionally)
emit a standard Transfer log.

The function is intentionally duck-typed and works with a variety of "state"
backends as long as they expose intuitive balance/nonce accessors. See helpers
below for the order of method/attribute probing.

Semantics (high level)
----------------------
- Determine intrinsic gas for a plain transfer (from gas tables if available,
  else use a conservative DEFAULT_INTRINSIC_TRANSFER).
- If a gas limit is provided and less than intrinsic → OOG.
- Compute total fee = gas_price * intrinsic; split into base burn and tip
  (coinbase reward). If a treasury account is available, base goes there;
  otherwise it is simply burned (not credited to anyone).
- Ensure sender has ≥ (amount + total_fee); otherwise REVERT (insufficient).
- Debit sender by amount+fee, credit recipient by amount, credit coinbase by
  tip component, bump sender nonce by +1.
- Optionally emit a Transfer log (address=recipient; topics=[b"transfer", sender, recipient];
  data = amount (big-endian bytes)).

This module returns an ApplyResult (status, gas_used, logs, state_root, receipt).
Receipt construction is performed by higher layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Mapping, Optional, Tuple

from ..errors import OOG, ExecError, Revert
from ..types.events import LogEvent
from ..types.result import ApplyResult
from ..types.status import TxStatus

if TYPE_CHECKING:
    from .env import BlockEnv, TxEnv

# ------------------------------------------------------------------------------
# Constants & feature toggles
# ------------------------------------------------------------------------------

DEFAULT_INTRINSIC_TRANSFER = 21_000  # sane default; may be overridden by gas.table


# ------------------------------------------------------------------------------
# Tolerant getters/coercers
# ------------------------------------------------------------------------------


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        if isinstance(obj, Mapping) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _as_int(x: Any, *, default: int = 0) -> int:
    if x is None:
        return default
    if isinstance(x, int):
        return x
    if isinstance(x, (bytes, bytearray)):
        return int.from_bytes(x, "big", signed=False)
    if isinstance(x, str):
        s = x.strip()
        if s.startswith(("0x", "0X")):
            try:
                return int(s, 16)
            except ValueError:
                return default
        try:
            return int(s, 10)
        except ValueError:
            return default
    try:
        return int(x)
    except Exception:
        return default


def _as_bytes(x: Any, *, expect_len: Optional[int] = None) -> bytes:
    if x is None:
        out = b""
    elif isinstance(x, (bytes, bytearray)):
        out = bytes(x)
    elif isinstance(x, str):
        s = x.strip()
        if s.startswith(("0x", "0X")):
            s = s[2:]
        if len(s) % 2:
            s = "0" + s
        try:
            out = bytes.fromhex(s)
        except ValueError:
            out = b""
    else:
        try:
            i = int(x)
            if i < 0:
                i = 0
            length = max(1, (i.bit_length() + 7) // 8)
            out = i.to_bytes(length, "big")
        except Exception:
            out = b""
    if expect_len is not None:
        if len(out) > expect_len:
            out = out[-expect_len:]
        elif len(out) < expect_len:
            out = out.rjust(expect_len, b"\x00")
    return out


# ------------------------------------------------------------------------------
# State access (duck-typed)
# ------------------------------------------------------------------------------


def _ensure_account(state: Any, addr: bytes) -> None:
    """Best-effort: create account if backend exposes such an API."""
    if hasattr(state, "ensure_account"):
        state.ensure_account(addr)  # type: ignore[attr-defined]
        return
    # If balances mapping exists, ensure key
    m = getattr(state, "balances", None)
    if isinstance(m, dict) and addr not in m:
        m[addr] = 0


def _get_balance(state: Any, addr: bytes) -> int:
    if hasattr(state, "get_balance"):
        return int(state.get_balance(addr))  # type: ignore[attr-defined]
    view = getattr(state, "view", None)
    if view is not None and hasattr(view, "get_balance"):
        return int(view.get_balance(addr))  # type: ignore[attr-defined]
    m = getattr(state, "balances", None)
    if isinstance(m, dict):
        return int(m.get(addr, 0))
    # Try account object
    accounts = getattr(state, "accounts", None)
    if isinstance(accounts, dict) and addr in accounts:
        acc = accounts[addr]
        bal = getattr(acc, "balance", 0)
        return int(bal)
    raise ExecError("state does not expose a readable balance API")


def _set_balance(state: Any, addr: bytes, value: int) -> None:
    # Preferred: explicit setter
    if hasattr(state, "set_balance"):
        state.set_balance(addr, int(value))  # type: ignore[attr-defined]
        return
    # Delta via credit/debit if available
    cur = _get_balance(state, addr)
    delta = int(value) - cur
    if delta == 0:
        return
    if delta > 0 and hasattr(state, "credit"):
        state.credit(addr, delta)  # type: ignore[attr-defined]
        return
    if delta < 0 and hasattr(state, "debit"):
        state.debit(addr, -delta)  # type: ignore[attr-defined]
        return
    # Direct mapping write
    m = getattr(state, "balances", None)
    if isinstance(m, dict):
        m[addr] = int(value)
        return
    accounts = getattr(state, "accounts", None)
    if isinstance(accounts, dict):
        acc = accounts.get(addr)
        if acc is None:
            # create minimal record
            @dataclass
            class _Acc:
                nonce: int = 0
                balance: int = 0
                code_hash: bytes = b""

            accounts[addr] = _Acc(balance=int(value))
        else:
            setattr(acc, "balance", int(value))
        return
    raise ExecError("state does not expose a writable balance API")


def _get_nonce(state: Any, addr: bytes) -> int:
    if hasattr(state, "get_nonce"):
        return int(state.get_nonce(addr))  # type: ignore[attr-defined]
    view = getattr(state, "view", None)
    if view is not None and hasattr(view, "get_nonce"):
        return int(view.get_nonce(addr))  # type: ignore[attr-defined]
    accounts = getattr(state, "accounts", None)
    if isinstance(accounts, dict) and addr in accounts:
        return int(getattr(accounts[addr], "nonce", 0))
    m = getattr(state, "nonces", None)
    if isinstance(m, dict):
        return int(m.get(addr, 0))
    return 0


def _set_nonce(state: Any, addr: bytes, value: int) -> None:
    if hasattr(state, "set_nonce"):
        state.set_nonce(addr, int(value))  # type: ignore[attr-defined]
        return
    accounts = getattr(state, "accounts", None)
    if isinstance(accounts, dict):
        acc = accounts.get(addr)
        if acc is None:
            _ensure_account(state, addr)
            acc = accounts.get(addr)
        if acc is not None:
            setattr(acc, "nonce", int(value))
            return
    m = getattr(state, "nonces", None)
    if isinstance(m, dict):
        m[addr] = int(value)
        return
    if hasattr(state, "bump_nonce"):
        # fallback: repeatedly bump (inefficient but deterministic)
        current = _get_nonce(state, addr)
        for _ in range(max(0, int(value) - current)):
            state.bump_nonce(addr)  # type: ignore[attr-defined]
        return
    raise ExecError("state does not expose a writable nonce API")


def _maybe_state_root(state: Any) -> bytes:
    for name in ("compute_state_root", "state_root", "merkle_root"):
        fn = getattr(state, name, None)
        if callable(fn):
            try:
                root = fn()
                return _as_bytes(root, expect_len=32)
            except Exception:
                pass
        val = getattr(state, name, None)
        if isinstance(val, (bytes, bytearray, str)):
            return _as_bytes(val, expect_len=32)
    return b"\x00" * 32


# ------------------------------------------------------------------------------
# Gas helpers
# ------------------------------------------------------------------------------


def _intrinsic_for_transfer(tx: Any, params: Optional[Any]) -> int:
    """
    Try to get intrinsic gas from execution.gas.table / intrinsic; otherwise default.
    """
    # execution.gas.intrinsic
    try:
        from ..gas.intrinsic import intrinsic_for_transfer  # type: ignore

        return int(intrinsic_for_transfer(tx, params))  # type: ignore[misc]
    except Exception:
        pass
    # execution.gas.table (lookup by name)
    try:
        from ..gas.table import get_gas_cost  # type: ignore

        v = get_gas_cost("transfer")  # type: ignore[misc]
        if isinstance(v, int):
            return v
        if isinstance(v, Mapping) and "base" in v:
            return int(v["base"])
    except Exception:
        pass
    return DEFAULT_INTRINSIC_TRANSFER


def _split_fee(base_price: int, gas_price: int, gas_used: int) -> Tuple[int, int, int]:
    """
    Return (total, base_component, tip_component).
    """
    base_component = max(0, int(base_price)) * int(gas_used)
    total = max(0, int(gas_price)) * int(gas_used)
    tip_component = max(0, total - base_component)
    return total, base_component, tip_component


# ------------------------------------------------------------------------------
# Logs
# ------------------------------------------------------------------------------


def _make_transfer_log(sender: bytes, recipient: bytes, amount: int) -> LogEvent:
    data = int(amount).to_bytes(max(1, (int(amount).bit_length() + 7) // 8), "big")
    # topics are raw bytes; "transfer" tag is a small, explicit domain tag.
    return LogEvent(
        address=recipient,
        topics=[b"transfer", sender.rjust(20, b"\x00"), recipient.rjust(20, b"\x00")],
        data=data,
    )


# ------------------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------------------


def apply_transfer(
    tx: Any,
    state: Any,
    block_env: "BlockEnv",
    tx_env: "TxEnv",
    *,
    params: Optional[Any] = None,
    emit_event: bool = True,
) -> ApplyResult:
    """
    Execute a value transfer transaction.

    Parameters
    ----------
    tx : Any
        Transaction-like object. Recognized fields:
          - to|recipient (address)
          - value|amount (integer)
          - gas|gas_limit|gasLimit (optional)
    state : Any
        Mutable state backend.
    block_env : BlockEnv
        Block-level context.
    tx_env : TxEnv
        Tx-level context (includes sender, gas/base/tip price).
    params : Optional[Any]
        Chain parameters, forwarded to intrinsic gas resolver.
    emit_event : bool
        Whether to produce a Transfer log event.

    Returns
    -------
    ApplyResult
    """
    sender = _as_bytes(getattr(tx_env, "sender", None), expect_len=20)
    if len(sender) != 20:
        raise ExecError("TxEnv.sender must be 20 bytes")

    to = _as_bytes(_get(tx, "to", "recipient", "to_address"), expect_len=20)
    if len(to) == 0:
        # No recipient → nothing to transfer (treat as revert)
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=0,
            logs=[],
            state_root=_maybe_state_root(state),
            receipt=None,
        )

    amount = _as_int(_get(tx, "value", "amount"), default=0)
    gas_limit = _as_int(_get(tx, "gas", "gas_limit", "gasLimit"), default=0)

    gas_price = _as_int(getattr(tx_env, "gas_price", 0), default=0)
    base_price = _as_int(getattr(tx_env, "base_price", 0), default=0)

    intrinsic = _intrinsic_for_transfer(tx, params)

    # Out-of-gas on intrinsic
    if gas_limit and intrinsic > gas_limit:
        return ApplyResult(
            status=TxStatus.OOG,
            gas_used=max(0, gas_limit),
            logs=[],
            state_root=_maybe_state_root(state),
            receipt=None,
        )

    total_fee, base_fee_part, tip_fee_part = _split_fee(
        base_price, gas_price, intrinsic
    )

    # Ensure sender has enough to cover amount + fee
    _ensure_account(state, sender)
    _ensure_account(state, to)
    sender_balance = _get_balance(state, sender)
    if sender_balance < amount + total_fee:
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=intrinsic if gas_limit == 0 else min(intrinsic, gas_limit),
            logs=[],
            state_root=_maybe_state_root(state),
            receipt=None,
        )

    # Debit fees first (burn base, tip to coinbase)
    new_sender_balance = sender_balance - total_fee
    _set_balance(state, sender, new_sender_balance)

    # Tip → coinbase
    coinbase = _as_bytes(getattr(block_env, "coinbase", b"\x00" * 20), expect_len=20)
    if tip_fee_part > 0 and any(coinbase):
        _ensure_account(state, coinbase)
        cb_bal = _get_balance(state, coinbase)
        _set_balance(state, coinbase, cb_bal + tip_fee_part)

    # Optional: send base fee to a treasury if exposed
    if base_fee_part > 0:
        treasury = getattr(block_env, "treasury", None)
        if isinstance(treasury, (bytes, bytearray, str)):
            t_addr = _as_bytes(treasury, expect_len=20)
            if any(t_addr):
                _ensure_account(state, t_addr)
                t_bal = _get_balance(state, t_addr)
                _set_balance(state, t_addr, t_bal + base_fee_part)
        # Else burned (no credit)

    # Value transfer
    _set_balance(state, sender, _get_balance(state, sender) - amount)
    _set_balance(state, to, _get_balance(state, to) + amount)

    # Nonce bump
    _set_nonce(state, sender, _get_nonce(state, sender) + 1)

    # Logs
    logs: List[LogEvent] = []
    if emit_event and amount > 0:
        logs.append(_make_transfer_log(sender, to, amount))

    return ApplyResult(
        status=TxStatus.SUCCESS,
        gas_used=intrinsic,
        logs=logs,
        state_root=_maybe_state_root(state),
        receipt=None,
    )


__all__ = ["apply_transfer"]
