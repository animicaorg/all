"""
execution.runtime.contracts — adapter hook to vm_py (no-op until vm_py lands)

This module defines the contract execution entrypoints used by the dispatcher.
By default (feature-flag off or vm_py unavailable), both deploy and call
behave as *deterministic no-ops that REVERT*, while still charging intrinsic
gas and splitting base/tip fees like a normal transaction. This keeps the node
fully functional before the Python-VM is integrated.

Once vm_py is available, set either:
  - environment ANIMICA_ENABLE_VM_PY=1
  - or supply enable_vm_py=True to the functions

and the adapter will attempt to import vm_py lazily and route deploy/call.
(That code path is guarded and will continue to REVERT if imports fail.)
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, List, Mapping, Optional, Tuple

from ..errors import ExecError
from ..types.events import LogEvent
from ..types.result import ApplyResult
from ..types.status import TxStatus

if TYPE_CHECKING:
    from .env import BlockEnv, TxEnv


# --------------------------------------------------------------------------------------
# Utilities (kept local to avoid import tangles)
# --------------------------------------------------------------------------------------

DEFAULT_INTRINSIC_CALL = 21_000
DEFAULT_INTRINSIC_DEPLOY = 53_000


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


def _ensure_account(state: Any, addr: bytes) -> None:
    if hasattr(state, "ensure_account"):
        state.ensure_account(addr)  # type: ignore[attr-defined]
        return
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
    accounts = getattr(state, "accounts", None)
    if isinstance(accounts, dict) and addr in accounts:
        return int(getattr(accounts[addr], "balance", 0))
    raise ExecError("state does not expose a readable balance API")


def _set_balance(state: Any, addr: bytes, value: int) -> None:
    if hasattr(state, "set_balance"):
        state.set_balance(addr, int(value))  # type: ignore[attr-defined]
        return
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
    m = getattr(state, "balances", None)
    if isinstance(m, dict):
        m[addr] = int(value)
        return
    accounts = getattr(state, "accounts", None)
    if isinstance(accounts, dict):
        acc = accounts.get(addr)
        if acc is None:

            class _Acc:
                nonce: int = 0
                balance: int = 0
                code_hash: bytes = b""

            accounts[addr] = _Acc()  # type: ignore[call-arg]
            setattr(accounts[addr], "balance", int(value))
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


def _split_fee(base_price: int, gas_price: int, gas_used: int) -> Tuple[int, int, int]:
    base_component = max(0, int(base_price)) * int(gas_used)
    total = max(0, int(gas_price)) * int(gas_used)
    tip_component = max(0, total - base_component)
    return total, base_component, tip_component


def _resolve_intrinsic_call(tx: Any, params: Optional[Any]) -> int:
    try:
        from ..gas.intrinsic import intrinsic_for_call  # type: ignore

        return int(intrinsic_for_call(tx, params))
    except Exception:
        pass
    try:
        from ..gas.table import get_gas_cost  # type: ignore

        v = get_gas_cost("call")
        if isinstance(v, int):
            return v
        if isinstance(v, Mapping) and "base" in v:
            return int(v["base"])
    except Exception:
        pass
    return DEFAULT_INTRINSIC_CALL


def _resolve_intrinsic_deploy(tx: Any, params: Optional[Any]) -> int:
    try:
        from ..gas.intrinsic import intrinsic_for_deploy  # type: ignore

        return int(intrinsic_for_deploy(tx, params))
    except Exception:
        pass
    try:
        from ..gas.table import get_gas_cost  # type: ignore

        v = get_gas_cost("deploy")
        if isinstance(v, int):
            return v
        if isinstance(v, Mapping) and "base" in v:
            return int(v["base"])
    except Exception:
        pass
    return DEFAULT_INTRINSIC_DEPLOY


def _burn_and_tip(
    state: Any, block_env: "BlockEnv", gas_used: int, gas_price: int, base_price: int
) -> Tuple[int, int, int]:
    total_fee, base_fee_part, tip_fee_part = _split_fee(base_price, gas_price, gas_used)

    coinbase = _as_bytes(getattr(block_env, "coinbase", b"\x00" * 20), expect_len=20)
    if tip_fee_part > 0 and any(coinbase):
        _ensure_account(state, coinbase)
        cb_bal = _get_balance(state, coinbase)
        _set_balance(state, coinbase, cb_bal + tip_fee_part)

    treasury = getattr(block_env, "treasury", None)
    if base_fee_part > 0 and isinstance(treasury, (bytes, bytearray, str)):
        t_addr = _as_bytes(treasury, expect_len=20)
        if any(t_addr):
            _ensure_account(state, t_addr)
            t_bal = _get_balance(state, t_addr)
            _set_balance(state, t_addr, t_bal + base_fee_part)

    return total_fee, base_fee_part, tip_fee_part


# --------------------------------------------------------------------------------------
# Public API — Deploy & Call
# --------------------------------------------------------------------------------------


def apply_deploy(
    tx: Any,
    state: Any,
    block_env: "BlockEnv",
    tx_env: "TxEnv",
    *,
    params: Optional[Any] = None,
    enable_vm_py: Optional[bool] = None,
) -> ApplyResult:
    """
    Deploy a contract package (manifest + code). Until vm_py is enabled/available,
    this deterministically REVERTs while charging intrinsic gas and fees.
    """
    intrinsic = _resolve_intrinsic_deploy(tx, params)
    gas_limit = _as_int(_get(tx, "gas", "gas_limit", "gasLimit"), default=0)

    # OOG check (intrinsic)
    if gas_limit and intrinsic > gas_limit:
        return ApplyResult(
            status=TxStatus.OOG,
            gas_used=max(0, gas_limit),
            logs=[],
            state_root=_maybe_state_root(state),
            receipt=None,
        )

    # Fees: debit sender, pay tip/treasury, bump nonce
    sender = _as_bytes(getattr(tx_env, "sender", None), expect_len=20)
    if len(sender) != 20:
        raise ExecError("TxEnv.sender must be 20 bytes")

    gas_price = _as_int(getattr(tx_env, "gas_price", 0))
    base_price = _as_int(getattr(tx_env, "base_price", 0))

    total_fee, _, _ = _burn_and_tip(state, block_env, intrinsic, gas_price, base_price)

    _ensure_account(state, sender)
    bal = _get_balance(state, sender)
    if bal < total_fee:
        # Not enough to pay fees — REVERT with intrinsic charged up to limit
        gas_used = intrinsic if gas_limit == 0 else min(intrinsic, gas_limit)
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=gas_used,
            logs=[],
            state_root=_maybe_state_root(state),
            receipt=None,
        )
    _set_balance(state, sender, bal - total_fee)
    _set_nonce(state, sender, _get_nonce(state, sender) + 1)

    # Feature-gated path (future)
    if (enable_vm_py is True) or (
        enable_vm_py is None and os.getenv("ANIMICA_ENABLE_VM_PY") == "1"
    ):
        try:
            # Placeholder hook — intentionally not imported at module import time.
            # from vm_py.runtime.loader import deploy_package
            # result = deploy_package(...)
            # return result_as_ApplyResult(...)
            pass  # not yet wired
        except Exception:
            # fall back to deterministic revert below
            pass

    # Deterministic no-op: REVERT with a diagnostic log
    logs: List[LogEvent] = [
        LogEvent(
            address=b"\x00" * 20,
            topics=[b"vm.disabled", b"deploy"],
            data=b"",
        )
    ]
    return ApplyResult(
        status=TxStatus.REVERT,
        gas_used=intrinsic,
        logs=logs,
        state_root=_maybe_state_root(state),
        receipt=None,
    )


def apply_call(
    tx: Any,
    state: Any,
    block_env: "BlockEnv",
    tx_env: "TxEnv",
    *,
    params: Optional[Any] = None,
    enable_vm_py: Optional[bool] = None,
) -> ApplyResult:
    """
    Call a deployed contract. Until vm_py is enabled/available, this REVERTs while
    charging intrinsic gas and fees. Value attached to the call is not transferred
    on REVERT (consistent with rollback semantics).
    """
    intrinsic = _resolve_intrinsic_call(tx, params)
    gas_limit = _as_int(_get(tx, "gas", "gas_limit", "gasLimit"), default=0)

    # OOG check (intrinsic)
    if gas_limit and intrinsic > gas_limit:
        return ApplyResult(
            status=TxStatus.OOG,
            gas_used=max(0, gas_limit),
            logs=[],
            state_root=_maybe_state_root(state),
            receipt=None,
        )

    # Fees: debit sender, pay tip/treasury, bump nonce
    sender = _as_bytes(getattr(tx_env, "sender", None), expect_len=20)
    if len(sender) != 20:
        raise ExecError("TxEnv.sender must be 20 bytes")

    gas_price = _as_int(getattr(tx_env, "gas_price", 0))
    base_price = _as_int(getattr(tx_env, "base_price", 0))

    total_fee, _, _ = _burn_and_tip(state, block_env, intrinsic, gas_price, base_price)

    _ensure_account(state, sender)
    bal = _get_balance(state, sender)
    if bal < total_fee:
        gas_used = intrinsic if gas_limit == 0 else min(intrinsic, gas_limit)
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=gas_used,
            logs=[],
            state_root=_maybe_state_root(state),
            receipt=None,
        )
    _set_balance(state, sender, bal - total_fee)
    _set_nonce(state, sender, _get_nonce(state, sender) + 1)

    # Future: route to vm_py if enabled & available
    if (enable_vm_py is True) or (
        enable_vm_py is None and os.getenv("ANIMICA_ENABLE_VM_PY") == "1"
    ):
        try:
            # Example sketch (intentionally not executed):
            # from vm_py.runtime.abi import dispatch_call
            # to = _as_bytes(_get(tx, "to", "recipient"), expect_len=20)
            # input_data = _as_bytes(_get(tx, "data", "input"))
            # result = dispatch_call(state_adapter, to, input_data, gas=gas_limit or intrinsic, env=...)
            # return result_as_ApplyResult(...)
            pass  # not yet wired
        except Exception:
            # fall back to deterministic revert below
            pass

    # Deterministic no-op: REVERT with diagnostic log tagged with recipient
    to = _as_bytes(_get(tx, "to", "recipient"), expect_len=20)
    logs: List[LogEvent] = [
        LogEvent(
            address=to if any(to) else b"\x00" * 20,
            topics=[b"vm.disabled", b"call"],
            data=b"",
        )
    ]
    return ApplyResult(
        status=TxStatus.REVERT,
        gas_used=intrinsic,
        logs=logs,
        state_root=_maybe_state_root(state),
        receipt=None,
    )


__all__ = ["apply_deploy", "apply_call"]
