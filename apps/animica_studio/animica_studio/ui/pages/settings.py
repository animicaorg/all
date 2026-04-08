"""Settings page grouped into Basic, Advanced, and Developer sections."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.models.profile_models import ProfileType
from animica_studio.services.build_info_service import collect_build_info
from animica_studio.services.job_runner import resolve_animica_cli
from animica_studio.services.profile_service import ProfileService
from animica_studio.services.settings_service import SettingsService
from animica_studio.services.studio_status_service import StudioStatusService
from animica_studio.services.workers import run_in_threadpool
from animica_studio.storage.config import Config, save_config
from animica_studio.ui.theme.theme_manager import ThemeManager

log = logging.getLogger(__name__)


class SettingsPage(QWidget):
    rerun_onboarding_requested = Signal()
    open_logs_requested = Signal()

    def __init__(
        self,
        config: Config | None = None,
        theme_manager: ThemeManager | None = None,
        parent: QWidget | None = None,
        *,
        profile_service: ProfileService | None = None,
        settings_service: SettingsService | None = None,
        status_service: StudioStatusService | None = None,
    ) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415

        self._config = config or load_config()
        self._theme_manager = theme_manager
        self._profile_service = profile_service or ProfileService(self._config)
        self._settings = settings_service or SettingsService(self._config)
        self._status_service = status_service or StudioStatusService(self._config, self._settings)
        self._diag_job = None
        self._developer_loaded = False
        self._build_ui()
        self._load_from_profile()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 28px; font-weight: 700;")
        subtitle = QLabel("Keep the basics simple, and put deeper diagnostics behind one clear place.")
        subtitle.setStyleSheet("color: #8f99a5;")
        root.addWidget(title)
        root.addWidget(subtitle)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_basic_tab(), "Basic")
        self._tabs.addTab(self._build_advanced_tab(), "Advanced")
        self._tabs.addTab(self._build_developer_tab(), "Developer")
        root.addWidget(self._tabs, 1)

        actions = QHBoxLayout()
        self._status = QLabel("")
        self._status.setStyleSheet("color: #8f99a5;")
        actions.addWidget(self._status, 1)
        save_btn = QPushButton("Save Settings")
        save_btn.clicked.connect(self._save)
        actions.addWidget(save_btn)
        root.addLayout(actions)

    def _build_basic_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        network_box = QGroupBox("Connection")
        form = QFormLayout(network_box)
        self._network = QComboBox()
        self._network.addItem("Custom", "custom")
        for preset in self._settings.network_presets():
            self._network.addItem(preset.label, preset.key)
        self._network.currentIndexChanged.connect(self._apply_network_selection)
        form.addRow("Network", self._network)

        self._mode = QComboBox()
        self._mode.addItem("Managed local node", ProfileType.LOCAL_NODE.value)
        self._mode.addItem("External RPC", ProfileType.REMOTE_RPC.value)
        self._mode.currentIndexChanged.connect(self._apply_network_selection)
        form.addRow("Mode", self._mode)

        self._wallet_path = QLabel(str(Path.home() / ".animica" / "wallets.json"))
        self._wallet_path.setTextInteractionFlags(
            self._wallet_path.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        form.addRow("Wallet store", self._wallet_path)

        self._theme_mode = QComboBox()
        self._theme_mode.addItems(["dark", "light"])
        form.addRow("Theme", self._theme_mode)
        layout.addWidget(network_box)

        common_box = QGroupBox("Common Preferences")
        common_form = QFormLayout(common_box)
        self._stop_node_on_exit = QCheckBox("Stop Studio-managed node when the app closes")
        common_form.addRow("", self._stop_node_on_exit)
        self._use_repo_venv = QCheckBox("Use the repo .venv automatically when resolving the Animica CLI")
        common_form.addRow("", self._use_repo_venv)
        layout.addWidget(common_box)
        layout.addStretch(1)
        return page

    def _build_advanced_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        rpc_box = QGroupBox("RPC and Node")
        rpc_form = QFormLayout(rpc_box)
        self._rpc_url = QLineEdit()
        rpc_form.addRow("RPC URL", self._rpc_url)
        self._explorer_url = QLineEdit()
        rpc_form.addRow("Explorer URL", self._explorer_url)
        self._chain_id = QLineEdit()
        rpc_form.addRow("Chain ID", self._chain_id)
        self._node_cmd = QLineEdit()
        rpc_form.addRow("Node start command", self._node_cmd)
        self._node_datadir = QLineEdit()
        rpc_form.addRow("Node data directory", self._node_datadir)
        test_btn = QPushButton("Test RPC")
        test_btn.clicked.connect(self._test_rpc)
        rpc_form.addRow("", test_btn)
        layout.addWidget(rpc_box)

        tooling_box = QGroupBox("Tooling and Paths")
        tooling_form = QFormLayout(tooling_box)
        self._repo_root = QLineEdit()
        repo_row = QHBoxLayout()
        repo_row.addWidget(self._repo_root)
        repo_browse = QPushButton("Browse…")
        repo_browse.clicked.connect(lambda: self._browse_dir(self._repo_root, "Select Repository Root"))
        repo_row.addWidget(repo_browse)
        tooling_form.addRow("Repository root", repo_row)

        self._cli_path = QLineEdit()
        cli_row = QHBoxLayout()
        cli_row.addWidget(self._cli_path)
        cli_browse = QPushButton("Browse…")
        cli_browse.clicked.connect(self._browse_cli)
        cli_row.addWidget(cli_browse)
        tooling_form.addRow("CLI path override", cli_row)

        self._da_path = QLineEdit()
        da_row = QHBoxLayout()
        da_row.addWidget(self._da_path)
        da_browse = QPushButton("Browse…")
        da_browse.clicked.connect(lambda: self._browse_dir(self._da_path, "Select DA Storage Directory"))
        da_row.addWidget(da_browse)
        tooling_form.addRow("DA storage path", da_row)
        layout.addWidget(tooling_box)

        ena_box = QGroupBox("ENA")
        ena_form = QFormLayout(ena_box)
        self._ena_provider = QComboBox()
        self._ena_provider.addItems(["local", "remote"])
        ena_form.addRow("Provider", self._ena_provider)
        self._ena_endpoint = QLineEdit()
        ena_form.addRow("Remote endpoint", self._ena_endpoint)
        self._ena_model = QLineEdit()
        ena_form.addRow("Remote model", self._ena_model)
        layout.addWidget(ena_box)
        layout.addStretch(1)
        return page

    def _build_developer_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        env_box = QGroupBox("Runtime")
        env_form = QFormLayout(env_box)
        self._version = QLabel("")
        self._resolved_cli = QLabel("")
        self._resolved_cli.setWordWrap(True)
        self._runtime = QLabel("")
        self._runtime.setWordWrap(True)
        env_form.addRow("Version", self._version)
        env_form.addRow("Resolved CLI", self._resolved_cli)
        env_form.addRow("Runtime", self._runtime)
        layout.addWidget(env_box)

        diag_box = QGroupBox("Developer Tools")
        diag_layout = QVBoxLayout(diag_box)
        self._diag_summary = QTextEdit()
        self._diag_summary.setReadOnly(True)
        self._diag_summary.setMinimumHeight(220)
        diag_layout.addWidget(self._diag_summary)
        button_row = QHBoxLayout()
        open_logs = QPushButton("Open Logs")
        open_logs.clicked.connect(self.open_logs_requested.emit)
        button_row.addWidget(open_logs)
        onboarding = QPushButton("Rerun Onboarding")
        onboarding.clicked.connect(self._rerun_onboarding)
        button_row.addWidget(onboarding)
        refresh = QPushButton("Refresh Diagnostics")
        refresh.clicked.connect(self._refresh_developer_info)
        button_row.addWidget(refresh)
        button_row.addStretch(1)
        diag_layout.addLayout(button_row)
        layout.addWidget(diag_box)
        layout.addStretch(1)
        return page

    def _load_from_profile(self) -> None:
        profile = self._profile_service.get_active()
        network_key = self._settings.detect_network(profile)
        idx = self._network.findData(network_key)
        self._network.setCurrentIndex(max(0, idx))
        mode_idx = self._mode.findData(profile.type.value)
        self._mode.setCurrentIndex(max(0, mode_idx))
        self._rpc_url.setText(profile.effective_rpc_url())
        self._explorer_url.setText(profile.explorer_base_url)
        self._chain_id.setText(str(profile.chain_id_expected))
        self._node_cmd.setText(" ".join(profile.node_start_cmd or ["animica", "node", "start"]))
        self._node_datadir.setText(profile.node_datadir or "")
        self._repo_root.setText(self._config.repo_root or "")
        self._cli_path.setText(self._config.cli_path_override or "")
        da_cfg = self._config.da_contribution if isinstance(self._config.da_contribution, dict) else {}
        self._da_path.setText(str(da_cfg.get("studio_contrib_dir") or da_cfg.get("studio_dir") or ""))
        ena = self._config.ena if isinstance(self._config.ena, dict) else {}
        remote = ena.get("remote") if isinstance(ena.get("remote"), dict) else {}
        self._ena_provider.setCurrentText(str(ena.get("provider") or "local"))
        self._ena_endpoint.setText(str(remote.get("endpoint") or ""))
        self._ena_model.setText(str(remote.get("model") or ""))
        self._stop_node_on_exit.setChecked(bool(self._config.stop_node_on_exit))
        self._use_repo_venv.setChecked(bool(self._config.use_repo_venv_automatically))
        if self._theme_manager:
            self._theme_mode.setCurrentText(self._theme_manager.mode())
        self._refresh_runtime_info()

    def _apply_network_selection(self) -> None:
        profile = self._profile_service.get_active()
        preset_key = str(self._network.currentData() or "custom")
        local_node = str(self._mode.currentData() or "") == ProfileType.LOCAL_NODE.value
        if preset_key == "custom":
            return
        profile = self._settings.apply_network_preset(profile, preset_key, local_node=local_node)
        rpc_value = profile.node_rpc_url if local_node else profile.rpc_url
        self._rpc_url.setText(rpc_value or profile.effective_rpc_url())
        self._chain_id.setText(str(profile.chain_id_expected))
        self._explorer_url.setText(profile.explorer_base_url)
        self._node_cmd.setText(" ".join(profile.node_start_cmd or ["animica", "node", "start"]))
        self._node_datadir.setText(profile.node_datadir or "")

    def _save(self) -> None:
        profile = self._profile_service.get_active()
        profile.type = ProfileType(str(self._mode.currentData() or ProfileType.REMOTE_RPC.value))
        if profile.type == ProfileType.LOCAL_NODE:
            profile.node_rpc_url = self._rpc_url.text().strip()
        else:
            profile.rpc_url = self._rpc_url.text().strip()
        try:
            profile = self._settings.save_active_profile_settings(
                profile,
                rpc_url=self._rpc_url.text().strip(),
                explorer_url=self._explorer_url.text().strip(),
                chain_id=int(self._chain_id.text().strip() or "1"),
                node_start_cmd=self._node_cmd.text().strip(),
                node_datadir=self._node_datadir.text().strip(),
            )
        except Exception as exc:  # noqa: BLE001
            self._status.setText(str(exc))
            return
        self._profile_service.update_profile(profile)
        self._config.repo_root = self._repo_root.text().strip() or None
        self._config.cli_path_override = self._cli_path.text().strip() or None
        self._config.use_repo_venv_automatically = self._use_repo_venv.isChecked()
        self._config.stop_node_on_exit = self._stop_node_on_exit.isChecked()
        da_cfg = dict(self._config.da_contribution or {})
        da_cfg["studio_contrib_dir"] = self._da_path.text().strip() or da_cfg.get("studio_contrib_dir") or ""
        da_cfg["studio_dir"] = da_cfg["studio_contrib_dir"]
        self._config.da_contribution = da_cfg
        ena = dict(self._config.ena or {})
        remote = dict(ena.get("remote") or {})
        remote["endpoint"] = self._ena_endpoint.text().strip()
        remote["model"] = self._ena_model.text().strip()
        ena["provider"] = self._ena_provider.currentText()
        ena["remote"] = remote
        self._config.ena = ena
        if self._theme_manager:
            self._theme_manager.set_mode(self._theme_mode.currentText())
        save_config(self._config)
        self._status.setText("Settings saved.")
        self._refresh_developer_info()

    def _test_rpc(self) -> None:
        self._status.setText("Testing RPC…")
        job = run_in_threadpool(self._status_service.test_rpc, self._rpc_url.text().strip())
        job.signals.result.connect(lambda result: self._status.setText(f"{result.summary} {result.details}".strip()))
        job.signals.error.connect(lambda message, _tb: self._status.setText(message))

    def _refresh_developer_info(self) -> None:
        self._refresh_runtime_info()
        self._refresh_diagnostics_async()

    def _refresh_runtime_info(self) -> None:
        build = collect_build_info()
        self._version.setText(build.app_version)
        resolved = resolve_animica_cli(self._config)
        self._resolved_cli.setText(" ".join(resolved.argv_prefix) if resolved.argv_prefix else (resolved.error or "CLI unresolved"))
        self._runtime.setText(
            f"{build.python_version} | {'packaged' if build.packaged else 'repo run'} | {build.platform_label}\n"
            f"Repo: {build.repo_root or 'n/a'}"
        )

    def _refresh_diagnostics_async(self) -> None:
        if self._diag_job is not None:
            return
        self._diag_summary.setPlainText("Loading diagnostics…")
        self._diag_job = run_in_threadpool(self._status_service.sync_diagnostics_text)
        self._diag_job.signals.result.connect(self._diag_summary.setPlainText)
        self._diag_job.signals.error.connect(lambda message, _tb: self._diag_summary.setPlainText(message))
        self._diag_job.signals.finished.connect(lambda: setattr(self, "_diag_job", None))

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if not self._developer_loaded:
            self._developer_loaded = True
            QTimer.singleShot(0, self._refresh_developer_info)

    def _rerun_onboarding(self) -> None:
        self._settings.rerun_onboarding()
        self.rerun_onboarding_requested.emit()
        self._status.setText("Onboarding will run again.")

    def _browse_dir(self, field: QLineEdit, title: str) -> None:
        current = field.text().strip() or ""
        selected = QFileDialog.getExistingDirectory(self, title, current)
        if selected:
            field.setText(selected)

    def _browse_cli(self) -> None:
        current = self._cli_path.text().strip() or ""
        selected, _ = QFileDialog.getOpenFileName(self, "Select Animica CLI", current)
        if selected:
            self._cli_path.setText(selected)
