"""IDE controller for build and deploy orchestration."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from animica_miner_gui.ide.toolchain.builder import BuildResult, build_contract
from animica_miner_gui.ide.toolchain.simulator import SimulationResult, simulate_call, simulate_tx


class IDEController(QObject):
    """Controller for IDE actions (build/deploy/simulate) with signals."""

    buildFinished = Signal(BuildResult)
    deployFinished = Signal(bool, str)
    simulateFinished = Signal(str, SimulationResult)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    def build_project(self, workspace: str) -> None:
        """Run a deterministic build for the current workspace."""
        result = build_contract(Path(workspace))
        self.buildFinished.emit(result)

    def deploy_project(self, workspace: str) -> None:
        """Simulate a deploy operation for now."""
        message = f"Deploy queued for workspace: {workspace or 'No workspace'}"
        self.deployFinished.emit(True, message)

    def run_simulation_call(self, manifest: dict, method: str, args: dict) -> None:
        result = simulate_call(manifest, method, args)
        self.simulateFinished.emit("call", result)

    def run_simulation_tx(self, manifest: dict, method: str, args: dict, tx_env: dict) -> None:
        result = simulate_tx(manifest, method, args, tx_env)
        self.simulateFinished.emit("tx", result)
