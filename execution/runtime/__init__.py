"""
execution.runtime — block/tx execution orchestration.

This package wires together the state machine pieces needed to execute a block
or a single transaction deterministically:

Submodules (thin overview)
--------------------------
- env          : helpers to construct BlockEnv / TxEnv from chain head + tx
- dispatcher   : routes by tx kind (transfer/deploy/call)
- transfers    : pure transfer execution (balance/nonce/events)
- contracts    : adapter hook to vm_py (feature-flagged)
- executor     : top-level apply_tx / apply_block orchestration
- fees         : base/tip split, burns, gas accounting finalization
- system       : well-known system accounts (treasury/coinbase/reserved)
- event_sink   : append logs, compute bloom/receipt hash

Re-exports
----------
For convenience, a few common entrypoints are exposed at the package level:

    from execution.runtime import apply_block, apply_tx
    from execution.runtime import make_block_env, make_tx_env
    from execution.runtime import dispatch

These are lazily loaded; importing this package does not import heavy
dependencies until the attributes are first accessed.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Dict, Tuple

from ..version import __version__ as __version__  # re-export

# Submodules available for "from execution.runtime import env" style imports.
__all__ = (
    "env",
    "dispatcher",
    "transfers",
    "contracts",
    "executor",
    "fees",
    "system",
    "event_sink",
)

# Lazy symbol re-exports: name -> (module, attribute)
_EXPORTS: Dict[str, Tuple[str, str]] = {
    "apply_block": ("executor", "apply_block"),
    "apply_tx": ("executor", "apply_tx"),
    "dispatch": ("dispatcher", "dispatch"),
    "make_block_env": ("env", "make_block_env"),
    "make_tx_env": ("env", "make_tx_env"),
}


def __getattr__(name: str) -> Any:  # PEP 562 lazy attribute access
    # First, allow "from execution.runtime import env" to load submodule lazily.
    if name in __all__:
        return import_module(f".{name}", __name__)
    # Then, resolve convenience re-exports on first use.
    target = _EXPORTS.get(name)
    if target:
        mod, attr = target
        return getattr(import_module(f".{mod}", __name__), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    # Make dir() show submodules and exported symbols.
    return sorted(list(__all__) + list(_EXPORTS) + ["__version__"])
