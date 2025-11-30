"""
execution.runtime.dispatcher — route a transaction to the correct executor.

This module decides *what kind* of transaction we have and forwards it to the
appropriate handler:

  - transfer → execution.runtime.transfers.apply_transfer
  - deploy   → execution.runtime.contracts.apply_deploy
  - call     → execution.runtime.contracts.apply_call

It is intentionally light and dependency-free; heavy imports are performed
lazily at dispatch time to keep module import cost low.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Optional

from ..errors import ExecError

if TYPE_CHECKING:  # type-only imports to avoid import-time cost/cycles
    from ..types.result import ApplyResult
    from .env import BlockEnv, TxEnv


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


class DispatchError(ExecError):
    """Raised when a transaction cannot be classified or routed."""


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """Tolerant getter over mapping or attribute lookup."""
    for n in names:
        if isinstance(obj, Mapping) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _as_bytes(x: Any) -> bytes:
    if x is None:
        return b""
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    if isinstance(x, str):
        s = x.strip()
        if s.startswith(("0x", "0X")):
            s = s[2:]
        if len(s) % 2:
            s = "0" + s
        try:
            return bytes.fromhex(s)
        except ValueError:
            return b""
    try:
        i = int(x)
        if i < 0:
            i = 0
        length = max(1, (i.bit_length() + 7) // 8)
        return i.to_bytes(length, "big")
    except Exception:
        return b""


_NUMERIC_KIND = {
    0: "transfer",
    1: "deploy",
    2: "call",
}

_ALIAS_KIND = {
    "xfer": "transfer",
    "payment": "transfer",
    "create": "deploy",
    "contract_create": "deploy",
    "invoke": "call",
    "exec": "call",
    "call": "call",
}


def resolve_tx_kind(tx: Any) -> str:
    """
    Determine the transaction kind: 'transfer' | 'deploy' | 'call'.

    Resolution order:
      1) Explicit field: kind / tx_kind / type / txType (string or numeric).
      2) Heuristics:
           - has code/init_code/bytecode → 'deploy'
           - to == None and has data/input → 'deploy'
           - has to and data/input non-empty → 'call'
           - has to and (no data/input) → 'transfer'
      3) Fallback: 'transfer'
    """
    explicit = _get(tx, "kind", "tx_kind", "type", "txType")
    if explicit is not None:
        # numeric → map; string → normalize
        if isinstance(explicit, int):
            if explicit in _NUMERIC_KIND:
                return _NUMERIC_KIND[explicit]
        else:
            k = str(explicit).strip().lower()
            if k in ("transfer", "deploy", "call"):
                return k
            if k in _ALIAS_KIND:
                return _ALIAS_KIND[k]

    # Heuristics
    has_code = any(
        _get(tx, n) is not None for n in ("code", "init_code", "bytecode", "contract")
    )
    to = _get(tx, "to", "recipient", "to_address")
    data = _get(tx, "data", "input", "call_data", "calldata")
    to_bytes = _as_bytes(to)
    data_bytes = _as_bytes(data)

    if has_code:
        return "deploy"
    if (to is None or len(to_bytes) == 0) and data is not None and len(data_bytes) > 0:
        # contract-creation style with to == null
        return "deploy"
    if (
        to is not None
        and len(to_bytes) > 0
        and data is not None
        and len(data_bytes) > 0
    ):
        return "call"
    if to is not None and len(to_bytes) > 0:
        return "transfer"

    # Last resort
    return "transfer"


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------


def dispatch(
    tx: Any,
    state: Any,
    block_env: BlockEnv,
    tx_env: TxEnv,
    *,
    vm: Optional[Any] = None,
    params: Optional[Any] = None,
    da: Optional[Any] = None,
    capabilities: Optional[Any] = None,
) -> "ApplyResult":
    """
    Route a transaction to the correct executor based on its kind.

    Parameters
    ----------
    tx : Any
        Transaction-like object (dict or dataclass). May include:
          - kind|tx_kind|type (transfer|deploy|call or 0|1|2)
          - to, data/input, code/init_code/bytecode, etc.
    state : Any
        Mutable execution state adapter (see execution.state.* and adapters).
    block_env : BlockEnv
        Block-level context (height, timestamp, base_price, chain_id, coinbase).
    tx_env : TxEnv
        Tx-level context (sender, nonce, gas/base/tip price, chain id).
    vm : Optional[Any]
        VM adapter/handle for contract deploy/call (passed through).
    params : Optional[Any]
        Chain parameters (gas tables, limits) if handlers require them.
    da : Optional[Any]
        Data-availability adapter (optional; forwarded to contract handlers).
    capabilities : Optional[Any]
        Capabilities adapter (AI/Quantum/Blob/zk hooks; forwarded where relevant).

    Returns
    -------
    ApplyResult
        Execution result object (status, gasUsed, logs, stateRoot, receipt).

    Raises
    ------
    DispatchError
        If the kind is unknown or handler is missing.
    """
    kind = resolve_tx_kind(tx)

    if kind == "transfer":
        from . import transfers as _transfers

        if not hasattr(_transfers, "apply_transfer"):
            raise DispatchError("transfer handler not available")
        return _transfers.apply_transfer(  # type: ignore[no-any-return]
            tx, state, block_env, tx_env, params=params
        )

    if kind == "deploy":
        from . import contracts as _contracts

        if not hasattr(_contracts, "apply_deploy"):
            raise DispatchError("deploy handler not available")
        return _contracts.apply_deploy(  # type: ignore[no-any-return]
            tx,
            state,
            block_env,
            tx_env,
            vm=vm,
            params=params,
            da=da,
            capabilities=capabilities,
        )

    if kind == "call":
        from . import contracts as _contracts

        if not hasattr(_contracts, "apply_call"):
            raise DispatchError("call handler not available")
        return _contracts.apply_call(  # type: ignore[no-any-return]
            tx,
            state,
            block_env,
            tx_env,
            vm=vm,
            params=params,
            da=da,
            capabilities=capabilities,
        )

    raise DispatchError(f"unknown transaction kind: {kind!r}")


__all__ = [
    "DispatchError",
    "resolve_tx_kind",
    "dispatch",
]
