"""
Animica Python VM (vm_py) — package marker and public entrypoints.

This module exposes a tiny, stable façade over the internal compiler/runtime so
downstream tools can rely on a consistent API:

- __version__(): semantic version (optionally with a git describe suffix)
- compile_source(source: str, *, optimize: bool=False) -> bytes
    Compile Python contract source to IR bytes.
- inspect_ir(ir: bytes) -> dict
    Decode/pretty metadata for IR (op counts, functions, static gas upper bound).
- run_call(manifest: Mapping[str, Any], call: str, args: Mapping[str, Any] | None=None,
           *, state: dict | None=None, gas_limit: int | None=None) -> dict
    Load+link per manifest, execute a single call deterministically, return result.
- simulate_tx(manifest: Mapping[str, Any], call: str, args: Mapping[str, Any] | None=None,
              tx_env: Mapping[str, Any] | None=None, *, gas_limit: int | None=None) -> dict
    Higher-level helper mirroring node execution (tx/env envelope).

All heavy imports are lazy to avoid import-time failures during incremental
checkouts or partial environments. Functions will raise ImportError only when
they are actually used and the corresponding module is missing.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Dict
import importlib

try:
    # Prefer a generated/version-controlled value.
    from .version import __version__ as __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0+dev"

def version() -> str:
    """Return the vm_py semantic version string."""
    return __version__

# --- Public entrypoints (lazy-delegated to runtime/loader & compiler) ---------

def compile_source(source: str, *, optimize: bool = False) -> bytes:
    """
    Compile a Python contract source string to IR bytes.

    Parameters
    ----------
    source : str
        Contract source code (strict Python subset).
    optimize : bool
        Optional static optimization pass toggle.

    Returns
    -------
    bytes
        Deterministically encoded IR blob.
    """
    loader = importlib.import_module(".runtime.loader", __name__)
    return loader.compile_source(source, optimize=optimize)  # type: ignore[attr-defined]

def inspect_ir(ir: bytes) -> Dict[str, Any]:
    """
    Introspect an IR blob for human/tooling consumption.

    Returns a dict with keys like: {"functions": [...], "op_counts": {...}, "gas_upper_bound": int}
    """
    compiler_encode = importlib.import_module(".compiler.encode", __name__)
    gas_estimator = importlib.import_module(".compiler.gas_estimator", __name__)
    ir_mod = compiler_encode.decode(ir)  # type: ignore[attr-defined]
    return {
        "functions": getattr(ir_mod, "functions", []),
        "op_counts": getattr(ir_mod, "op_counts", {}),
        "gas_upper_bound": gas_estimator.estimate_upper_bound(ir_mod),  # type: ignore[attr-defined]
    }

def run_call(
    manifest: Mapping[str, Any],
    call: str,
    args: Optional[Mapping[str, Any]] = None,
    *,
    state: Optional[Dict[str, Any]] = None,
    gas_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Execute a contract call deterministically using an in-process engine.

    Parameters
    ----------
    manifest : Mapping[str, Any]
        The package manifest (ABI + metadata). May be a loaded dict.
    call : str
        Function name to invoke (per ABI).
    args : Mapping[str, Any] | None
        Call arguments matching the ABI schema.
    state : dict | None
        Optional ephemeral state object (testing/simulation). If None, a fresh in-memory state is used.
    gas_limit : int | None
        Optional hard gas cap for the call.

    Returns
    -------
    dict
        Result envelope, e.g. {"ok": True, "return": <value>, "logs": [...], "gasUsed": N}
    """
    loader = importlib.import_module(".runtime.loader", __name__)
    return loader.run_call(  # type: ignore[attr-defined]
        manifest=manifest,
        call=call,
        args=dict(args or {}),
        state=state,
        gas_limit=gas_limit,
    )

def simulate_tx(
    manifest: Mapping[str, Any],
    call: str,
    args: Optional[Mapping[str, Any]] = None,
    tx_env: Optional[Mapping[str, Any]] = None,
    *,
    gas_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Simulate a transaction-style call with a Tx/Block environment envelope.

    This mirrors node execution semantics more closely than `run_call`, exposing
    fields like chainId, coinbase, block height/timestamp, and gas price in `tx_env`.
    """
    loader = importlib.import_module(".runtime.loader", __name__)
    return loader.simulate_tx(  # type: ignore[attr-defined]
        manifest=manifest,
        call=call,
        args=dict(args or {}),
        tx_env=dict(tx_env or {}),
        gas_limit=gas_limit,
    )

__all__ = [
    "__version__",
    "version",
    "compile_source",
    "inspect_ir",
    "run_call",
    "simulate_tx",
]
