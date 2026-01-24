"""IDE controller for build and deploy orchestration."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from animica_miner_gui.backend.rpc_client import RPCClient
from animica_miner_gui.ide.deploy_manager import DeploymentManager, DeploymentOptions, DeploymentResult

from animica_miner_gui.ide.toolchain.builder import BuildResult, build_contract
from animica_miner_gui.ide.toolchain.preflight import PreflightResult, run_preflight
from animica_miner_gui.ide.toolchain.simulator import SimulationResult, simulate_call, simulate_tx


class IDEController(QObject):
    """Controller for IDE actions (build/deploy/simulate) with signals."""

    buildFinished = Signal(BuildResult)
    preflightFinished = Signal(PreflightResult)
    deployFinished = Signal(bool, str, object)
    deployProgress = Signal(str)
    simulateFinished = Signal(str, SimulationResult)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    def build_project(self, workspace: str) -> None:
        """Run a deterministic build for the current workspace."""
        result = build_contract(Path(workspace))
        self.buildFinished.emit(result)

    def preflight_project(self, workspace: str, rpc_client: RPCClient | None) -> None:
        """Run preflight checks for build, simulate, and RPC reachability."""

        def _run() -> None:
            result = run_preflight(Path(workspace), rpc_client)
            self.preflightFinished.emit(result)

        import threading

        threading.Thread(target=_run, daemon=True).start()

    def deploy_project(
        self,
        workspace: str,
        *,
        rpc_client: RPCClient,
        wallet_path: Path,
        options: DeploymentOptions,
    ) -> None:
        """Deploy a contract package from the workspace."""

        def _run() -> None:
            manager = DeploymentManager(
                rpc_client,
                workspace=Path(workspace),
                wallet_path=wallet_path,
                on_progress=self.deployProgress.emit,
            )
            result = manager.deploy(options)
            self.deployFinished.emit(result.success, result.message, result)

        import threading

        threading.Thread(target=_run, daemon=True).start()

    def run_simulation_call(self, manifest: dict, method: str, args: dict) -> None:
        result = simulate_call(manifest, method, args)
        self.simulateFinished.emit("call", result)

    def run_simulation_tx(self, manifest: dict, method: str, args: dict, tx_env: dict) -> None:
        result = simulate_tx(manifest, method, args, tx_env)
        self.simulateFinished.emit("tx", result)
