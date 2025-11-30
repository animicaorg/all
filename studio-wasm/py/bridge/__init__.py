"""
Bridge package exports (Pyodide/WASM).

Public API re-exported from `bridge.entry`:

- version() -> str
- compile_bytes(...) -> dict
- run_call(...) -> dict
- simulate_tx(...) -> dict

These helpers are thin wrappers used by the JS side (studio-wasm/src)
to compile/link contracts and run simulated calls in a deterministic,
browser-safe Python VM.
"""

from .entry import compile_bytes, run_call, simulate_tx, version

__all__ = ["version", "compile_bytes", "run_call", "simulate_tx"]
