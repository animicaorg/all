"""DA page — blob put/get/proof with progress, namespace support, and Disk Contribution."""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QMessageBox,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.da_client import DaUploadError
from animica_studio.services.da_engine import DaContributionEngine, DaEngineConfig, DaEngineState
from animica_studio.services.da_status_service import DaStatusService
from animica_studio.services.node_path_mapper import NodePathMapper
from animica_studio.services.da_service import DaService
from animica_studio.services.error_format import format_rpc_error, safe_json_dumps
from animica_studio.services.rpc_client import RpcClient
from animica_studio.services.workers import WorkerThread
from animica_studio.util.qt import ui_thread_only
from animica_studio.storage.config import Config, save_config
from animica_studio.ui.widgets.stream_console import StreamConsole
from animica_studio.util.cancel import CancelToken
from animica_studio.util.paths import default_da_contrib_dir

log = logging.getLogger(__name__)


class DaPage(QWidget):
    """Data Availability: put, get, proof, and disk contribution."""

    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415
        self._config = config or load_config()
        self._service = DaService(self._config)
        self._da_status = DaStatusService(self._config)
        profile = self._config.get_active_profile()
        contrib_cfg = self._config.da_contribution
        self._da_engine = DaContributionEngine(
            DaEngineConfig(
                enabled=bool(contrib_cfg.get("enabled", False)),
                host_data_dir=str(contrib_cfg.get("studio_contrib_dir") or contrib_cfg.get("host_data_dir") or contrib_cfg.get("data_dir") or contrib_cfg.get("directory") or str(default_da_contrib_dir())),
                node_data_dir=str(contrib_cfg.get("node_da_dir") or contrib_cfg.get("node_data_dir") or "/data/da"),
                mode=str(contrib_cfg.get("mode") or contrib_cfg.get("reserve_mode") or "quota"),
                limit_bytes=int(contrib_cfg.get("limit_bytes") or int(contrib_cfg.get("max_gb", 50)) * 1024**3),
                rpc_url=str(contrib_cfg.get("rpc_url") or profile.node.rpc_local_url),
                contributor_id=str(contrib_cfg.get("contributor_id") or ""),
                auto_start=bool(contrib_cfg.get("auto_start", True)),
            )
        )
        self._cancel_token = CancelToken()
        self._active_workers: list[WorkerThread] = []
        self._recent_worker_errors: list[str] = []
        self._enable_toggle_touched = False
        self._mount_error = ""
        self._docker_mount_snippet = ""
        self._allowed_base_dirs: list[str] = []
        self._default_node_dir = ""
        self._last_logged_default_dir = ""
        self._settings_dirty = False
        self._last_mapping_probe_at = 0.0
        self._mapping_probe_interval_seconds = 600.0
        self._upload_capability_reason = ""
        self._status_poll_timer = QTimer(self)
        self._status_poll_timer.setInterval(10000)
        self._status_poll_timer.timeout.connect(self._poll_da_status)
        self._build_ui()
        self._load_contribution_settings()
        self._da_engine.stateChanged.connect(self._on_engine_state)
        self._da_engine.healthChanged.connect(self._on_engine_health)
        self._da_engine.metricsUpdated.connect(self._on_engine_metrics)
        self._da_engine.logLine.connect(self._on_engine_log)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("🗄️  DA (Data Availability)")
        title.setObjectName("placeholderLabel")
        layout.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._build_put_tab(), "Put Blob")
        tabs.addTab(self._build_get_tab(), "Get Blob")
        tabs.addTab(self._build_proof_tab(), "Get Proof")
        tabs.addTab(self._build_contribution_tab(), "💾 Disk Contribution")
        layout.addWidget(tabs, stretch=1)

    # ------------------------------------------------------------------
    # Put tab
    # ------------------------------------------------------------------

    def _build_put_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        form = QFormLayout()
        self._put_namespace = QLineEdit()
        self._put_namespace.setPlaceholderText("Optional namespace")
        form.addRow("Namespace:", self._put_namespace)
        self._put_file_path = QLineEdit()
        self._put_file_path.setPlaceholderText("File path or enter text below")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_file)
        file_row = QHBoxLayout()
        file_row.addWidget(self._put_file_path)
        file_row.addWidget(browse_btn)
        form.addRow("File:", file_row)
        layout.addLayout(form)

        self._put_text = QTextEdit()
        self._put_text.setPlaceholderText("Or paste raw text/hex to upload as blob…")
        self._put_text.setMaximumHeight(120)
        layout.addWidget(self._put_text)

        self._put_progress = QProgressBar()
        self._put_progress.setVisible(False)
        layout.addWidget(self._put_progress)

        btn_row = QHBoxLayout()
        put_btn = QPushButton("⬆️  Upload Blob")
        put_btn.clicked.connect(self._on_put)
        self._put_cancel_btn = QPushButton("⏹  Cancel")
        self._put_cancel_btn.setEnabled(False)
        self._put_cancel_btn.clicked.connect(lambda: self._cancel_token.cancel())
        btn_row.addWidget(put_btn)
        btn_row.addWidget(self._put_cancel_btn)
        layout.addLayout(btn_row)

        self._put_output = QTextEdit()
        self._put_output.setReadOnly(True)
        layout.addWidget(self._put_output, stretch=1)
        return w

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select file to upload")
        if path:
            self._put_file_path.setText(path)

    def _on_put(self) -> None:
        import os  # noqa: PLC0415
        namespace = self._put_namespace.text().strip() or None
        file_path = self._put_file_path.text().strip()
        text = self._put_text.toPlainText().strip()

        if file_path and os.path.isfile(file_path):
            with open(file_path, "rb") as f:
                data = f.read()
        elif text:
            data = text.encode()
        else:
            self._put_output.setPlainText("Provide a file or text to upload.")
            return

        self._cancel_token = CancelToken()
        token = self._cancel_token
        self._put_progress.setVisible(True)
        self._put_progress.setValue(0)
        self._put_output.setPlainText("Uploading…")
        self._put_cancel_btn.setEnabled(True)
        service = self._service
        total_size = len(data)

        def _progress(done, total):
            pct = int(done * 100 / max(total, 1))
            self._put_progress.setValue(pct)

        def _task():
            return service.put_blob(data, namespace=namespace, cancel_token=token, progress_cb=_progress)

        def _done(result):
            self._put_progress.setVisible(False)
            self._put_cancel_btn.setEnabled(False)
            if result.get("ok"):
                self._put_output.setPlainText(f"✅ Uploaded!\n{safe_json_dumps(result, indent=2)}")
            else:
                self._put_output.setPlainText(f"Error: {format_rpc_error(result.get('error'))}")

        def _err(msg, _tb):
            self._put_progress.setVisible(False)
            self._put_cancel_btn.setEnabled(False)
            self._put_output.setPlainText(f"Error: {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()

    # ------------------------------------------------------------------
    # Get tab
    # ------------------------------------------------------------------

    def _build_get_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        form = QFormLayout()
        self._get_commitment = QLineEdit()
        self._get_commitment.setPlaceholderText("Commitment hash (0x…)")
        form.addRow("Commitment:", self._get_commitment)
        layout.addLayout(form)
        get_btn = QPushButton("⬇️  Download Blob")
        get_btn.clicked.connect(self._on_get)
        layout.addWidget(get_btn)
        self._get_output = QTextEdit()
        self._get_output.setReadOnly(True)
        layout.addWidget(self._get_output, stretch=1)
        return w

    def _on_get(self) -> None:
        commitment = self._get_commitment.text().strip()
        if not commitment:
            self._get_output.setPlainText("Enter a commitment hash.")
            return
        self._get_output.setPlainText("Downloading…")
        service = self._service

        def _task():
            return service.get_blob(commitment)

        def _done(result):
            if result.get("ok"):
                raw = result.get("data")
                if isinstance(raw, bytes):
                    try:
                        text = raw.decode("utf-8")
                    except Exception:  # noqa: BLE001
                        text = "0x" + raw.hex()
                    self._get_output.setPlainText(f"✅ {len(raw)} bytes:\n{text[:2000]}")
                else:
                    self._get_output.setPlainText(f"✅\n{safe_json_dumps(raw, indent=2)}")
            else:
                self._get_output.setPlainText(f"Error: {format_rpc_error(result.get('error'))}")

        def _err(msg, _tb):
            self._get_output.setPlainText(f"Error: {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()

    # ------------------------------------------------------------------
    # Proof tab
    # ------------------------------------------------------------------

    def _build_proof_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        form = QFormLayout()
        self._proof_commitment = QLineEdit()
        self._proof_commitment.setPlaceholderText("Commitment hash (0x…)")
        form.addRow("Commitment:", self._proof_commitment)
        layout.addLayout(form)
        proof_btn = QPushButton("🔍  Get Proof")
        proof_btn.clicked.connect(self._on_proof)
        layout.addWidget(proof_btn)
        self._proof_output = QTextEdit()
        self._proof_output.setReadOnly(True)
        layout.addWidget(self._proof_output, stretch=1)
        return w

    def _on_proof(self) -> None:
        commitment = self._proof_commitment.text().strip()
        if not commitment:
            self._proof_output.setPlainText("Enter a commitment hash.")
            return
        self._proof_output.setPlainText("Fetching proof…")
        service = self._service

        def _task():
            return service.get_proof(commitment)

        def _done(result):
            if result.get("ok"):
                self._proof_output.setPlainText(f"✅\n{safe_json_dumps(result.get('proof'), indent=2)}")
            else:
                self._proof_output.setPlainText(f"Error: {format_rpc_error(result.get('error'))}")

        def _err(msg, _tb):
            self._proof_output.setPlainText(f"Error: {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()

    # ------------------------------------------------------------------
    # Disk Contribution tab
    # ------------------------------------------------------------------

    def _build_contribution_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Settings card ────────────────────────────────────────────
        settings_box = QGroupBox("Contribution Settings")
        form = QFormLayout(settings_box)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self._contrib_enable_cb = QCheckBox("Enable DA contribution")
        self._contrib_enable_cb.toggled.connect(lambda _checked: setattr(self, "_enable_toggle_touched", True))
        form.addRow("", self._contrib_enable_cb)

        dir_row = QHBoxLayout()
        self._contrib_host_dir_edit = QLineEdit()
        self._contrib_host_dir_edit.setPlaceholderText(str(default_da_contrib_dir()))
        dir_row.addWidget(self._contrib_host_dir_edit, stretch=1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_contrib_browse_dir)
        dir_row.addWidget(browse_btn)
        open_btn = QPushButton("Open Folder")
        open_btn.clicked.connect(self._on_contrib_open_folder)
        dir_row.addWidget(open_btn)
        form.addRow("Studio dir:", dir_row)

        self._contrib_node_dir_edit = QLineEdit()
        self._contrib_node_dir_edit.setPlaceholderText("Node DA dir from da.getDefaultDir")
        self._contrib_node_dir_edit.setReadOnly(True)
        form.addRow("Node DA dir:", self._contrib_node_dir_edit)

        self._contrib_node_dir_advanced_cb = QCheckBox("Advanced: edit node DA dir")
        self._contrib_node_dir_advanced_cb.toggled.connect(lambda checked: self._contrib_node_dir_edit.setReadOnly(not checked))
        form.addRow("", self._contrib_node_dir_advanced_cb)

        self._contrib_local_ingest_host_dir = QLineEdit()
        self._contrib_local_ingest_host_dir.setReadOnly(True)
        self._contrib_local_ingest_host_dir.setPlaceholderText("Only used for local ingest")
        form.addRow("Host ingest dir (mapped):", self._contrib_local_ingest_host_dir)

        self._contrib_max_gb_spin = QSpinBox(); self._contrib_max_gb_spin.setRange(1, 20000); self._contrib_max_gb_spin.setValue(50); self._contrib_max_gb_spin.setSuffix(" GiB")
        form.addRow("Limit:", self._contrib_max_gb_spin)

        self._contrib_reserve_combo = QComboBox()
        self._contrib_reserve_combo.addItem("quota  — enforce cap by evicting old chunks", "quota")
        self._contrib_reserve_combo.addItem(
            "preallocate  — reserve space with a sparse file", "preallocate"
        )
        form.addRow("Reserve mode:", self._contrib_reserve_combo)

        self._contrib_rpc_url = QLineEdit()
        form.addRow("RPC URL:", self._contrib_rpc_url)
        self._contrib_autostart_cb = QCheckBox("Auto-start on launch")
        form.addRow("", self._contrib_autostart_cb)

        btn_row = QHBoxLayout()
        self._contrib_apply_btn = QPushButton("✅  Apply / Save")
        self._contrib_apply_btn.clicked.connect(self._on_contrib_apply)
        self._contrib_start_btn = QPushButton("▶  Start")
        self._contrib_start_btn.clicked.connect(self._on_contrib_start)
        self._contrib_stop_btn = QPushButton("⏹  Stop")
        self._contrib_stop_btn.clicked.connect(self._on_contrib_stop)
        self._contrib_refresh_btn = QPushButton("🔄  Refresh")
        self._contrib_refresh_btn.clicked.connect(lambda: self._on_engine_metrics(self._da_engine.metrics))
        btn_row.addWidget(self._contrib_apply_btn)
        btn_row.addWidget(self._contrib_start_btn)
        btn_row.addWidget(self._contrib_stop_btn)
        btn_row.addWidget(self._contrib_refresh_btn)
        self._contrib_recommend_btn = QPushButton("Use node default dir")
        self._contrib_recommend_btn.clicked.connect(self._on_use_recommended_paths)
        btn_row.addWidget(self._contrib_recommend_btn)
        self._contrib_retest_mapping_btn = QPushButton("Re-test mount mapping")
        self._contrib_retest_mapping_btn.clicked.connect(lambda: self._refresh_da_recommendations(force_probe=True))
        btn_row.addWidget(self._contrib_retest_mapping_btn)
        self._contrib_copy_mount_btn = QPushButton("Copy docker mount snippet")
        self._contrib_copy_mount_btn.clicked.connect(self._copy_docker_mount_snippet)
        btn_row.addWidget(self._contrib_copy_mount_btn)
        self._contrib_open_docs_btn = QPushButton("Open docs")
        self._contrib_open_docs_btn.clicked.connect(self._open_mount_docs)
        btn_row.addWidget(self._contrib_open_docs_btn)
        self._contrib_fix_retry_btn = QPushButton("Fix & Retry Start")
        self._contrib_fix_retry_btn.clicked.connect(self._on_fix_and_retry_start)
        btn_row.addWidget(self._contrib_fix_retry_btn)
        self._contrib_diag_btn = QPushButton("Copy diagnostics")
        self._contrib_diag_btn.clicked.connect(self._copy_contrib_diagnostics)
        btn_row.addWidget(self._contrib_diag_btn)
        form.addRow("", btn_row)

        root.addWidget(settings_box)

        # ── Status card ──────────────────────────────────────────────
        status_box = QGroupBox("Status")
        status_form = QFormLayout(status_box)

        self._contrib_health_label = QLabel("—")
        status_form.addRow("Engine state:", self._contrib_health_label)

        self._contrib_da_status_label = QLabel("—")
        status_form.addRow("DA status:", self._contrib_da_status_label)

        self._contrib_upload_capability_label = QLabel("—")
        self._contrib_upload_capability_label.setWordWrap(True)
        status_form.addRow("Upload capability:", self._contrib_upload_capability_label)

        self._contrib_usage_bar = QProgressBar()
        self._contrib_usage_bar.setRange(0, 100)
        self._contrib_usage_bar.setValue(0)
        self._contrib_usage_bar.setFormat("%v%  used")
        status_form.addRow("Used / Limit:", self._contrib_usage_bar)

        self._contrib_used_label = QLabel("—")
        status_form.addRow("Used:", self._contrib_used_label)

        self._contrib_free_label = QLabel("—")
        status_form.addRow("Remaining:", self._contrib_free_label)

        self._contrib_disk_label = QLabel("—")
        status_form.addRow("Disk used:", self._contrib_disk_label)

        self._contrib_chunks_label = QLabel("—")
        status_form.addRow("Stored chunks:", self._contrib_chunks_label)

        self._contrib_served_label = QLabel("—")
        status_form.addRow("Served bytes:", self._contrib_served_label)

        self._contrib_error_label = QLabel("")
        self._contrib_error_label.setWordWrap(True)
        self._contrib_error_label.setStyleSheet("color: #e05050;")
        status_form.addRow("Last error:", self._contrib_error_label)

        self._test_blob_id = QLineEdit(); self._test_blob_id.setReadOnly(True)
        status_form.addRow("Last blob ID:", self._test_blob_id)

        root.addWidget(status_box)

        # ── Log panel ────────────────────────────────────────────────
        log_box = QGroupBox("Logs")
        log_layout = QVBoxLayout(log_box)
        self._contrib_console = StreamConsole()
        log_layout.addWidget(self._contrib_console)
        root.addWidget(log_box, stretch=1)

        # Wire log callback
        test_box = QGroupBox("Test DA Upload")
        test_form = QFormLayout(test_box)
        self._test_namespace = QSpinBox()
        self._test_namespace.setRange(0, 2_147_483_647)
        self._test_namespace.setToolTip("DA namespace for blob (integer).")
        test_form.addRow("Namespace:", self._test_namespace)
        upload_test_btn = QPushButton("Upload test blob")
        upload_test_btn.clicked.connect(self._upload_test_blob)
        verify_test_btn = QPushButton("Verify retrieval")
        verify_test_btn.clicked.connect(self._verify_test_blob)
        rpc_diag_btn = QPushButton("Copy RPC diagnostics")
        rpc_diag_btn.clicked.connect(self._copy_rpc_diagnostics)
        copy_btn = QPushButton("Copy ID")
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._test_blob_id.text().strip()))
        row = QHBoxLayout(); row.addWidget(upload_test_btn); row.addWidget(verify_test_btn); row.addWidget(rpc_diag_btn); row.addWidget(copy_btn)
        self._test_result = QLabel("—")
        self._test_diag = QTextEdit()
        self._test_diag.setReadOnly(True)
        self._test_diag.setPlaceholderText("Diagnostics will appear here on upload errors.")
        self._test_copy_diag_btn = QPushButton("Copy diagnostics")
        self._test_copy_diag_btn.setEnabled(False)
        self._test_copy_diag_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._test_diag.toPlainText()))
        test_form.addRow("", row)
        test_form.addRow("Result:", self._test_result)
        test_form.addRow("Diagnostics:", self._test_diag)
        test_form.addRow("", self._test_copy_diag_btn)
        root.addWidget(test_box)

        return w

    # ── Contribution helpers ─────────────────────────────────────────

    def _load_contribution_settings(self) -> None:
        """Populate contribution controls from saved config."""
        cfg = self._config.da_contribution
        self._contrib_enable_cb.setChecked(bool(cfg.get("enabled", False)))
        saved_dir = cfg.get("studio_contrib_dir") or cfg.get("host_data_dir") or cfg.get("data_dir") or cfg.get("directory", "") or ""
        self._contrib_host_dir_edit.setText(saved_dir)
        self._contrib_node_dir_edit.setText(str(cfg.get("node_da_dir") or cfg.get("node_data_dir") or ""))
        self._contrib_max_gb_spin.setValue(int((int(cfg.get("limit_bytes") or int(cfg.get("max_gb", 50)) * 1024**3) / 1024**3)))
        mode = cfg.get("mode") or cfg.get("reserve_mode", "quota")
        idx = self._contrib_reserve_combo.findData(mode)
        if idx >= 0:
            self._contrib_reserve_combo.setCurrentIndex(idx)
        self._contrib_autostart_cb.setChecked(bool(cfg.get("auto_start", True)))

        self._contrib_rpc_url.setText(str(cfg.get("rpc_url") or self._config.get_active_profile().node.rpc_local_url))
        saved_ns = self._config.da_defaults.get("test_namespace", self._config.da_defaults.get("default_namespace", 0))
        try:
            self._test_namespace.setValue(max(int(saved_ns), 0))
        except Exception:
            self._test_namespace.setValue(0)

        # Migrate legacy enabled=false + auto_start=true configs and autostart once.
        if self._da_engine.ensure_enabled_if_autostart():
            self._contrib_enable_cb.setChecked(True)
            self._persist_da_settings()
            self._contrib_console.append_info("DA auto-enabled due to auto_start")
        QTimer.singleShot(0, self._da_engine.autostart_if_configured)

        self._enable_toggle_touched = False
        self._on_engine_state(self._da_engine.state.value)
        self._on_engine_metrics(self._da_engine.metrics)
        self._refresh_da_recommendations(force_probe=False)
        self._status_poll_timer.start()

        for widget, signal_name in (
            (self._contrib_enable_cb, "toggled"),
            (self._contrib_host_dir_edit, "textChanged"),
            (self._contrib_node_dir_edit, "textChanged"),
            (self._contrib_max_gb_spin, "valueChanged"),
            (self._contrib_reserve_combo, "currentIndexChanged"),
            (self._contrib_rpc_url, "textChanged"),
            (self._contrib_autostart_cb, "toggled"),
        ):
            getattr(widget, signal_name).connect(lambda *_a: setattr(self, "_settings_dirty", True))

    def _poll_da_status(self) -> None:
        self._refresh_da_recommendations(force_probe=False)

    def _on_contrib_browse_dir(self) -> None:
        try:
            path = QFileDialog.getExistingDirectory(
                self, "Select Contribution Directory", self._contrib_host_dir_edit.text() or str(os.path.expanduser("~"))
            )
            if path:
                self._contrib_host_dir_edit.setText(path)
        except Exception as exc:  # noqa: BLE001
            log.exception("Browse dir failed: %s", exc)

    def _on_contrib_open_folder(self) -> None:
        try:
            from PySide6.QtCore import QUrl  # noqa: PLC0415
            from PySide6.QtGui import QDesktopServices  # noqa: PLC0415
            path = self._contrib_host_dir_edit.text().strip() or str(default_da_contrib_dir())
            if not os.path.isdir(path):
                self._contrib_error_label.setText(f"Directory does not exist: {path}")
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as exc:  # noqa: BLE001
            log.exception("Open folder failed: %s", exc)


    def _prompt_directory_permission_fix(self, directory: str) -> None:
        command = f"sudo chmod -R a+rwx '{directory}'"
        text = (
            "The configured DA directory is not writable.\n\n"
            "To continue using this directory, update permissions with sudo:\n"
            f"{command}"
        )
        dlg = QMessageBox(self)
        dlg.setIcon(QMessageBox.Warning)
        dlg.setWindowTitle("Directory permissions required")
        dlg.setText(text)
        copy_btn = dlg.addButton("Copy command", QMessageBox.ActionRole)
        dlg.addButton(QMessageBox.Ok)
        dlg.exec()
        if dlg.clickedButton() is copy_btn:
            QGuiApplication.clipboard().setText(command)
            self._contrib_console.append_info("Copied permission command to clipboard.")

    def _persist_da_settings(self) -> None:
        self._config.da_defaults["test_namespace"] = int(self._test_namespace.value())
        self._config.da_contribution.update(
            {
                "enabled": self._da_engine.config.enabled,
                "studio_contrib_dir": self._da_engine.config.host_data_dir,
                "node_da_dir": self._da_engine.config.node_data_dir,
                "host_data_dir": self._da_engine.config.host_data_dir,
                "node_data_dir": self._da_engine.config.node_data_dir,
                "data_dir": self._da_engine.config.host_data_dir,
                "mode": self._da_engine.config.mode,
                "limit_bytes": self._da_engine.config.limit_bytes,
                "rpc_url": self._da_engine.config.rpc_url,
                "auto_start": self._da_engine.config.auto_start,
            }
        )
        save_config(self._config)


    def _recommended_host_dir(self) -> str:
        active_id = getattr(self._config, "active_profile_id", None)
        chain_id = 1
        node_datadir = None
        for raw in list(getattr(self._config, "rpc_profiles", []) or []):
            if raw.get("id") == active_id:
                chain_id = int(raw.get("chain_id_expected") or 1)
                node_datadir = raw.get("node_datadir")
                break
        if node_datadir:
            expanded = str(Path(str(node_datadir)).expanduser())
            # Never mirror container-only /data paths onto the Studio host path.
            if not (expanded == "/data" or expanded.startswith("/data/")):
                return os.path.join(expanded, "da")
        return os.path.expanduser(f"~/.animica/chain-{chain_id}/da")

    def _host_chain_dir(self) -> str:
        active_id = getattr(self._config, "active_profile_id", None)
        for raw in list(getattr(self._config, "rpc_profiles", []) or []):
            if raw.get("id") == active_id:
                node_datadir = str(raw.get("node_datadir") or "").strip()
                if node_datadir:
                    return node_datadir
                chain_id = int(raw.get("chain_id_expected") or 1)
                return os.path.expanduser(f"~/.animica/chain-{chain_id}")
        return os.path.expanduser("~/.animica/chain-1")

    def _refresh_da_recommendations(self, *, force_probe: bool = False) -> None:
        try:
            status = self._da_status.get_status(self._contrib_rpc_url.text().strip() or None)
            self._allowed_base_dirs = [str(v) for v in list(status.get("allowed_base_dirs") or []) if str(v).strip()]
            self._default_node_dir = str(status.get("default_dir") or "").strip()
            effective_dir = str(status.get("effective_dir") or status.get("configured_dir") or "").strip()
            node_data_root = NodePathMapper.infer_node_data_root(self._default_node_dir, effective_dir)

            if self._default_node_dir:
                recommendation = self._default_node_dir
            elif self._allowed_base_dirs:
                recommendation = f"{self._allowed_base_dirs[0].rstrip('/')}/da"
            else:
                recommendation = "/data/da"
                self._contrib_console.append_warn("Could not discover allowed base dirs from node; using /data/da fallback.")
            self._contrib_node_dir_edit.setPlaceholderText(recommendation)

            if not self._contrib_node_dir_edit.text().strip():
                self._contrib_node_dir_edit.setText(recommendation)

            local_ingest_enabled = bool((status.get("raw") or {}).get("local_ingest_enabled", True))
            allow_remote_put = bool(status.get("allow_remote_put", True))
            show_local = (not allow_remote_put) and local_ingest_enabled
            self._contrib_local_ingest_host_dir.setVisible(show_local)

            da_health_ok = bool(status.get("enabled", False)) and bool(status.get("writable", False))
            if da_health_ok:
                self._contrib_da_status_label.setStyleSheet("color: #2fbf71;")
                self._contrib_da_status_label.setText("Healthy")
            else:
                self._contrib_da_status_label.setStyleSheet("color: #e05050;")
                self._contrib_da_status_label.setText("Unhealthy")

            if bool(status.get("ok", False)):
                self._contrib_upload_capability_label.setStyleSheet("color: #2fbf71;")
                self._contrib_upload_capability_label.setText("remote_put_available")
            elif allow_remote_put:
                self._contrib_upload_capability_label.setStyleSheet("color: #2fbf71;")
                self._contrib_upload_capability_label.setText("remote_put_available")
            elif not show_local:
                self._contrib_upload_capability_label.setStyleSheet("color: #e05050;")
                self._contrib_upload_capability_label.setText("blocked: local ingest unavailable")
            else:
                self._contrib_upload_capability_label.setStyleSheet("color: #e05050;")
                self._contrib_upload_capability_label.setText("checking local ingest mapping…")

            mapper = NodePathMapper(self._host_chain_dir())
            host_data_root = mapper.host_data_root()
            node_ingest_dir = str((status.get("raw") or {}).get("ingest_dir") or f"{node_data_root.rstrip('/')}/da_ingest")
            host_ingest_dir = mapper.map_host_path(node_ingest_dir, node_data_root)
            self._contrib_local_ingest_host_dir.setText(host_ingest_dir)
            should_probe = force_probe or ((time.time() - self._last_mapping_probe_at) >= self._mapping_probe_interval_seconds)
            if not show_local:
                self._mount_error = ""
                self._docker_mount_snippet = ""
                self._upload_capability_reason = ""
            if show_local and host_ingest_dir and should_probe:
                self._last_mapping_probe_at = time.time()
                pending_host = str(Path(host_ingest_dir) / "pending")
                pending_node = str(Path(node_ingest_dir) / "pending")
                with RpcClient(status.get("rpc_url") or self._contrib_rpc_url.text().strip()) as client:
                    ok, detail = mapper.probe_visibility(client, pending_node, pending_host)
                if not ok:
                    self._mount_error = (
                        "Node cannot see host ~/.animica as /data. Fix Docker volume mounts.\n"
                        f"host: {host_data_root}\n"
                        f"node: {node_data_root}"
                    )
                    self._docker_mount_snippet = f"volumes:\n  - {host_data_root}:{node_data_root}"
                    self._contrib_error_label.setText(self._mount_error)
                    self._contrib_enable_cb.setChecked(False)
                    self._contrib_upload_capability_label.setText("blocked: MOUNT_MAPPING_MISSING")
                    if self._upload_capability_reason != "MOUNT_MAPPING_MISSING":
                        self._contrib_console.append_error(detail)
                    self._upload_capability_reason = "MOUNT_MAPPING_MISSING"
                else:
                    self._contrib_upload_capability_label.setStyleSheet("color: #2fbf71;")
                    self._contrib_upload_capability_label.setText("local_ingest_available")
                    self._upload_capability_reason = ""
            elif show_local and self._mount_error:
                self._contrib_upload_capability_label.setStyleSheet("color: #e05050;")
                self._contrib_upload_capability_label.setText("blocked: MOUNT_MAPPING_MISSING")
                self._contrib_error_label.setText(self._mount_error)
        except Exception as exc:  # noqa: BLE001
            self._contrib_console.append_warn(f"Failed to refresh DA recommendations: {exc}")

    def _on_use_recommended_paths(self) -> None:
        if self._default_node_dir:
            self._contrib_node_dir_edit.setText(self._default_node_dir)
            node_data_root = NodePathMapper.infer_node_data_root(self._default_node_dir)
            mapper = NodePathMapper(self._host_chain_dir())
            mapped_host_dir = mapper.map_host_path(self._default_node_dir, node_data_root)
            current_host_dir = self._contrib_host_dir_edit.text().strip()
            if mapped_host_dir and (not current_host_dir or current_host_dir == "/data" or current_host_dir.startswith("/data/")):
                self._contrib_host_dir_edit.setText(mapped_host_dir)
                self._contrib_console.append_info(f"Mapped studio dir to host path: {mapped_host_dir}")
            if self._last_logged_default_dir != self._default_node_dir:
                self._contrib_console.append_info(f"Using node default dir: {self._default_node_dir}")
                self._last_logged_default_dir = self._default_node_dir
            return
        self._refresh_da_recommendations(force_probe=False)

    def _copy_docker_mount_snippet(self) -> None:
        snippet = self._docker_mount_snippet.strip()
        if not snippet:
            self._contrib_console.append_warn("Docker mount snippet unavailable until a mount probe fails.")
            return
        QGuiApplication.clipboard().setText(snippet)
        self._contrib_console.append_info("Copied docker mount snippet.")

    def _open_mount_docs(self) -> None:
        try:
            from PySide6.QtCore import QUrl  # noqa: PLC0415
            from PySide6.QtGui import QDesktopServices  # noqa: PLC0415
            QDesktopServices.openUrl(QUrl("https://docs.docker.com/compose/compose-file/05-services/#volumes"))
        except Exception as exc:  # noqa: BLE001
            self._contrib_console.append_warn(f"Failed to open docs: {exc}")

    def _on_fix_and_retry_start(self) -> None:
        self._refresh_da_recommendations(force_probe=True)
        self._on_contrib_start()


    def _on_contrib_apply(self) -> None:
        try:
            self._contrib_apply_btn.setEnabled(False)
            if self._mount_error and self._contrib_local_ingest_host_dir.isVisible():
                self._contrib_error_label.setText(self._mount_error)
                return
            directory = self._contrib_host_dir_edit.text().strip() or str(default_da_contrib_dir())
            node_directory = self._contrib_node_dir_edit.text().strip() or self._default_node_dir or self._contrib_node_dir_edit.placeholderText() or "/data/da"
            max_gb = self._contrib_max_gb_spin.value(); max_bytes = max_gb * 1024 ** 3
            reserve_mode = self._contrib_reserve_combo.currentData() or "quota"
            auto_start = self._contrib_autostart_cb.isChecked()
            enabled = self._contrib_enable_cb.isChecked()
            if not self._enable_toggle_touched and auto_start:
                enabled = True
                self._contrib_enable_cb.setChecked(True)
            engine_cfg = DaEngineConfig(enabled=enabled, host_data_dir=directory, node_data_dir=node_directory, mode=str(reserve_mode), limit_bytes=max_bytes, rpc_url=self._contrib_rpc_url.text().strip(), auto_start=auto_start, allowed_base_dirs=None if self._mount_error else self._allowed_base_dirs)
            ok, msg = self._da_engine.apply_config(engine_cfg) if self._settings_dirty else (True, "unchanged")
            if not ok:
                self._contrib_error_label.setText(msg)
                if "Host directory not writable" in msg:
                    self._prompt_directory_permission_fix(directory)
                self._contrib_apply_btn.setEnabled(True)
                return

            self._contrib_error_label.setText("")

            self._persist_da_settings()
            self._contrib_console.append_info("Settings saved.")
            self._settings_dirty = False
            self._on_engine_metrics(self._da_engine.metrics)
        except Exception as exc:  # noqa: BLE001
            log.exception("Apply contribution settings failed: %s", exc)
            self._contrib_error_label.setText(f"Error: {exc}")
        finally:
            self._contrib_apply_btn.setEnabled(True)

    def _on_contrib_start(self) -> None:
        try:
            self._contrib_start_btn.setEnabled(False)
            if self._da_engine.state in {DaEngineState.RUNNING, DaEngineState.STARTING}:
                self._contrib_console.append_info("Start ignored: DA engine already running or start in progress.")
                return
            if self._mount_error and self._contrib_local_ingest_host_dir.isVisible():
                self._contrib_error_label.setText(self._mount_error)
                return
            directory = self._contrib_host_dir_edit.text().strip() or str(default_da_contrib_dir())
            node_directory = self._contrib_node_dir_edit.text().strip() or self._default_node_dir or self._contrib_node_dir_edit.placeholderText() or "/data/da"
            max_bytes = self._contrib_max_gb_spin.value() * 1024 ** 3
            reserve_mode = self._contrib_reserve_combo.currentData() or "quota"
            engine_cfg = DaEngineConfig(
                enabled=True,
                host_data_dir=directory,
                node_data_dir=node_directory,
                mode=str(reserve_mode),
                limit_bytes=max_bytes,
                rpc_url=self._contrib_rpc_url.text().strip(),
                auto_start=self._contrib_autostart_cb.isChecked(),
                allowed_base_dirs=None if self._mount_error else self._allowed_base_dirs,
            )
            ok, msg = self._da_engine.apply_config(engine_cfg) if self._settings_dirty else (True, "unchanged")
            if not ok:
                self._contrib_error_label.setText(msg)
                return
            self._contrib_enable_cb.setChecked(True)
            self._persist_da_settings()
            self._settings_dirty = False
            self._da_engine.start()
        except Exception as exc:  # noqa: BLE001
            log.exception("Start contribution failed: %s", exc)
            self._contrib_error_label.setText(f"Error: {exc}")
        finally:
            self._contrib_start_btn.setEnabled(True)

    def _on_contrib_stop(self) -> None:
        try:
            self._contrib_stop_btn.setEnabled(False)
            self._da_engine.stop()
            self._contrib_stop_btn.setEnabled(True)
        except Exception as exc:  # noqa: BLE001
            self._contrib_stop_btn.setEnabled(True)
            log.exception("Stop contribution failed: %s", exc)


    @ui_thread_only(log)
    def _copy_contrib_diagnostics(self) -> None:
        d = self._da_engine.diagnostics()
        lines = [
            "DA diagnostics",
            f"state: {d['state']}",
            f"config: {safe_json_dumps(d['config'], indent=2)}",
            f"metrics: {safe_json_dumps(d['metrics'], indent=2)}",
            f"worker_errors: {' | '.join(self._recent_worker_errors[-5:])}",
        ]
        QGuiApplication.clipboard().setText("\n".join(lines))
        self._contrib_console.append_info("Diagnostics copied.")

    @ui_thread_only(log)
    def _on_engine_state(self, state: str) -> None:
        mapping = {
            DaEngineState.DISABLED.value: "Disabled (not configured or toggle off)",
            DaEngineState.CONFIGURED.value: "Configured",
            DaEngineState.STARTING.value: "Starting",
            DaEngineState.RUNNING.value: "Running",
            DaEngineState.STOPPING.value: "Stopping",
            DaEngineState.ERROR.value: "Error",
        }
        self._contrib_health_label.setText(mapping.get(state, state))
        if state == DaEngineState.DISABLED.value and self._contrib_autostart_cb.isChecked():
            self._contrib_error_label.setText("Disabled while auto-start is enabled. Click Start to fix now.")

    @ui_thread_only(log)
    def _on_engine_health(self, healthy: bool, detail: str) -> None:
        if healthy:
            if detail:
                self._contrib_console.append_info(detail)
            self._contrib_error_label.setText("")
            return
        if "errno 30" in detail.lower() or "/home" in detail.lower():
            self._contrib_error_label.setText(detail + "\nNode runs in a container; /home is not writable there. Click 'Use node default dir'.")
            return
        self._contrib_error_label.setText(detail)

    @ui_thread_only(log)
    def _on_engine_metrics(self, metrics) -> None:
        limit = max(int(metrics.limit_bytes), 1)
        used = int(metrics.used_bytes)
        self._contrib_usage_bar.setValue(min(int((used * 100) / limit), 100))
        self._contrib_used_label.setText(f"{used / 1024**3:.2f} GiB / {limit / 1024**3:.2f} GiB")
        if (getattr(self._da_engine.config, "mode", "quota") or "quota") == "quota":
            self._contrib_free_label.setText(f"{int(metrics.remaining_bytes) / 1024**3:.2f} GiB")
        else:
            self._contrib_free_label.setText("Unlimited")
        disk_used = int(getattr(metrics, "disk_used_bytes", 0))
        disk_total = int(getattr(metrics, "disk_total_bytes", 0))
        if disk_total > 0:
            self._contrib_disk_label.setText(f"{disk_used / 1024**3:.2f} GiB of {disk_total / 1024**3:.2f} GiB")
        else:
            self._contrib_disk_label.setText("—")
        self._contrib_chunks_label.setText(str(metrics.queued_files))
        self._contrib_served_label.setText(str(metrics.uploaded_blobs))
        if metrics.last_error:
            self._contrib_error_label.setText(metrics.last_error)

    @ui_thread_only(log)
    def _on_engine_log(self, kind: str, text: str) -> None:
        self._contrib_console.append_line(f"[{kind}] {text}")

    def _upload_test_blob(self) -> None:
        started = time.time()
        payload = {"hello": "world", "ts": started}
        namespace = int(self._test_namespace.value())
        self._test_diag.clear()
        self._test_copy_diag_btn.setEnabled(False)
        try:
            self._persist_da_settings()
            res = self._da_engine.client().upload_json(payload, namespace=namespace)
            self._test_blob_id.setText(str(res["blob_id"]))
            self._test_result.setText(f"Uploaded in {(time.time()-started)*1000:.0f} ms")
            self._contrib_console.append_info(f"Test blob uploaded: {res['blob_id']}")
        except DaUploadError as exc:
            diag = getattr(exc, "diagnostics", {}) or {}
            lines = [
                f"resolved_method: {diag.get('resolved_method', 'unknown')}",
                f"params_len: {diag.get('params_len', 0)}",
                f"params: {safe_json_dumps(diag.get('params', []))}",
                f"namespace: {diag.get('namespace', namespace)}",
                f"data_hex_length: {diag.get('data_hex_length', 0)}",
                f"server_version: {diag.get('server_version', 'unknown')}",
            ]
            self._test_diag.setPlainText("\n".join(lines))
            self._test_copy_diag_btn.setEnabled(True)
            self._test_result.setText(f"Upload failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            self._test_result.setText(f"Upload failed: {exc}")

    def _verify_test_blob(self) -> None:
        blob_id = self._test_blob_id.text().strip()
        if not blob_id:
            self._test_result.setText("No blob ID yet")
            return
        started = time.time()
        try:
            raw = self._da_engine.client().get_blob(blob_id)
            digest = hashlib.sha256(raw).hexdigest()
            self._test_result.setText(f"Verified ({len(raw)} bytes, sha256={digest[:16]}..) in {(time.time()-started)*1000:.0f} ms")
        except Exception as exc:  # noqa: BLE001
            self._test_result.setText(f"Verify failed: {exc}")

    def _copy_rpc_diagnostics(self) -> None:
        rpc_url = self._contrib_rpc_url.text().strip() or self._config.get_active_profile().node.rpc_local_url
        with RpcClient(rpc_url) as client:
            diag = client.rpc_diagnostics(prefixes=("chain", "tx", "da", "aicf"))
        text = safe_json_dumps(diag, indent=2)
        QGuiApplication.clipboard().setText(text)
        self._contrib_console.append_info("RPC diagnostics copied.")

    def closeEvent(self, event) -> None:
        self._status_poll_timer.stop()
        self._da_engine.stop()
        for wt in list(self._active_workers):
            try:
                if wt.isRunning():
                    wt.quit()
                    wt.wait(1200)
            except RuntimeError:
                pass
        super().closeEvent(event)
