"""
execution.runtime.transfers — deterministic transfer execution

Implements the simplest on-chain action: move funds from sender → recipient,
charge intrinsic gas, split base/tip fees, and (optionally)
emit a standard Transfer log.

The function is intentionally duck-typed and works with a variety of "state"
backends as long as they expose intuitive balance accessors. See helpers
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
  tip component.
- Optionally emit a Transfer log (address=recipient; topics=[b"transfer", sender, recipient];
  data = amount (big-endian bytes)).

This module returns an ApplyResult (status, gas_used, logs, state_root, receipt).
Receipt construction is performed by higher layers.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any, List, Mapping, Optional, Tuple

from ..errors import OOG, ExecError, Revert
from core.utils.address import AddressError
from core.utils.address_codec import (
    AccountKeyError,
    account_key_from_any,
    account_key_from_bech32,
    account_key_from_pubkey,
    account_key_from_raw,
)
from ..state.apply_balance import InsufficientBalance
from ..types.events import LogEvent
from ..types.result import ApplyResult
from ..types.status import TxStatus

if TYPE_CHECKING:
    from .env import BlockEnv, TxEnv

# ------------------------------------------------------------------------------
# Constants & feature toggles
# ------------------------------------------------------------------------------

DEFAULT_INTRINSIC_TRANSFER = 21_000  # sane default; may be overridden by gas.table
ADDRESS_LEN = 32  # Animica uses 32-byte addresses (matches core/types/tx.py)

log = logging.getLogger(__name__)


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


def _account_key_from_value(x: Any) -> bytes:
    if x is None:
        return b""
    if isinstance(x, (bytes, bytearray, memoryview)):
        try:
            return account_key_from_raw(x)
        except AccountKeyError:
            return b""
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return b""
        try:
            return account_key_from_bech32(s)
        except (AccountKeyError, AddressError):
            return b""
    try:
        return account_key_from_any(x)
    except (AccountKeyError, AddressError, TypeError):
        return b""


def _extract_signature_pubkey(tx: Any) -> tuple[bytes | None, int | None]:
    sig = None
    if hasattr(tx, "sigs"):
        sigs = getattr(tx, "sigs", None)
        if isinstance(sigs, (list, tuple)) and sigs:
            sig = sigs[0]
    if sig is None and isinstance(tx, Mapping):
        sigs = tx.get("sigs")
        if isinstance(sigs, list) and sigs:
            sig = sigs[0]
        elif "sig" in tx:
            sig = tx.get("sig")
        elif "signature" in tx:
            sig = tx.get("signature")

    if sig is None:
        return None, None

    if isinstance(sig, Mapping):
        alg_id = sig.get("alg") or sig.get("alg_id") or sig.get("algId")
        pubkey = sig.get("pubkey") or sig.get("pub") or sig.get("pk")
    else:
        alg_id = getattr(sig, "alg_id", getattr(sig, "alg", None))
        pubkey = getattr(sig, "pubkey", getattr(sig, "pub", None))

    if pubkey is None:
        return None, None
    if isinstance(pubkey, str):
        if pubkey.startswith(("0x", "0X")):
            try:
                pubkey = bytes.fromhex(pubkey[2:])
            except ValueError:
                return None, None
        else:
            return None, None
    return bytes(pubkey), int(alg_id) if alg_id is not None else None


def _payload_sender_value(tx: Any, tx_env: Any) -> Any:
    unsigned = _get(tx, "unsigned")
    if unsigned is not None:
        sender = _get(unsigned, "sender", "from", "from_address")
        if sender is not None:
            return sender
    sender = _get(tx, "sender", "from", "from_address")
    if sender is not None:
        return sender
    if tx_env is not None:
        return getattr(tx_env, "sender", None)
    return None


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
                balance: int = 0
                code_hash: bytes = b""

            accounts[addr] = _Acc(balance=int(value))
        else:
            setattr(acc, "balance", int(value))
        return
    raise ExecError("state does not expose a writable balance API")


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


def _compute_fee_parts(
    base_price: int, gas_price: int, gas_used: int
) -> Tuple[int, int, int]:
    """
    Return (total_fee, base_component, tip_component).

    Ensures total_fee == base_component + tip_component and rejects
    underpriced transactions (gas_price < base_price) to prevent
    crediting more fees than were debited.
    """
    base_per_gas = max(0, int(base_price))
    gas_per_gas = max(0, int(gas_price))
    used = max(0, int(gas_used))
    if gas_per_gas < base_per_gas:
        raise ExecError(
            "gas_price below base_fee",
            code="FEE_TOO_LOW",
            data={
                "gas_price": str(gas_per_gas),
                "base_fee": str(base_per_gas),
            },
        )
    base_component = base_per_gas * used
    tip_component = (gas_per_gas - base_per_gas) * used
    total_fee = base_component + tip_component
    return total_fee, base_component, tip_component


def _credit_balance(state: Any, addr: bytes, amount: int) -> None:
    if amount < 0:
        raise ExecError("negative credit amount", code="NEGATIVE_AMOUNT")
    if amount == 0:
        return
    cur = _get_balance(state, addr)
    _set_balance(state, addr, cur + amount)


def _debit_balance(state: Any, addr: bytes, amount: int) -> None:
    if amount < 0:
        raise ExecError("negative debit amount", code="NEGATIVE_AMOUNT")
    if amount == 0:
        return
    cur = _get_balance(state, addr)
    if cur < amount:
        raise InsufficientBalance(
            "Insufficient balance for transfer",
            required=amount,
            available=cur,
            shortfall=amount - cur,
        )
    _set_balance(state, addr, cur - amount)


def _increment_nonce(state: Any, addr: bytes) -> None:
    get_nonce = getattr(state, "get_nonce", None)
    set_nonce = getattr(state, "set_nonce", None)
    if callable(get_nonce) and callable(set_nonce):
        current = int(get_nonce(addr))
        set_nonce(addr, current + 1)
        return
    inc_nonce = getattr(state, "increment_nonce", None)
    if callable(inc_nonce):
        inc_nonce(addr)


# ------------------------------------------------------------------------------
# Logs
# ------------------------------------------------------------------------------


def _make_transfer_log(sender: bytes, recipient: bytes, amount: int) -> LogEvent:
    data = int(amount).to_bytes(max(1, (int(amount).bit_length() + 7) // 8), "big")
    # topics are raw bytes; "transfer" tag is a small, explicit domain tag.
    # Use 32-byte addresses in topics (Animica native format)
    return LogEvent(
        address=recipient,
        topics=[b"transfer", sender.rjust(ADDRESS_LEN, b"\x00"), recipient.rjust(ADDRESS_LEN, b"\x00")],
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
    sig_pubkey, sig_alg_id = _extract_signature_pubkey(tx)
    payload_sender = _payload_sender_value(tx, tx_env)
    sender = b""
    if sig_pubkey is not None:
        sender = account_key_from_pubkey(sig_pubkey, sig_alg_id)
        if payload_sender is not None:
            payload_key = _account_key_from_value(payload_sender)
            if payload_key and payload_key != sender:
                raise ExecError(
                    "sender mismatch between signature and payload",
                    code="SENDER_MISMATCH",
                )
    else:
        sender = _account_key_from_value(payload_sender)

    if not sender:
        raise ExecError("missing sender", code="MISSING_SENDER")
    if len(sender) != ADDRESS_LEN:
        raise ExecError(f"TxEnv.sender must be {ADDRESS_LEN} bytes, got {len(sender)}")

    # Extract recipient address from tx (check multiple locations for compatibility)
    to = _get(tx, "to", "recipient", "to_address")
    if to is None:
        # Try nested structure: tx.unsigned.payload.to (canonical Tx format)
        unsigned = _get(tx, "unsigned")
        if unsigned is not None:
            payload = _get(unsigned, "payload")
            if payload is not None:
                to = _get(payload, "to", "recipient")
    
    to = _account_key_from_value(to)
    
    # Check for empty or zero address before padding
    if len(to) == 0 or to == b"\x00" * len(to):
        # No recipient or zero address → nothing to transfer (treat as revert)
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=0,
            logs=[],
            state_root=_maybe_state_root(state),
            receipt=None,
        )
    
    if len(to) != ADDRESS_LEN:
        raise ExecError(f"Recipient address must be {ADDRESS_LEN} bytes, got {len(to)}")

    # Extract transfer amount from tx (check multiple locations for compatibility)
    amount = _get(tx, "value", "amount")
    if amount is None:
        # Try nested structure: tx.unsigned.payload.amount (canonical Tx format)
        unsigned = _get(tx, "unsigned")
        if unsigned is not None:
            payload = _get(unsigned, "payload")
            if payload is not None:
                amount = _get(payload, "amount", "value")
    amount = _as_int(amount, default=0)
    
    # Extract gas limit from tx (check multiple locations for compatibility)
    gas_limit = _get(tx, "gas", "gas_limit", "gasLimit")
    if gas_limit is None or gas_limit == 0:
        # Try nested structure: tx.unsigned.gas_limit (canonical Tx format)
        unsigned = _get(tx, "unsigned")
        if unsigned is not None:
            gas_limit = _get(unsigned, "gas_limit", "gasLimit")
    gas_limit = _as_int(gas_limit, default=0)

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

    if amount < 0:
        raise ExecError("negative transfer amount", code="NEGATIVE_AMOUNT")

    total_fee, base_fee_part, tip_fee_part = _compute_fee_parts(
        base_price, gas_price, intrinsic
    )

    coinbase = _account_key_from_value(
        getattr(block_env, "coinbase", b"\x00" * ADDRESS_LEN)
    )
    treasury = getattr(block_env, "treasury", None)
    t_addr = b""
    if base_fee_part > 0 and isinstance(treasury, (bytes, bytearray, str)):
        t_addr = _account_key_from_value(treasury)

    _ensure_account(state, sender)
    _ensure_account(state, to)

    # Ensure sender has enough to cover amount + fee
    sender_balance = _get_balance(state, sender)
    required = amount + total_fee
    if sender_balance < required:
        raise InsufficientBalance(
            f"Insufficient balance for transfer",
            required=required,
            available=sender_balance,
            shortfall=required - sender_balance,
        )

    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "apply_transfer account keys",
            extra={
                "sender_key": sender.hex(),
                "recipient_key": to.hex(),
                "sender_len": len(sender),
                "recipient_len": len(to),
            },
        )

    pre_sender_balance = sender_balance
    pre_recipient_balance = _get_balance(state, to)
    pre_coinbase_balance = _get_balance(state, coinbase) if coinbase else 0
    pre_treasury_balance = _get_balance(state, t_addr) if t_addr else 0

    # Debit fees first (burn base, tip to coinbase)
    _debit_balance(state, sender, total_fee)

    # Tip → coinbase
    if tip_fee_part > 0 and any(coinbase) and coinbase != b"\x00" * ADDRESS_LEN:
        _ensure_account(state, coinbase)
        _credit_balance(state, coinbase, tip_fee_part)

    # Optional: send base fee to a treasury if exposed
    if base_fee_part > 0:
        if any(t_addr):
            _ensure_account(state, t_addr)
            _credit_balance(state, t_addr, base_fee_part)
        # Else burned (no credit)

    # Value transfer (skip if sending to self; fees already debited)
    if sender != to and amount > 0:
        _debit_balance(state, sender, amount)
        _credit_balance(state, to, amount)

    # Logs
    logs: List[LogEvent] = []
    if emit_event and amount > 0:
        logs.append(_make_transfer_log(sender, to, amount))

    _increment_nonce(state, sender)

    if log.isEnabledFor(logging.DEBUG):
        post_sender_balance = _get_balance(state, sender)
        post_recipient_balance = _get_balance(state, to)
        post_coinbase_balance = _get_balance(state, coinbase) if coinbase else 0
        post_treasury_balance = _get_balance(state, t_addr) if t_addr else 0

        sender_delta = post_sender_balance - pre_sender_balance
        recipient_delta = post_recipient_balance - pre_recipient_balance
        coinbase_delta = post_coinbase_balance - pre_coinbase_balance
        treasury_delta = post_treasury_balance - pre_treasury_balance

        expected_sender_delta = -(amount + total_fee)
        expected_recipient_delta = amount if sender != to else 0
        expected_coinbase_delta = tip_fee_part if tip_fee_part > 0 and any(coinbase) else 0
        expected_treasury_delta = base_fee_part if base_fee_part > 0 and any(t_addr) else 0
        burned = 0
        if tip_fee_part > 0 and not any(coinbase):
            burned += tip_fee_part
        if base_fee_part > 0 and not any(t_addr):
            burned += base_fee_part
        expected_sum = -burned
        actual_sum = sender_delta + recipient_delta + coinbase_delta + treasury_delta

        if (
            sender_delta != expected_sender_delta
            or recipient_delta != expected_recipient_delta
            or coinbase_delta != expected_coinbase_delta
            or treasury_delta != expected_treasury_delta
            or actual_sum != expected_sum
        ):
            raise ExecError(
                "transfer balance invariant failed",
                code="TRANSFER_INVARIANT",
                data={
                    "sender_delta": sender_delta,
                    "recipient_delta": recipient_delta,
                    "coinbase_delta": coinbase_delta,
                    "treasury_delta": treasury_delta,
                    "expected_sender_delta": expected_sender_delta,
                    "expected_recipient_delta": expected_recipient_delta,
                    "expected_coinbase_delta": expected_coinbase_delta,
                    "expected_treasury_delta": expected_treasury_delta,
                    "expected_sum": expected_sum,
                    "actual_sum": actual_sum,
                },
            )

    return ApplyResult(
        status=TxStatus.SUCCESS,
        gas_used=intrinsic,
        logs=logs,
        state_root=_maybe_state_root(state),
        receipt=None,
    )


__all__ = ["apply_transfer"]
