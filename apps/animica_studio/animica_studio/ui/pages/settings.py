"""Settings page — profile configuration + RPC test + diagnostics panel."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QFileDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.diagnostics import diagnostics
from animica_studio.services.workers import WorkerThread
from animica_studio.services.job_runner import resolve_animica_cli
from animica_studio.storage.config import Config, save_config
from animica_studio.ui.theme.theme_manager import ThemeManager
from animica_studio.util.qt import qthread_running, stop_thread

log = logging.getLogger(__name__)


class SettingsPage(QWidget):
    def __init__(
        self,
        config: Config | None = None,
        theme_manager: ThemeManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415

        self._config = config or load_config()
        self._theme_manager = theme_manager
        self._worker_thread: WorkerThread | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        profile_group = QGroupBox("Active Profile")
        form = QFormLayout(profile_group)
        profile = self._config.get_active_profile()

        self._rpc_url_edit = QLineEdit(profile.rpc_url)
        form.addRow("RPC URL:", self._rpc_url_edit)
        self._chain_id_edit = QLineEdit(str(profile.chain_id_expected))
        form.addRow("Chain ID:", self._chain_id_edit)
        self._start_cmd_edit = QLineEdit(" ".join(profile.node.start_cmd))
        form.addRow("Node start cmd:", self._start_cmd_edit)
        layout.addWidget(profile_group)

        cli_group = QGroupBox("CLI")
        cform = QFormLayout(cli_group)
        self._repo_root_edit = QLineEdit(self._config.repo_root or "")
        repo_row = QHBoxLayout()
        repo_row.addWidget(self._repo_root_edit)
        repo_browse = QPushButton("Browse…")
        repo_browse.clicked.connect(self._browse_repo_root)
        repo_row.addWidget(repo_browse)
        cform.addRow("Repo root:", repo_row)

        self._cli_path_edit = QLineEdit(self._config.cli_path_override or "")
        cli_row = QHBoxLayout()
        cli_row.addWidget(self._cli_path_edit)
        cli_browse = QPushButton("Browse…")
        cli_browse.clicked.connect(self._browse_cli_path)
        cli_row.addWidget(cli_browse)
        cform.addRow("CLI path override:", cli_row)

        self._use_repo_venv = QCheckBox("Use repo .venv automatically")
        self._use_repo_venv.setChecked(self._config.use_repo_venv_automatically)
        cform.addRow("", self._use_repo_venv)

        self._resolved_cli_label = QLabel("")
        cform.addRow("Resolved CLI:", self._resolved_cli_label)
        layout.addWidget(cli_group)

        self._repo_root_edit.textChanged.connect(self._refresh_cli_resolution_label)
        self._cli_path_edit.textChanged.connect(self._refresh_cli_resolution_label)
        self._use_repo_venv.toggled.connect(self._refresh_cli_resolution_label)

        appearance = QGroupBox("Appearance")
        aform = QFormLayout(appearance)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["dark", "light"])
        self._accent_combo = QComboBox()
        self._accent_combo.addItems(["#5b8cff", "#8b5cf6", "#14b8a6"])
        self._effects_combo = QComboBox()
        self._effects_combo.addItems(["off", "balanced", "high"])
        self._reduced_motion = QCheckBox("Reduced motion")
        aform.addRow("Theme mode:", self._mode_combo)
        aform.addRow("Accent:", self._accent_combo)
        aform.addRow("Visual effects:", self._effects_combo)
        aform.addRow("", self._reduced_motion)
        layout.addWidget(appearance)
        self._config.repo_root = self._repo_root_edit.text().strip() or None
        self._config.cli_path_override = self._cli_path_edit.text().strip() or None
        self._config.use_repo_venv_automatically = self._use_repo_venv.isChecked()
        if self._theme_manager:
            self._mode_combo.setCurrentText(self._theme_manager.mode())
            self._effects_combo.setCurrentText(self._theme_manager.visual_effects())
            self._reduced_motion.setChecked(self._theme_manager.reduced_motion())

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._on_save)
        test_rpc_btn = QPushButton("Test RPC")
        test_rpc_btn.clicked.connect(self._on_test_rpc)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(test_rpc_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

        diag_group = QGroupBox("Recent Diagnostics (last 20 events)")
        diag_layout = QVBoxLayout(diag_group)
        self._diag_text = QTextEdit()
        self._diag_text.setReadOnly(True)
        self._diag_text.setMaximumHeight(200)
        diag_layout.addWidget(self._diag_text)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_diagnostics)
        diag_layout.addWidget(refresh_btn)
        layout.addWidget(diag_group)
        layout.addStretch()
        self._refresh_diagnostics()
        self._refresh_cli_resolution_label()

    def _on_save(self) -> None:
        profile = self._config.get_active_profile()
        profile.rpc_url = self._rpc_url_edit.text().strip()
        try:
            profile.chain_id_expected = int(self._chain_id_edit.text().strip())
        except ValueError:
            self._status_label.setText("Invalid chain id; kept previous value.")
        cmd_text = self._start_cmd_edit.text().strip()
        profile.node.start_cmd = cmd_text.split() if cmd_text else ["animica", "node", "start"]
        if self._theme_manager:
            self._theme_manager.set_mode(self._mode_combo.currentText())
            self._theme_manager.set_accent(self._accent_combo.currentText())
            self._theme_manager.set_visual_effects(self._effects_combo.currentText())
            self._theme_manager.set_reduced_motion(self._reduced_motion.isChecked())
        save_config(self._config)
        self._status_label.setText("Settings saved.")
        self._refresh_cli_resolution_label()

    def _browse_repo_root(self) -> None:
        current = self._repo_root_edit.text().strip() or ""
        selected = QFileDialog.getExistingDirectory(self, "Select Animica Repo Root", current)
        if selected:
            self._repo_root_edit.setText(selected)
            self._refresh_cli_resolution_label()

    def _browse_cli_path(self) -> None:
        current = self._cli_path_edit.text().strip() or ""
        selected, _ = QFileDialog.getOpenFileName(self, "Select Animica CLI", current)
        if selected:
            self._cli_path_edit.setText(selected)
            self._refresh_cli_resolution_label()

    def _refresh_cli_resolution_label(self) -> None:
        self._config.repo_root = self._repo_root_edit.text().strip() or None
        self._config.cli_path_override = self._cli_path_edit.text().strip() or None
        self._config.use_repo_venv_automatically = self._use_repo_venv.isChecked()
        resolved = resolve_animica_cli(self._config)
        if resolved.argv_prefix:
            self._resolved_cli_label.setText(" ".join(resolved.argv_prefix))
        else:
            self._resolved_cli_label.setText(resolved.error or "CLI unresolved")

    def _on_test_rpc(self) -> None:
        if qthread_running(self._worker_thread):
            return
        url = self._rpc_url_edit.text().strip()
        self._status_label.setText(f"Testing RPC at {url} …")

        def _do_test() -> str:
            from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415

            client = RpcClient(url, connect_timeout=5.0, read_timeout=10.0, max_retries=2)
            try:
                result = client.discover()
                methods = result.get("methods", [])
                return f"RPC OK — {len(methods)} methods discovered"
            finally:
                client.close()

        self._worker_thread = WorkerThread(_do_test)
        self._worker_thread.worker.result.connect(lambda msg: self._status_label.setText(str(msg)))
        self._worker_thread.worker.error.connect(
            lambda msg, _tb: self._status_label.setText(f"RPC error: {str(msg)}")
        )
        self._worker_thread.worker.finished.connect(self._on_worker_finished)
        self._worker_thread.destroyed.connect(lambda *_: setattr(self, "_worker_thread", None))
        self._worker_thread.start()


    def _on_worker_finished(self) -> None:
        self._worker_thread = None

    def closeEvent(self, event) -> None:  # type: ignore[override]
        thread = self._worker_thread
        self._worker_thread = None
        stop_thread(thread)
        super().closeEvent(event)

    def _refresh_diagnostics(self) -> None:
        import datetime  # noqa: PLC0415

        events = diagnostics.get_events(last_n=20)
        if not events:
            self._diag_text.setPlainText("(no events)")
            return
        lines = [
            f"[{datetime.datetime.fromtimestamp(ev.ts).strftime('%H:%M:%S')}] {ev.level:5s} {ev.source}: {str(ev.message)}"
            for ev in reversed(events)
        ]
        self._diag_text.setPlainText("\n".join(lines))
