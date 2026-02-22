"""DA page — blob put/get/proof with progress, namespace support, and Disk Contribution."""

from __future__ import annotations

import hashlib
import logging
import os
import time

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
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.da_engine import DaContributionEngine, DaEngineConfig, DaEngineState
from animica_studio.services.da_service import DaService
from animica_studio.services.error_format import format_rpc_error, safe_json_dumps
from animica_studio.services.workers import WorkerThread
from animica_studio.util.qt import ui_thread_only
from animica_studio.storage.config import Config, save_config
from animica_studio.ui.widgets.stream_console import StreamConsole
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)


class DaPage(QWidget):
    """Data Availability: put, get, proof, and disk contribution."""

    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415
        self._config = config or load_config()
        self._service = DaService(self._config)
        profile = self._config.get_active_profile()
        contrib_cfg = self._config.da_contribution
        self._da_engine = DaContributionEngine(
            DaEngineConfig(
                enabled=bool(contrib_cfg.get("enabled", False)),
                data_dir=str(contrib_cfg.get("data_dir") or contrib_cfg.get("directory") or os.path.expanduser("~/animica-da")),
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
        form.addRow("", self._contrib_enable_cb)

        dir_row = QHBoxLayout()
        self._contrib_dir_edit = QLineEdit()
        self._contrib_dir_edit.setPlaceholderText(os.path.expanduser("~/animica-da"))
        dir_row.addWidget(self._contrib_dir_edit, stretch=1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_contrib_browse_dir)
        dir_row.addWidget(browse_btn)
        open_btn = QPushButton("Open Folder")
        open_btn.clicked.connect(self._on_contrib_open_folder)
        dir_row.addWidget(open_btn)
        form.addRow("Directory:", dir_row)

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
        self._contrib_diag_btn = QPushButton("Copy diagnostics")
        self._contrib_diag_btn.clicked.connect(self._copy_contrib_diagnostics)
        btn_row.addWidget(self._contrib_diag_btn)
        form.addRow("", btn_row)

        root.addWidget(settings_box)

        # ── Status card ──────────────────────────────────────────────
        status_box = QGroupBox("Status")
        status_form = QFormLayout(status_box)

        self._contrib_health_label = QLabel("—")
        status_form.addRow("Health:", self._contrib_health_label)

        self._contrib_usage_bar = QProgressBar()
        self._contrib_usage_bar.setRange(0, 100)
        self._contrib_usage_bar.setValue(0)
        self._contrib_usage_bar.setFormat("%v%  used")
        status_form.addRow("Used / Limit:", self._contrib_usage_bar)

        self._contrib_used_label = QLabel("—")
        status_form.addRow("Used:", self._contrib_used_label)

        self._contrib_free_label = QLabel("—")
        status_form.addRow("Free on drive:", self._contrib_free_label)

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
        upload_test_btn = QPushButton("Upload test blob")
        upload_test_btn.clicked.connect(self._upload_test_blob)
        verify_test_btn = QPushButton("Verify retrieval")
        verify_test_btn.clicked.connect(self._verify_test_blob)
        copy_btn = QPushButton("Copy ID")
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._test_blob_id.text().strip()))
        row = QHBoxLayout(); row.addWidget(upload_test_btn); row.addWidget(verify_test_btn); row.addWidget(copy_btn)
        self._test_result = QLabel("—")
        test_form.addRow("", row)
        test_form.addRow("Result:", self._test_result)
        root.addWidget(test_box)

        return w

    # ── Contribution helpers ─────────────────────────────────────────

    def _load_contribution_settings(self) -> None:
        """Populate contribution controls from saved config."""
        cfg = self._config.da_contribution
        self._contrib_enable_cb.setChecked(bool(cfg.get("enabled", False)))
        saved_dir = cfg.get("directory", "") or ""
        self._contrib_dir_edit.setText(saved_dir)
        self._contrib_max_gb_spin.setValue(int((int(cfg.get("limit_bytes") or int(cfg.get("max_gb", 50)) * 1024**3) / 1024**3)))
        mode = cfg.get("mode") or cfg.get("reserve_mode", "quota")
        idx = self._contrib_reserve_combo.findData(mode)
        if idx >= 0:
            self._contrib_reserve_combo.setCurrentIndex(idx)
        self._contrib_autostart_cb.setChecked(bool(cfg.get("auto_start", True)))

        self._contrib_rpc_url.setText(str(cfg.get("rpc_url") or self._config.get_active_profile().node.rpc_local_url))

        # Auto-start if configured
        if cfg.get("enabled") and cfg.get("auto_start", True):
            self._da_engine.start()
        self._on_engine_state(self._da_engine.state.value)
        self._on_engine_metrics(self._da_engine.metrics)

    def _on_contrib_browse_dir(self) -> None:
        try:
            path = QFileDialog.getExistingDirectory(
                self, "Select Contribution Directory", self._contrib_dir_edit.text() or str(os.path.expanduser("~"))
            )
            if path:
                self._contrib_dir_edit.setText(path)
        except Exception as exc:  # noqa: BLE001
            log.exception("Browse dir failed: %s", exc)

    def _on_contrib_open_folder(self) -> None:
        try:
            import subprocess  # noqa: PLC0415
            import sys as _sys  # noqa: PLC0415
            path = self._contrib_dir_edit.text().strip() or os.path.expanduser("~/animica-da")
            if not os.path.isdir(path):
                self._contrib_error_label.setText(f"Directory does not exist: {path}")
                return
            if os.name == "nt":
                # Windows: os.startfile is only available on Windows
                getattr(os, "startfile")(path)
            elif _sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:  # noqa: BLE001
            log.exception("Open folder failed: %s", exc)

    def _on_contrib_apply(self) -> None:
        try:
            self._contrib_apply_btn.setEnabled(False)
            directory = self._contrib_dir_edit.text().strip() or default_da_dir()
            max_gb = self._contrib_max_gb_spin.value(); max_bytes = max_gb * 1024 ** 3
            reserve_mode = self._contrib_reserve_combo.currentData() or "quota"
            enabled = self._contrib_enable_cb.isChecked()
            auto_start = self._contrib_autostart_cb.isChecked()
            engine_cfg = DaEngineConfig(enabled=enabled, data_dir=directory, mode=str(reserve_mode), limit_bytes=max_bytes, rpc_url=self._contrib_rpc_url.text().strip(), auto_start=auto_start)
            ok, msg = self._da_engine.apply_config(engine_cfg)
            if not ok:
                self._contrib_error_label.setText(msg)
                self._contrib_apply_btn.setEnabled(True)
                return

            self._contrib_error_label.setText("")

            # Persist to config
            self._config.da_contribution.update(
                {
                    "enabled": enabled,
                    "data_dir": directory,
                    "mode": reserve_mode,
                    "limit_bytes": max_bytes,
                    "rpc_url": self._contrib_rpc_url.text().strip(),
                    "auto_start": auto_start,
                }
            )
            save_config(self._config)
            self._contrib_console.append_info("Settings saved.")
            self._on_engine_metrics(self._da_engine.metrics)
        except Exception as exc:  # noqa: BLE001
            log.exception("Apply contribution settings failed: %s", exc)
            self._contrib_error_label.setText(f"Error: {exc}")
        finally:
            self._contrib_apply_btn.setEnabled(True)

    def _on_contrib_start(self) -> None:
        try:
            self._contrib_start_btn.setEnabled(False)
            self._da_engine.start()
            self._contrib_start_btn.setEnabled(True)
        except Exception as exc:  # noqa: BLE001
            self._contrib_start_btn.setEnabled(True)
            log.exception("Start contribution failed: %s", exc)

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
            DaEngineState.DISABLED.value: "Disabled",
            DaEngineState.CONFIGURED.value: "Configured",
            DaEngineState.STARTING.value: "Starting",
            DaEngineState.RUNNING.value: "Running",
            DaEngineState.STOPPING.value: "Stopping",
            DaEngineState.ERROR.value: "Error",
        }
        self._contrib_health_label.setText(mapping.get(state, state))

    @ui_thread_only(log)
    def _on_engine_health(self, healthy: bool, detail: str) -> None:
        if not healthy:
            self._contrib_error_label.setText(detail)

    @ui_thread_only(log)
    def _on_engine_metrics(self, metrics) -> None:
        limit = max(int(metrics.limit_bytes), 1)
        used = int(metrics.used_bytes)
        self._contrib_usage_bar.setValue(min(int((used * 100) / limit), 100))
        self._contrib_used_label.setText(f"{used / 1024**3:.2f} GiB / {limit / 1024**3:.2f} GiB")
        self._contrib_free_label.setText(f"{int(metrics.remaining_bytes) / 1024**3:.2f} GiB")
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
        try:
            res = self._da_engine.client().upload_json(payload)
            self._test_blob_id.setText(str(res["blob_id"]))
            self._test_result.setText(f"Uploaded in {(time.time()-started)*1000:.0f} ms")
            self._contrib_console.append_info(f"Test blob uploaded: {res['blob_id']}")
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

    def closeEvent(self, event) -> None:
        self._da_engine.stop()
        for wt in list(self._active_workers):
            try:
                if wt.isRunning():
                    wt.quit()
                    wt.wait(1200)
            except RuntimeError:
                pass
        super().closeEvent(event)
