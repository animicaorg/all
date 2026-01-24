"""Local VM simulation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from animica_miner_gui.ide.toolchain.utils import load_vm_py


@dataclass(frozen=True)
class SimulationResult:
    ok: bool
    payload: Dict[str, Any]
    message: str


def simulate_call(manifest: Dict[str, Any], method: str, args: Dict[str, Any]) -> SimulationResult:
    try:
        vm_py = load_vm_py()
    except Exception as exc:
        return SimulationResult(False, {}, f"vm_py unavailable: {exc}")
    if vm_py is None:
        return SimulationResult(False, {}, "vm_py module not available.")

    try:
        result = vm_py.run_call(manifest, method, args)
        return SimulationResult(True, result, "Call simulated")
    except Exception as exc:  # pragma: no cover - VM dependency
        return SimulationResult(False, {}, f"Simulation failed: {exc}")


def simulate_tx(
    manifest: Dict[str, Any],
    method: str,
    args: Dict[str, Any],
    tx_env: Optional[Dict[str, Any]] = None,
) -> SimulationResult:
    try:
        vm_py = load_vm_py()
    except Exception as exc:
        return SimulationResult(False, {}, f"vm_py unavailable: {exc}")
    if vm_py is None:
        return SimulationResult(False, {}, "vm_py module not available.")

    try:
        result = vm_py.simulate_tx(manifest, method, args, tx_env or {})
        return SimulationResult(True, result, "Transaction simulated")
    except Exception as exc:  # pragma: no cover - VM dependency
        return SimulationResult(False, {}, f"Simulation failed: {exc}")
