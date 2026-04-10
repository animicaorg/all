"""
execution.gas — gas accounting package.

Submodules
----------
- table.py     : load/resolve gas costs from spec/opcodes_vm_py.yaml + builtins
- intrinsic.py : intrinsic gas for tx kinds (transfer/deploy/call/blob)
- meter.py     : GasMeter (debit/refund), OOG semantics
- refund.py    : gas refund tracker & finalization rules

This package exposes a small, stable surface via lazy re-exports so importing
`execution.gas` does not pull heavy dependencies until needed.

Example
-------
    from execution.gas import GasMeter, load_gas_table
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, Tuple

# Public re-exports (attr_name -> (module_name, attr_name_in_module))
_EXPORTS: Dict[str, Tuple[str, str]] = {
    # table
    "load_gas_table": ("table", "load_gas_table"),
    "GasTable": ("table", "GasTable"),
    # intrinsic
    "intrinsic_gas": ("intrinsic", "intrinsic_gas"),
    "IntrinsicGas": ("intrinsic", "IntrinsicGas"),
    # meter
    "GasMeter": ("meter", "GasMeter"),
    # refund
    "RefundTracker": ("refund", "RefundTracker"),
    "Refunds": ("refund", "Refunds"),
}

__all__ = tuple(_EXPORTS.keys())


def __getattr__(name: str) -> Any:  # PEP 562 lazy attribute loading
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as e:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from e
    mod = importlib.import_module(f"{__name__}.{module_name}")
    return getattr(mod, attr_name)


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(__all__))
