"""IDE tab implementation for Animica Miner GUI."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from animica_miner_gui.ide.command_palette import CommandPalette, PaletteCommand
from animica_miner_gui.backend.config import MiningAppConfig, NetworkType
from animica_miner_gui.backend.node_controller import NodeController
from animica_miner_gui.backend.rpc_client import RPCClient
from animica_miner_gui.ide.controller import IDEController
from animica_miner_gui.ide.deploy_manager import (
    DeploymentOptions,
    DeploymentResult,
    WalletEntry,
    load_wallet_entries,
)
from animica_miner_gui.ide.editor_tabs import EditorTabs
from animica_miner_gui.ide.git_panel import GitPanel
from animica_miner_gui.ide.manifest_editor import ManifestEditor
from animica_miner_gui.ide.output_panel import OutputPanels
from animica_miner_gui.ide.project_tree import ProjectTree
from animica_miner_gui.ide.settings import load_ide_settings, save_ide_settings
from animica_miner_gui.ide.toolchain.diagnostics import Diagnostic
from animica_miner_gui.ide.toolchain.builder import BuildResult, build_contract
from animica_miner_gui.ide.toolchain.manifest import (
    ManifestLoadError,
    load_manifest,
    resolve_abi,
    resolve_manifest_path,
    resolve_source_path,
)
from animica_miner_gui.ide.toolchain.utils import canonical_json_str
from animica_miner_gui.ide.toolchain.preflight import PreflightResult

logger = logging.getLogger(__name__)


class IDETab(QWidget):
    """IDE tab with project explorer and editor."""

    def __init__(
        self,
        config: Optional[MiningAppConfig] = None,
        node_controller: Optional[NodeController] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.settings = load_ide_settings()
        self.controller = IDEController(self)
        self.config = config
        self.node_controller = node_controller
        self.rpc_client: Optional[RPCClient] = None
        if self.node_controller is not None:
            self.node_controller.rpcChanged.connect(self._on_rpc_changed)

        self.workspace_picker = QComboBox()
        self.workspace_picker.setEditable(True)
        self._load_recent_projects(self.settings.recent_projects)
        self.workspace_picker.currentTextChanged.connect(self._on_workspace_selected)

        new_button = QPushButton("New Project")
        new_button.clicked.connect(self.open_new_project_wizard)

        open_button = QPushButton("Open Folder")
        open_button.clicked.connect(self.select_workspace)

        refresh_button = QToolButton()
        refresh_button.setText("↻")
        refresh_button.clicked.connect(self.refresh_workspace)

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Workspace:"))
        top_bar.addWidget(self.workspace_picker, stretch=1)
        top_bar.addWidget(new_button)
        top_bar.addWidget(open_button)
        top_bar.addWidget(refresh_button)

        quickstart_group = QGroupBox("Quickstart")
        quickstart_layout = QHBoxLayout(quickstart_group)
        quick_build_button = QPushButton("Build")
        quick_build_button.clicked.connect(self.run_build)
        quick_simulate_button = QPushButton("Simulate")
        quick_simulate_button.clicked.connect(self.run_simulate_call)
        quick_deploy_button = QPushButton("Deploy")
        quick_deploy_button.clicked.connect(self.run_deploy)
        quick_interact_button = QPushButton("Interact")
        quick_interact_button.clicked.connect(self.run_interact)
        preflight_button = QPushButton("Preflight")
        preflight_button.clicked.connect(self.run_preflight)
        quickstart_layout.addWidget(quick_build_button)
        quickstart_layout.addWidget(quick_simulate_button)
        quickstart_layout.addWidget(quick_deploy_button)
        quickstart_layout.addWidget(quick_interact_button)
        quickstart_layout.addWidget(preflight_button)

        self.project_tree = ProjectTree(self)
        self.project_tree.fileOpenRequested.connect(self._open_file)
        self.project_tree.rootChanged.connect(self._on_workspace_root_changed)

        self.editor_tabs = EditorTabs(autosave_interval_ms=self.settings.autosave_interval_ms, parent=self)
        self.editor_tabs.fileOpened.connect(self._register_open_file)
        self.editor_tabs.fileClosed.connect(self._unregister_open_file)
        if not self.settings.autosave_enabled:
            self.editor_tabs.autosave_timer.stop()

        self.output_panels = OutputPanels(self)
        self.output_panels.problemActivated.connect(self._open_problem_location)

        inspector = QWidget()
        inspector_layout = QVBoxLayout(inspector)
        self.manifest_editor = ManifestEditor(inspector)
        inspector_layout.addWidget(self.manifest_editor)
        self.git_panel = GitPanel(inspector)
        inspector_layout.addWidget(self.git_panel)

        horizontal_split = QSplitter(Qt.Horizontal)
        horizontal_split.addWidget(self.project_tree)
        horizontal_split.addWidget(self.editor_tabs)
        horizontal_split.addWidget(inspector)
        horizontal_split.setStretchFactor(1, 2)

        vertical_split = QSplitter(Qt.Vertical)
        vertical_split.addWidget(horizontal_split)
        vertical_split.addWidget(self.output_panels)
        vertical_split.setStretchFactor(0, 3)
        vertical_split.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(top_bar)
        layout.addWidget(quickstart_group)
        layout.addWidget(vertical_split)

        self._setup_actions()
        self._connect_controller()
        self._restore_workspace()
        self._restore_open_tabs()
        self._sync_manifest_editor()

    def _setup_actions(self) -> None:
        self.command_palette_action = QAction("Command Palette", self)
        self.command_palette_action.setShortcut(QKeySequence("Ctrl+P"))
        self.command_palette_action.triggered.connect(self.open_command_palette)
        self.addAction(self.command_palette_action)

        self.build_action = QAction("Build Contract", self)
        self.build_action.setShortcut(QKeySequence("Ctrl+B"))
        self.build_action.triggered.connect(self.run_build)
        self.addAction(self.build_action)

        self.simulate_call_action = QAction("Simulate Call", self)
        self.simulate_call_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self.simulate_call_action.triggered.connect(self.run_simulate_call)
        self.addAction(self.simulate_call_action)

        self.simulate_tx_action = QAction("Simulate Tx", self)
        self.simulate_tx_action.setShortcut(QKeySequence("Ctrl+Shift+T"))
        self.simulate_tx_action.triggered.connect(self.run_simulate_tx)
        self.addAction(self.simulate_tx_action)

        self.deploy_action = QAction("Deploy", self)
        self.deploy_action.setShortcut(QKeySequence("Ctrl+Shift+D"))
        self.deploy_action.triggered.connect(self.run_deploy)
        self.addAction(self.deploy_action)

        self.preflight_action = QAction("Preflight", self)
        self.preflight_action.setShortcut(QKeySequence("Ctrl+Shift+F"))
        self.preflight_action.triggered.connect(self.run_preflight)
        self.addAction(self.preflight_action)

        self.save_action = QAction("Save", self)
        self.save_action.setShortcut(QKeySequence.Save)
        self.save_action.triggered.connect(self.editor_tabs.save_current)
        self.addAction(self.save_action)

        self.save_all_action = QAction("Save All", self)
        self.save_all_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.save_all_action.triggered.connect(self.editor_tabs.save_all)
        self.addAction(self.save_all_action)

        self.find_action = QAction("Find", self)
        self.find_action.setShortcut(QKeySequence.Find)
        self.find_action.triggered.connect(lambda: self.editor_tabs.toggle_find(True))
        self.addAction(self.find_action)

        self.replace_action = QAction("Replace", self)
        self.replace_action.setShortcut(QKeySequence("Ctrl+H"))
        self.replace_action.triggered.connect(lambda: self.editor_tabs.toggle_find(True))
        self.addAction(self.replace_action)

    def _connect_controller(self) -> None:
        self.controller.buildFinished.connect(self._on_build_finished)
        self.controller.preflightFinished.connect(self._on_preflight_finished)
        self.controller.deployFinished.connect(self._on_deploy_finished)
        self.controller.deployProgress.connect(self._on_deploy_progress)
        self.controller.simulateFinished.connect(self._on_simulation_finished)

    def _restore_workspace(self) -> None:
        if self.settings.last_workspace:
            self.workspace_picker.setCurrentText(self.settings.last_workspace)
            self.project_tree.set_root(self.settings.last_workspace)
            self.git_panel.set_workspace(self.settings.last_workspace)

    def _restore_open_tabs(self) -> None:
        for file_path in self.settings.open_files:
            path = Path(file_path)
            if path.exists():
                self.editor_tabs.open_file(path)
        if self.settings.active_file:
            self.editor_tabs.set_active_file(self.settings.active_file)

    def _load_recent_projects(self, projects: List[str]) -> None:
        self.workspace_picker.clear()
        for project in projects:
            self.workspace_picker.addItem(project)

    def _on_workspace_selected(self, path: str) -> None:
        if path:
            self.project_tree.set_root(path)
            self.settings.last_workspace = path
            self._add_recent_project(path)
            self._persist_settings()
            self._sync_manifest_editor()
            self.git_panel.set_workspace(path)

    def select_workspace(self) -> None:
        directory = self.project_tree.open_workspace_dialog()
        if directory:
            self.workspace_picker.setCurrentText(directory)

    def refresh_workspace(self) -> None:
        path = self.workspace_picker.currentText()
        if path:
            self.project_tree.set_root(path)
            self.git_panel.set_workspace(path)

    def _add_recent_project(self, path: str) -> None:
        if path in self.settings.recent_projects:
            self.settings.recent_projects.remove(path)
        self.settings.recent_projects.insert(0, path)
        self.settings.recent_projects = self.settings.recent_projects[:10]
        self._load_recent_projects(self.settings.recent_projects)

    def _open_file(self, path: str) -> None:
        self.editor_tabs.open_file(Path(path))

    def _register_open_file(self, path: str) -> None:
        if path not in self.settings.open_files:
            self.settings.open_files.append(path)
            self._persist_settings()

    def _unregister_open_file(self, path: str) -> None:
        if path in self.settings.open_files:
            self.settings.open_files.remove(path)
            self._persist_settings()

    def open_command_palette(self) -> None:
        commands = [
            PaletteCommand("Open File", self._command_open_file),
            PaletteCommand("Go to Line", self._command_go_to_line),
            PaletteCommand("Save", self.editor_tabs.save_current),
            PaletteCommand("Save All", self.editor_tabs.save_all),
            PaletteCommand("Find", lambda: self.editor_tabs.toggle_find(True)),
            PaletteCommand("Replace", lambda: self.editor_tabs.toggle_find(True)),
            PaletteCommand("Build Contract", self.run_build),
            PaletteCommand("Simulate Call", self.run_simulate_call),
            PaletteCommand("Simulate Tx", self.run_simulate_tx),
            PaletteCommand("Deploy Project", self.run_deploy),
            PaletteCommand("Preflight Check", self.run_preflight),
        ]
        palette = CommandPalette(commands, self)
        palette.exec()

    def _command_open_file(self) -> None:
        workspace = Path(self.workspace_picker.currentText())
        if not workspace.exists():
            QMessageBox.warning(self, "Open File", "Select a workspace first.")
            return
        files = [str(path.relative_to(workspace)) for path in workspace.rglob("*") if path.is_file()]
        if not files:
            QMessageBox.information(self, "Open File", "No files in workspace.")
            return
        choice, ok = QInputDialog.getItem(self, "Open File", "File:", files, 0, False)
        if ok and choice:
            self.editor_tabs.open_file(workspace / choice)

    def _command_go_to_line(self) -> None:
        editor = self.editor_tabs.current_editor()
        if not editor:
            return
        line, ok = QInputDialog.getInt(self, "Go to Line", "Line number:", 1, 1, 1000000)
        if ok:
            editor.go_to_line(line)

    def run_build(self) -> None:
        self.output_panels.append_output("Build", "Starting build...")
        self.output_panels.clear_problems()
        self.controller.build_project(self.workspace_picker.currentText())

    def run_preflight(self) -> None:
        workspace = self.workspace_picker.currentText()
        if not workspace:
            QMessageBox.warning(self, "Preflight", "Select a workspace first.")
            return
        self.output_panels.append_output("Preflight", "Running preflight checks...")
        self.output_panels.clear_problems()
        self.controller.preflight_project(workspace, self.rpc_client)

    def run_deploy(self) -> None:
        workspace = Path(self.workspace_picker.currentText())
        if not workspace.exists():
            QMessageBox.warning(self, "Deploy", "Select a valid workspace.")
            return
        if not self.rpc_client:
            QMessageBox.warning(self, "Deploy", "RPC client not connected. Start the local node first.")
            return
        if not self._rpc_is_local(self.rpc_client.rpc_url):
            QMessageBox.warning(self, "Deploy", "Deploy is restricted to the bundled local node (localhost RPC).")
            return

        build_result = build_contract(workspace)
        if not build_result.success or not build_result.artifacts:
            self._on_build_finished(build_result)
            return

        wallet_path = self._resolve_wallet_path()
        wallets = load_wallet_entries(wallet_path)
        if not wallets:
            QMessageBox.warning(self, "Deploy", "No wallets found. Configure a wallet first.")
            return

        dialog = DeployDialog(
            build_result=build_result,
            wallets=wallets,
            network=self._current_network_label(),
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        if not self._ensure_sync_ready():
            return

        if not self._warn_if_no_peers():
            return

        options = dialog.options(self.settings.explorer_url)
        if not self._confirm_deploy(options, build_result):
            return

        self.output_panels.append_output("Deploy", "Starting deploy...")
        self.controller.deploy_project(
            str(workspace),
            rpc_client=self.rpc_client,
            wallet_path=wallet_path,
            options=options,
        )

    def _on_build_finished(self, result) -> None:
        status = "✅" if result.success else "❌"
        self.output_panels.append_output("Build", f"{status} {result.message}")
        if result.artifacts:
            self.output_panels.append_output(
                "Build",
                f"Artifacts: {result.artifacts.manifest_path} (hash {result.artifacts.code_hash})",
            )
        diagnostics: List[Diagnostic] = result.diagnostics or []
        if diagnostics:
            self.output_panels.set_problems(diagnostics)
            for diag in diagnostics:
                self.output_panels.append_output("Build", f"⚠️ {diag.display_text()}")

    def _on_preflight_finished(self, result: PreflightResult) -> None:
        status = "✅" if result.ok else "❌"
        self.output_panels.append_output("Preflight", f"{status} {result.message}")
        for check in result.checks:
            icon = "✅" if check.ok else "❌"
            self.output_panels.append_output("Preflight", f"{icon} {check.name}: {check.message}")
        diagnostics = list(result.diagnostics or [])
        if diagnostics:
            self.output_panels.set_problems(diagnostics)
            for diag in diagnostics:
                label = "ℹ️" if diag.severity == "info" else "⚠️"
                self.output_panels.append_output("Preflight", f"{label} {diag.display_text()}")

    def _on_deploy_finished(self, success: bool, message: str, result_obj: object) -> None:
        status = "✅" if success else "❌"
        self.output_panels.append_output("Deploy", f"{status} {message}")
        if isinstance(result_obj, DeploymentResult) and result_obj.tx_hash:
            self.output_panels.append_output("Deploy", f"Tx hash: {result_obj.tx_hash}")
            if result_obj.contract_address:
                self.output_panels.append_output("Deploy", f"Contract: {result_obj.contract_address}")
            if result_obj.block_height is not None:
                self.output_panels.append_output("Deploy", f"Block height: {result_obj.block_height}")
            self._show_deploy_result_dialog(result_obj)

    def _on_deploy_progress(self, message: str) -> None:
        self.output_panels.append_output("Deploy", message)

    def run_simulate_call(self) -> None:
        self._run_simulation(is_tx=False)

    def run_simulate_tx(self) -> None:
        self._run_simulation(is_tx=True)

    def run_interact(self) -> None:
        if not self.rpc_client:
            QMessageBox.warning(self, "Interact", "RPC client not connected. Start the local node first.")
            return
        manifest, abi, _ = self._load_project_manifest()
        if not manifest or not abi:
            return
        functions = [fn.get("name") for fn in abi.get("functions", []) if fn.get("name")]
        if not functions:
            QMessageBox.warning(self, "Interact", "No ABI functions found.")
            return
        dialog = InteractDialog(functions=functions, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        address, method, args = dialog.values
        try:
            result = self.rpc_client.call_contract(address=address, method=method, args=args, abi=abi)
        except Exception as exc:
            QMessageBox.warning(self, "Interact", f"RPC call failed: {exc}")
            return
        payload = canonical_json_str(result)
        self.output_panels.append_output("Console", payload)

    def _run_simulation(self, *, is_tx: bool) -> None:
        manifest, abi, source_path = self._load_project_manifest()
        if not manifest or not abi or not source_path:
            return
        functions = [fn.get("name") for fn in abi.get("functions", []) if fn.get("name")]
        if not functions:
            QMessageBox.warning(self, "Simulate", "No ABI functions found.")
            return
        dialog = SimulationDialog(functions=functions, is_tx=is_tx, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        method, args, tx_env = dialog.values
        self.output_panels.append_output(
            "Simulation",
            f"Running {'tx' if is_tx else 'call'} {method}...",
        )
        manifest_with_source = dict(manifest)
        manifest_with_source["source"] = str(source_path)
        manifest_with_source["abi"] = abi
        if is_tx:
            self.controller.run_simulation_tx(manifest_with_source, method, args, tx_env)
        else:
            self.controller.run_simulation_call(manifest_with_source, method, args)

    def _on_simulation_finished(self, kind: str, result) -> None:
        status = "✅" if result.ok else "❌"
        self.output_panels.append_output("Simulation", f"{status} {result.message}")
        if result.payload:
            payload = canonical_json_str(result.payload)
            self.output_panels.append_output("Simulation", payload)

    def _on_rpc_changed(self, rpc_url: str, token: str) -> None:
        self.rpc_client = RPCClient(rpc_url, token=token)

    def _open_problem_location(self, path: str, line: int, column: int) -> None:
        self.editor_tabs.open_file_at(Path(path), line, column)

    def _load_project_manifest(
        self,
    ) -> tuple[Optional[dict], Optional[dict], Optional[Path]]:
        workspace = Path(self.workspace_picker.currentText())
        manifest_path = resolve_manifest_path(workspace)
        if not manifest_path:
            QMessageBox.warning(self, "Manifest", "No manifest.json found.")
            return None, None, None
        try:
            manifest = load_manifest(manifest_path)
            source_path = resolve_source_path(manifest, manifest_path)
            abi = resolve_abi(manifest, manifest_path)
            if not abi:
                raise ManifestLoadError("Manifest missing ABI definition")
        except ManifestLoadError as exc:
            QMessageBox.warning(self, "Manifest", str(exc))
            return None, None, None
        return manifest, abi, source_path

    def _resolve_wallet_path(self) -> Path:
        if self.config and self.config.miner.wallet_file:
            return Path(self.config.miner.wallet_file).expanduser()
        return Path.home() / ".animica" / "wallets.json"

    def _current_network_label(self) -> str:
        if not self.config:
            return "devnet"
        network_type = self.config.network.network_type
        if isinstance(network_type, NetworkType):
            return network_type.value if network_type != NetworkType.CUSTOM else "devnet"
        return str(network_type or "devnet")

    def _rpc_is_local(self, rpc_url: str) -> bool:
        try:
            parsed = urlparse(rpc_url)
        except Exception:
            return False
        host = parsed.hostname or ""
        return host in {"127.0.0.1", "localhost"}

    def _ensure_sync_ready(self) -> bool:
        if not self.rpc_client:
            return False
        sync = self.rpc_client.get_sync_status()
        syncing = bool(sync.get("syncing"))
        current = sync.get("currentBlock") or sync.get("current_block")
        highest = sync.get("highestBlock") or sync.get("highest_block")
        if highest is not None and current is not None and int(current) < int(highest):
            syncing = True
        if not syncing:
            return True
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Sync Required")
        dialog.setText("Node is still syncing. Deployments require a synced node.")
        open_button = dialog.addButton("Open Sync Panel", QMessageBox.ActionRole)
        dialog.addButton("Cancel", QMessageBox.RejectRole)
        dialog.exec()
        if dialog.clickedButton() == open_button:
            self._open_sync_panel()
        return False

    def _open_sync_panel(self) -> None:
        window = self.window()
        if not window:
            return
        tabs = getattr(window, "tabs", None)
        if tabs is None:
            return
        for idx in range(tabs.count()):
            if tabs.tabText(idx) == "Dashboard":
                tabs.setCurrentIndex(idx)
                return

    def _warn_if_no_peers(self) -> bool:
        if not self.rpc_client:
            return False
        peers = self.rpc_client.get_peer_summary()
        total = peers.get("total")
        if total not in (None, 0):
            return True
        choice = QMessageBox.warning(
            self,
            "No Peers Connected",
            "Peer count is 0. You can still deploy locally, but the transaction may not propagate.",
            QMessageBox.Ok | QMessageBox.Cancel,
        )
        return choice == QMessageBox.Ok

    def _confirm_deploy(self, options: DeploymentOptions, build_result) -> bool:
        manifest = build_result.manifest or {}
        summary = [
            f"Network: {options.network}",
            f"From: {options.from_address}",
            f"Max fee: {options.max_fee}",
            f"Gas limit: {options.gas_limit or 'auto'}",
            f"Contract: {manifest.get('name', 'Unknown')}@{manifest.get('version', '0.0.0')}",
            f"Code hash: {build_result.artifacts.code_hash}",
        ]
        confirm = QMessageBox.question(
            self,
            "Confirm Deploy",
            "Sign and deploy this contract?\n\n" + "\n".join(summary),
            QMessageBox.Yes | QMessageBox.No,
        )
        return confirm == QMessageBox.Yes

    def _show_deploy_result_dialog(self, result: DeploymentResult) -> None:
        if not result.tx_hash:
            return
        explorer_url = (self.settings.explorer_url or "").strip().rstrip("/")
        dialog = QDialog(self)
        dialog.setWindowTitle("Deployment Result")
        layout = QVBoxLayout(dialog)

        tx_label = QLabel(f"Tx hash: {result.tx_hash}")
        tx_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(tx_label)

        if result.contract_address:
            addr_label = QLabel(f"Contract: {result.contract_address}")
            addr_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(addr_label)

        if result.block_height is not None:
            layout.addWidget(QLabel(f"Block height: {result.block_height}"))

        if explorer_url:
            tx_link = f"{explorer_url}/tx/{result.tx_hash}"
            tx_link_label = QLabel(f'<a href="{tx_link}">View transaction</a>')
            tx_link_label.setOpenExternalLinks(True)
            layout.addWidget(tx_link_label)
            if result.contract_address:
                contract_link = f"{explorer_url}/contract/{result.contract_address}"
                contract_label = QLabel(f'<a href="{contract_link}">View contract</a>')
                contract_label.setOpenExternalLinks(True)
                layout.addWidget(contract_label)

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    def _sync_manifest_editor(self) -> None:
        workspace = self.workspace_picker.currentText()
        if not workspace:
            self.manifest_editor.set_manifest_path(None)
            return
        manifest_path = resolve_manifest_path(Path(workspace))
        self.manifest_editor.set_manifest_path(manifest_path)

    def _on_workspace_root_changed(self, path: str) -> None:
        self.git_panel.set_workspace(path)

    def prompt_close(self) -> bool:
        if not self.editor_tabs.close_all():
            return False
        self.settings.open_files = self.editor_tabs.open_files()
        self.settings.active_file = self.editor_tabs.active_file()
        self._persist_settings()
        return True

    def _persist_settings(self) -> None:
        self.settings.open_files = self.editor_tabs.open_files()
        self.settings.active_file = self.editor_tabs.active_file()
        save_ide_settings(self.settings)

    def open_new_project_wizard(self) -> None:
        dialog = NewProjectDialog(parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        workspace_path = dialog.created_path
        if workspace_path:
            self.workspace_picker.setCurrentText(str(workspace_path))
            self.editor_tabs.open_file(workspace_path / "contract.py")


class DeployDialog(QDialog):
    """Dialog to configure contract deployment options."""

    def __init__(
        self,
        *,
        build_result: BuildResult,
        wallets: List[WalletEntry],
        network: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Deploy Contract")
        self._wallets = wallets
        self._default_max_fee = 2_000_000

        summary_group = QGroupBox("Artifact Summary")
        summary_layout = QFormLayout(summary_group)
        manifest = build_result.manifest or {}
        summary_layout.addRow("Name:", QLabel(str(manifest.get("name", "Unknown"))))
        summary_layout.addRow("Version:", QLabel(str(manifest.get("version", "0.0.0"))))
        summary_layout.addRow("Entry:", QLabel(str(manifest.get("entry", "contract.py"))))
        summary_layout.addRow("Code hash:", QLabel(build_result.artifacts.code_hash))

        self.network_picker = QComboBox()
        self.network_picker.addItems(["mainnet", "testnet", "devnet"])
        if network in {"mainnet", "testnet", "devnet"}:
            self.network_picker.setCurrentText(network)

        self.from_picker = QComboBox()
        for entry in wallets:
            label = entry.label or "Wallet"
            self.from_picker.addItem(f"{label} — {entry.address}", entry.address)

        advanced_group = QGroupBox("Advanced (fee/gas)")
        advanced_group.setCheckable(True)
        advanced_group.setChecked(False)
        advanced_layout = QFormLayout(advanced_group)

        self.gas_input = QLineEdit()
        self.gas_input.setPlaceholderText("auto")
        self.max_fee_input = QLineEdit(str(self._default_max_fee))

        advanced_layout.addRow("Gas limit:", self.gas_input)
        advanced_layout.addRow("Max fee:", self.max_fee_input)

        deploy_button = QPushButton("Deploy")
        deploy_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(deploy_button)

        layout = QVBoxLayout(self)
        layout.addWidget(summary_group)

        form_group = QGroupBox("Deployment")
        form_layout = QFormLayout(form_group)
        form_layout.addRow("Network:", self.network_picker)
        form_layout.addRow("From address:", self.from_picker)
        layout.addWidget(form_group)

        layout.addWidget(advanced_group)
        layout.addLayout(button_row)

    def options(self, explorer_url: str) -> DeploymentOptions:
        gas_limit = None
        gas_text = self.gas_input.text().strip()
        if gas_text:
            try:
                gas_limit = int(gas_text)
            except ValueError:
                gas_limit = None

        max_fee = self._default_max_fee
        max_fee_text = self.max_fee_input.text().strip()
        if max_fee_text:
            try:
                max_fee = int(max_fee_text)
            except ValueError:
                max_fee = self._default_max_fee

        return DeploymentOptions(
            from_address=str(self.from_picker.currentData()),
            network=self.network_picker.currentText(),
            gas_limit=gas_limit,
            max_fee=max_fee,
            explorer_url=explorer_url.strip() if explorer_url else None,
        )


class SimulationDialog(QDialog):
    """Dialog for running a call or tx simulation."""

    def __init__(self, functions: List[str], is_tx: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Simulate Tx" if is_tx else "Simulate Call")
        self.values: tuple[str, dict, dict] = ("", {}, {})

        self.method_picker = QComboBox()
        self.method_picker.addItems(functions)

        self.args_editor = QPlainTextEdit("{}")
        self.args_editor.setPlaceholderText("Args JSON")
        self.tx_editor = QPlainTextEdit("{}")
        self.tx_editor.setPlaceholderText("Tx env JSON")
        self.tx_editor.setVisible(is_tx)

        run_button = QPushButton("Run")
        run_button.clicked.connect(self._accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Method"))
        layout.addWidget(self.method_picker)
        layout.addWidget(QLabel("Arguments (JSON)"))
        layout.addWidget(self.args_editor)
        if is_tx:
            layout.addWidget(QLabel("Tx Env (JSON)"))
            layout.addWidget(self.tx_editor)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(run_button)
        layout.addLayout(button_row)

    def _accept(self) -> None:
        try:
            args = json.loads(self.args_editor.toPlainText() or "{}")
            tx_env = json.loads(self.tx_editor.toPlainText() or "{}")
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "Simulation", f"Invalid JSON: {exc}")
            return
        if not isinstance(args, dict):
            QMessageBox.warning(self, "Simulation", "Arguments must be a JSON object.")
            return
        if not isinstance(tx_env, dict):
            QMessageBox.warning(self, "Simulation", "Tx env must be a JSON object.")
            return
        self.values = (self.method_picker.currentText(), args, tx_env)
        self.accept()


class InteractDialog(QDialog):
    """Dialog for calling a deployed contract via RPC."""

    def __init__(self, functions: List[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Interact with Contract")
        self.values: tuple[str, str, list] = ("", "", [])

        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText("Contract address")
        self.method_picker = QComboBox()
        self.method_picker.addItems(functions)
        self.args_editor = QPlainTextEdit("[]")
        self.args_editor.setPlaceholderText("Args JSON array (e.g., [1, \"hi\"])")

        run_button = QPushButton("Call")
        run_button.clicked.connect(self._accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Contract address"))
        layout.addWidget(self.address_input)
        layout.addWidget(QLabel("Method"))
        layout.addWidget(self.method_picker)
        layout.addWidget(QLabel("Arguments (JSON array)"))
        layout.addWidget(self.args_editor)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(run_button)
        layout.addLayout(button_row)

    def _accept(self) -> None:
        address = self.address_input.text().strip()
        if not address:
            QMessageBox.warning(self, "Interact", "Contract address is required.")
            return
        try:
            args = json.loads(self.args_editor.toPlainText() or "[]")
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "Interact", f"Invalid JSON: {exc}")
            return
        if not isinstance(args, list):
            QMessageBox.warning(self, "Interact", "Arguments must be a JSON array.")
            return
        self.values = (address, self.method_picker.currentText(), args)
        self.accept()


class NewProjectDialog(QDialog):
    """Wizard to scaffold a new contract project."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Contract Project")
        self.created_path: Optional[Path] = None

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Counter")
        self.location_input = QLineEdit()
        self.location_input.setText(str(Path.home()))
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self._browse)

        form = QFormLayout()
        form.addRow("Project name:", self.name_input)

        location_row = QHBoxLayout()
        location_row.addWidget(self.location_input)
        location_row.addWidget(browse_button)
        form.addRow("Location:", location_row)

        create_button = QPushButton("Create")
        create_button.clicked.connect(self._create)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(create_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(button_row)

    def _browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Location")
        if directory:
            self.location_input.setText(directory)

    def _create(self) -> None:
        name = self.name_input.text().strip() or "Counter"
        location = Path(self.location_input.text().strip() or Path.home())
        slug = _slugify(name)
        workspace = location / slug
        if workspace.exists():
            QMessageBox.warning(self, "New Project", f"{workspace} already exists.")
            return
        try:
            workspace.mkdir(parents=True, exist_ok=False)
            (workspace / "contract.py").write_text(_default_contract(name), encoding="utf-8")
            (workspace / "manifest.json").write_text(_default_manifest(name), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "New Project", f"Failed to create project: {exc}")
            return
        self.created_path = workspace
        self.accept()


def _slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return slug or "contract"


def _default_contract(name: str) -> str:
    return (
        "# -*- coding: utf-8 -*-\n"
        f'"""Deterministic {name} contract scaffold."""\n'
        "from __future__ import annotations\n\n"
        "from stdlib import abi, storage\n\n"
        "KEY_COUNTER = b\"counter\"\n\n"
        "def _get_counter() -> int:\n"
        "    data = storage.get(KEY_COUNTER)\n"
        "    if data is None:\n"
        "        return 0\n"
        "    return abi.decode_int(data)\n\n"
        "def get() -> int:\n"
        "    \"\"\"Return the current counter value.\"\"\"\n"
        "    return _get_counter()\n\n"
        "def increment() -> int:\n"
        "    \"\"\"Increment the counter and return the new value.\"\"\"\n"
        "    value = _get_counter() + 1\n"
        "    storage.set(KEY_COUNTER, abi.encode_int(value))\n"
        "    return value\n"
    )


def _default_manifest(name: str) -> str:
    safe_name = "".join(ch for ch in name if ch.isalnum() or ch == "_") or "Counter"
    manifest = {
        "name": safe_name,
        "version": "0.1.0",
        "language": "python",
        "source": "contract.py",
        "abi": {
            "abiVersion": 1,
            "name": safe_name,
            "functions": [
                {
                    "name": "get",
                    "inputs": [],
                    "outputs": [{"name": "value", "type": "int"}],
                    "stateMutability": "view",
                },
                {
                    "name": "increment",
                    "inputs": [],
                    "outputs": [{"name": "value", "type": "int"}],
                    "stateMutability": "nonpayable",
                },
            ],
        },
    }
    return json.dumps(manifest, indent=2)
